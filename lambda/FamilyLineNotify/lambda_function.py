"""
FamilyLineNotify - Lambda function for LINE notification
---------------------------------------------------------
Receives a chat message payload from FamilyChatHandler (async invoke)
and pushes it to the LINE group via Messaging API.

Environment variables:
  LINE_CHANNEL_ACCESS_TOKEN - Long-lived channel access token
  LINE_GROUP_ID             - LINE group ID (Cxxx...)
                              If empty, falls back to LINE_USER_IDS
  LINE_USER_IDS             - Comma-separated user IDs for multicast
                              Used when LINE_GROUP_ID is not set
                              e.g. "Uxxx,Uyyy"
  CHILD_USER_ID             - Cognito userId of the child (default: "child")
                              Messages from this user trigger LINE notification
                              Set to empty string to notify for all users
"""

import json
import os
import logging
from urllib.request import urlopen, Request
from urllib.error import HTTPError, URLError

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# ── Environment variables ────────────────────────────────────────────────────
CHANNEL_ACCESS_TOKEN = os.environ["LINE_CHANNEL_ACCESS_TOKEN"]
LINE_GROUP_ID = os.environ.get("LINE_GROUP_ID", "")
LINE_USER_IDS = os.environ.get("LINE_USER_IDS", "")
CHILD_USER_ID = os.environ.get("CHILD_USER_ID", "child")

# ── LINE Messaging API endpoints ─────────────────────────────────────────────
PUSH_URL = "https://api.line.me/v2/bot/message/push"
MULTICAST_URL = "https://api.line.me/v2/bot/message/multicast"


# ============================================================
# Entry point
# ============================================================


def lambda_handler(event: dict, context) -> dict:
    """
    Receives a message dict from FamilyChatHandler and sends a LINE notification.

    event (passed from FamilyChatHandler via async Lambda invoke):
      {
        "userId":   "child",
        "text":     "ただいま！おやつたべていい？",
        "isStamp":  false,
        "roomId":   "family",
        "timestamp":"2024-01-01T12:00:00.000Z"
      }
    """
    logger.info("Received event: %s", json.dumps(event, ensure_ascii=False))

    user_id = event.get("userId", "unknown")
    text = event.get("text", "")
    is_stamp = bool(event.get("isStamp", False))

    # 子供からのメッセージのみ通知する
    # CHILD_USER_ID が空文字の場合は全員のメッセージを通知する
    if CHILD_USER_ID and user_id != CHILD_USER_ID:
        logger.info("Skipping notification for userId=%s (not the child)", user_id)
        return _ok()

    if not text:
        logger.warning("Empty text, skipping")
        return _ok()

    line_message = _build_message(text, is_stamp, user_id)

    if LINE_GROUP_ID:
        _push(LINE_GROUP_ID, [line_message])
    elif LINE_USER_IDS:
        user_ids = [uid.strip() for uid in LINE_USER_IDS.split(",") if uid.strip()]
        _multicast(user_ids, [line_message])
    else:
        logger.error("Neither LINE_GROUP_ID nor LINE_USER_IDS is set")
        return _error(500, "No LINE destination configured")

    return _ok()


# ============================================================
# Message builder
# ============================================================


def _build_message(text: str, is_stamp: bool, user_id: str) -> dict:
    """
    Build a LINE message object.
    Stamps are sent as large emoji via flex message.
    Plain text is wrapped with the sender label.
    """
    sender_label = _sender_label(user_id)

    if is_stamp:
        # スタンプ（絵文字）はFlexメッセージで大きく表示する
        return {
            "type": "flex",
            "altText": f"{sender_label}がスタンプを送りました: {text}",
            "contents": {
                "type": "bubble",
                "size": "micro",
                "body": {
                    "type": "box",
                    "layout": "vertical",
                    "contents": [
                        {
                            "type": "text",
                            "text": sender_label,
                            "size": "xs",
                            "color": "#888888",
                            "margin": "none",
                        },
                        {
                            "type": "text",
                            "text": text,
                            "size": "5xl",
                            "align": "center",
                            "margin": "sm",
                        },
                    ],
                },
            },
        }

    # 通常テキスト
    return {
        "type": "text",
        "text": f"{sender_label}\n{text}",
    }


def _sender_label(user_id: str) -> str:
    labels = {
        "child": "🧒 こどもから",
        "papa": "👨 パパから",
        "mama": "👩 ママから",
    }
    return labels.get(user_id, f"📨 {user_id}から")


# ============================================================
# LINE Messaging API calls
# ============================================================


def _push(to: str, messages: list) -> None:
    """Push messages to a single user or group."""
    payload = {"to": to, "messages": messages}
    _call_line_api(PUSH_URL, payload)
    logger.info("Pushed to %s", to)


def _multicast(to: list, messages: list) -> None:
    """Multicast messages to multiple users (max 500)."""
    payload = {"to": to, "messages": messages}
    _call_line_api(MULTICAST_URL, payload)
    logger.info("Multicast to %s", to)


def _call_line_api(url: str, payload: dict) -> None:
    """
    Call LINE Messaging API with urllib (no external dependencies).
    Raises on non-2xx responses.
    """
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {CHANNEL_ACCESS_TOKEN}",
        },
        method="POST",
    )
    try:
        with urlopen(req, timeout=10) as resp:
            resp_body = resp.read().decode("utf-8")
            logger.info("LINE API response %d: %s", resp.status, resp_body)
    except HTTPError as exc:
        resp_body = exc.read().decode("utf-8")
        logger.error("LINE API HTTP error %d: %s", exc.code, resp_body)
        raise
    except URLError as exc:
        logger.error("LINE API URL error: %s", exc.reason)
        raise


# ============================================================
# Utilities
# ============================================================


def _ok() -> dict:
    return {"statusCode": 200, "body": "OK"}


def _error(status: int, message: str) -> dict:
    return {"statusCode": status, "body": json.dumps({"error": message})}
