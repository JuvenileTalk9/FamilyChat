"""
FamilyLineWebhook - Lambda function for LINE Webhook receiver
-------------------------------------------------------------
Receives webhook events from LINE Messaging API and forwards
chat messages to connected WebSocket clients via API Gateway.

Handled LINE event types:
  message/text - Forward parent's text message to iPad via WebSocket
  message/sticker - Forward sticker as stamp
  follow       - Log userId of new follower (for LINE_USER_IDS setup)
  join         - Log groupId when Bot joins a group (for LINE_GROUP_ID setup)
  (others)     - Acknowledge and ignore

Environment variables:
  LINE_CHANNEL_SECRET    - For webhook signature verification (required)
  CONNECTIONS_TABLE      - DynamoDB table name for WebSocket connections
  MESSAGES_TABLE         - DynamoDB table name for chat messages
  USERS_TABLE            - DynamoDB table name for LINE userId mapping (default: FamilyChatUsers)
  API_GW_ENDPOINT        - API Gateway Management endpoint (https://...)
  ROOM_ID                - Chat room identifier (default: "family")
  CHILD_CONNECTION_USER  - userId of the child client (default: "child")
                           Used to target WebSocket push to iPad
"""

import base64
import hashlib
import hmac
import json
import logging
import os
import time
from datetime import datetime, timezone

import boto3
from boto3.dynamodb.conditions import Attr
from botocore.exceptions import ClientError

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# ── Environment variables ────────────────────────────────────────────────────
LINE_CHANNEL_SECRET    = os.environ["LINE_CHANNEL_SECRET"]
CONNECTIONS_TABLE      = os.environ["CONNECTIONS_TABLE"]
MESSAGES_TABLE         = os.environ["MESSAGES_TABLE"]
USERS_TABLE            = os.environ.get("USERS_TABLE", "FamilyChatUsers")
API_GW_ENDPOINT        = os.environ["API_GW_ENDPOINT"]   # https://xxx.amazonaws.com/prod
ROOM_ID                = os.environ.get("ROOM_ID", "family")
CHILD_CONNECTION_USER  = os.environ.get("CHILD_CONNECTION_USER", "child")

# ── AWS clients ──────────────────────────────────────────────────────────────
dynamodb    = boto3.resource("dynamodb")
conn_table  = dynamodb.Table(CONNECTIONS_TABLE)
msg_table   = dynamodb.Table(MESSAGES_TABLE)
users_table = dynamodb.Table(USERS_TABLE)

# LINEユーザーID → 内部userId のインメモリキャッシュ（ウォームスタート高速化）
_user_id_cache: dict = {}

_apigw_mgmt = None

def get_apigw_mgmt():
    global _apigw_mgmt
    if _apigw_mgmt is None:
        _apigw_mgmt = boto3.client(
            "apigatewaymanagementapi",
            endpoint_url=API_GW_ENDPOINT,
        )
    return _apigw_mgmt


# ============================================================
# Entry point
# ============================================================

def lambda_handler(event: dict, context) -> dict:
    """
    Called by API Gateway HTTP API when LINE sends a webhook POST.
    Must return 200 quickly — LINE retries if response takes >1s.
    """
    # 1. ヘッダーキーを小文字に正規化（API Gatewayの設定に依らず確実に取得するため）
    headers_raw = event.get("headers") or {}
    headers = {k.lower(): v for k, v in headers_raw.items()}
    signature = headers.get("x-line-signature", "")

    # 2. Base64エンコードされている場合はバイト列にデコードする
    #    API Gateway HTTP API は Content-Type によって isBase64Encoded=true になる場合がある
    body_raw = event.get("body") or ""
    is_base64 = event.get("isBase64Encoded", False)
    if is_base64:
        body_bytes = base64.b64decode(body_raw)
    else:
        body_bytes = body_raw.encode("utf-8")

    # 3. 署名検証（バイト列で検証する）
    if not _verify_signature(body_bytes, signature):
        logger.warning("Invalid LINE signature")
        return _resp(403, "Forbidden")

    # 4. JSONパース（署名検証後に文字列に戻す）
    try:
        payload = json.loads(body_bytes.decode("utf-8"))
    except json.JSONDecodeError:
        logger.error("Invalid JSON body")
        return _resp(400, "Bad Request")

    for line_event in payload.get("events", []):
        try:
            _handle_event(line_event)
        except Exception as exc:
            # Never let one event failure block the 200 response to LINE
            logger.exception("Error handling event %s: %s", line_event.get("type"), exc)

    return _resp(200, "OK")


# ============================================================
# LINE event dispatcher
# ============================================================

def _handle_event(line_event: dict) -> None:
    event_type = line_event.get("type")
    logger.info("LINE event type: %s", event_type)

    if event_type == "message":
        _handle_message(line_event)
    elif event_type == "follow":
        _handle_follow(line_event)
    elif event_type == "join":
        _handle_join(line_event)
    else:
        logger.info("Ignored event type: %s", event_type)


# ============================================================
# message event
# ============================================================

def _handle_message(line_event: dict) -> None:
    msg_obj  = line_event.get("message", {})
    msg_type = msg_obj.get("type")
    source   = line_event.get("source", {})
    sender_line_uid = source.get("userId", "unknown")

    if msg_type == "text":
        text     = msg_obj.get("text", "").strip()
        is_stamp = False
    elif msg_type == "sticker":
        # LINEスタンプは絵文字として代替表示
        text     = _sticker_to_emoji(msg_obj)
        is_stamp = True
    else:
        logger.info("Unsupported message type: %s", msg_type)
        return

    if not text:
        return

    sender_id  = _resolve_user_id(sender_line_uid)
    created_at  = _now_iso()
    message_id = f"{created_at}#{sender_line_uid[:8]}"

    message = {
        "roomId":    ROOM_ID,
        "createdAt": created_at,
        "messageId": message_id,
        "userId":    sender_id,
        "text":      text,
        "isStamp":   is_stamp,
    }

    # 1. DynamoDB に保存
    msg_table.put_item(Item=message)
    logger.info("Message saved from LINE: %s", message_id)

    # 2. 子供のiPad（WebSocket接続）に転送
    _push_to_child(message)


# ============================================================
# follow / join events（ユーザーID・グループID取得用）
# ============================================================

def _handle_follow(line_event: dict) -> None:
    """
    Botを友だち追加したときのイベント。
    CloudWatch Logsにユーザーの LINE userId を出力する。
    LINE_USER_IDS 環境変数に設定する値をここで確認できる。
    """
    user_id = line_event.get("source", {}).get("userId", "unknown")
    logger.info("=== NEW FOLLOWER ===")
    logger.info("LINE userId: %s", user_id)
    logger.info("-> Set this value to LINE_USER_IDS environment variable")


def _handle_join(line_event: dict) -> None:
    """
    BotがLINEグループに招待されたときのイベント。
    CloudWatch Logsにグループ ID を出力する。
    LINE_GROUP_ID 環境変数に設定する値をここで確認できる。
    """
    source   = line_event.get("source", {})
    group_id = source.get("groupId", "unknown")
    logger.info("=== BOT JOINED GROUP ===")
    logger.info("LINE groupId: %s", group_id)
    logger.info("-> Set this value to LINE_GROUP_ID environment variable")


# ============================================================
# WebSocket push to child
# ============================================================

def _push_to_child(message: dict) -> None:
    """
    DynamoDB FamilyChatConnections から子供の接続IDを取得して
    WebSocket経由でメッセージをiPadに転送する。
    """
    try:
        response = conn_table.scan(
            FilterExpression=(
                Attr("roomId").eq(ROOM_ID) &
                Attr("userId").eq(CHILD_CONNECTION_USER)
            )
        )
        connections = response.get("Items", [])
    except ClientError as exc:
        logger.error("Failed to scan connections: %s", exc)
        return

    if not connections:
        logger.info("No active WebSocket connection for userId=%s", CHILD_CONNECTION_USER)
        return

    payload = json.dumps(
        {"type": "message", **message},
        ensure_ascii=False,
        default=str,
    )

    stale_ids = []
    for conn in connections:
        cid = conn["connectionId"]
        if not _send_to_connection(cid, payload):
            stale_ids.append(cid)

    for cid in stale_ids:
        try:
            conn_table.delete_item(Key={"connectionId": cid})
        except ClientError as exc:
            logger.warning("Could not delete stale connection %s: %s", cid, exc)


def _send_to_connection(connection_id: str, payload: str) -> bool:
    try:
        get_apigw_mgmt().post_to_connection(
            ConnectionId=connection_id,
            Data=payload.encode("utf-8"),
        )
        return True
    except ClientError as exc:
        error_code = exc.response["Error"]["Code"]
        if error_code in ("GoneException", "410"):
            logger.info("Connection gone: %s", connection_id)
            return False
        logger.error("post_to_connection error for %s: %s", connection_id, exc)
        return False


# ============================================================
# Signature verification
# ============================================================

def _verify_signature(body_bytes: bytes, signature: str) -> bool:
    """
    Verify the X-Line-Signature header using HMAC-SHA256.
    https://developers.line.biz/en/docs/messaging-api/receiving-messages/#verifying-signatures

    bodyはバイト列で受け取る。
    encode() を二重にかけると署名が一致しなくなるため注意。
    """
    if not signature:
        return False
    expected = hmac.new(
        LINE_CHANNEL_SECRET.encode("utf-8"),
        body_bytes,                            # ← バイト列をそのまま渡す
        hashlib.sha256,
    ).digest()
    expected_b64 = base64.b64encode(expected).decode("utf-8")
    return hmac.compare_digest(expected_b64, signature)


# ============================================================
# Helpers
# ============================================================

def _sticker_to_emoji(msg_obj: dict) -> str:
    """
    Convert a LINE sticker to a representative emoji.
    Falls back to a generic emoji.
    """
    return "🎭"


def _resolve_user_id(line_user_id: str) -> str:
    """
    DynamoDB FamilyChatUsers テーブルを参照して
    LINE userId を内部 userId (papa/mama) にマッピングする。
    見つからない場合は line_user_id をそのまま返す。
    ウォームスタート時はインメモリキャッシュを使い DynamoDB アクセスを省略する。
    """
    if line_user_id in _user_id_cache:
        return _user_id_cache[line_user_id]

    try:
        resp = users_table.get_item(Key={"lineUserId": line_user_id})
        item = resp.get("Item")
        if item:
            user_id = item.get("userId", line_user_id)
            _user_id_cache[line_user_id] = user_id
            logger.info("Resolved lineUserId=%s -> userId=%s", line_user_id, user_id)
            return user_id
        else:
            logger.warning("lineUserId=%s not found in %s", line_user_id, USERS_TABLE)
            return line_user_id
    except ClientError as exc:
        logger.error("Failed to resolve userId for %s: %s", line_user_id, exc)
        return line_user_id


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _resp(status: int, body: str) -> dict:
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps({"message": body}),
    }
