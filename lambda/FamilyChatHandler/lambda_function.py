"""
FamilyChatHandler - Lambda function for family chat WebSocket API
-----------------------------------------------------------------
Handles WebSocket routes:
  $connect    - Store connection info in DynamoDB
  $disconnect - Remove connection info from DynamoDB
  sendMessage - Persist message and broadcast to all connections
  $default    - Fallback (no-op)

Environment variables (set in Lambda console):
  CONNECTIONS_TABLE  - DynamoDB table name for WebSocket connections
  MESSAGES_TABLE     - DynamoDB table name for chat messages
  LINE_FUNCTION_NAME - Lambda function name for LINE notification
  ROOM_ID            - Chat room identifier (e.g. "family")
  API_GW_ENDPOINT    - API Gateway Management endpoint
                       e.g. https://xxxxxxxxxx.execute-api.ap-northeast-1.amazonaws.com/prod
"""

import json
import os
import time
import logging
from datetime import datetime, timezone

import boto3
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

# ── Logger ──────────────────────────────────────────────────────────────────
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# ── Environment variables ────────────────────────────────────────────────────
CONNECTIONS_TABLE  = os.environ["CONNECTIONS_TABLE"]   # e.g. "FamilyChatConnections"
MESSAGES_TABLE     = os.environ["MESSAGES_TABLE"]       # e.g. "FamilyChatMessages"
LINE_FUNCTION_NAME = os.environ["LINE_FUNCTION_NAME"]  # e.g. "FamilyLineNotify"
ROOM_ID            = os.environ.get("ROOM_ID", "family")
API_GW_ENDPOINT    = os.environ["API_GW_ENDPOINT"]     # e.g. "https://xxx.execute-api.ap-northeast-1.amazonaws.com/prod"

# ── AWS clients (reused across warm invocations) ─────────────────────────────
dynamodb   = boto3.resource("dynamodb")
lambda_cli = boto3.client("lambda")

conn_table = dynamodb.Table(CONNECTIONS_TABLE)
msg_table  = dynamodb.Table(MESSAGES_TABLE)

# API Gateway Management API client is created lazily (endpoint known at runtime)
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
    """Main Lambda entry point dispatched by API Gateway WebSocket routes."""
    route = event.get("requestContext", {}).get("routeKey", "$default")
    connection_id = event["requestContext"]["connectionId"]

    logger.info("Route: %s | ConnectionId: %s", route, connection_id)

    try:
        if route == "$connect":
            return handle_connect(event, connection_id)
        elif route == "$disconnect":
            return handle_disconnect(connection_id)
        elif route == "sendMessage":
            return handle_send_message(event, connection_id)
        else:
            return _ok()
    except Exception as exc:
        logger.exception("Unhandled error on route %s: %s", route, exc)
        return _error(500, "Internal server error")


# ============================================================
# Route handlers
# ============================================================

def handle_connect(event: dict, connection_id: str) -> dict:
    """
    $connect: save connection to DynamoDB.

    Query-string parameters expected:
      userId  - e.g. "child", "papa", "mama"
      token   - Cognito ID token (validated by API Gateway authorizer;
                kept here for reference / additional checks if needed)
    """
    user_id = (
        event.get("requestContext", {})
            .get("authorizer", {})
            .get("userId", "unknown")
    )

    # TTL: auto-expire connection record after 24 hours (covers accidental disconnects)
    ttl = int(time.time()) + 86400

    conn_table.put_item(
        Item={
            "connectionId": connection_id,
            "userId":       user_id,
            "roomId":       ROOM_ID,
            "connectedAt":  _now_iso(),
            "ttl":          ttl,
        }
    )
    logger.info("Connected: %s (userId=%s)", connection_id, user_id)

    # Push unread messages accumulated while the client was offline
    _push_unread_messages(connection_id, user_id)

    return _ok()


def handle_disconnect(connection_id: str) -> dict:
    """$disconnect: remove the stale connection record."""
    conn_table.delete_item(Key={"connectionId": connection_id})
    logger.info("Disconnected: %s", connection_id)
    return _ok()


def handle_send_message(event: dict, connection_id: str) -> dict:
    """
    sendMessage: persist message to DynamoDB and broadcast to all connections.

    Expected request body (JSON):
      {
        "action":    "sendMessage",
        "text":      "こんにちは！",
        "isStamp":   false          (optional)
      }
    """
    body = _parse_body(event)
    if body is None:
        return _error(400, "Invalid JSON body")

    text = (body.get("text") or "").strip()
    if not text:
        return _error(400, "Empty message text")

    # Resolve sender's userId from the connection record
    sender_id = _get_user_id(connection_id)
    created_at = _now_iso()
    message_id = f"{created_at}#{connection_id}"

    message = {
        "roomId":    ROOM_ID,
        "createdAt": created_at,
        "messageId": message_id,
        "userId":    sender_id,
        "text":      text,
        "isStamp":   bool(body.get("isStamp", False)),
    }

    # 1. Persist to DynamoDB
    msg_table.put_item(Item=message)
    logger.info("Message saved: %s from %s", message_id, sender_id)

    # 2. Trigger LINE notification asynchronously (fire-and-forget)
    _invoke_line_notify(message)

    return _ok()


# ============================================================
# Helper: push unread messages on reconnect
# ============================================================

def _push_unread_messages(connection_id: str, user_id: str) -> None:
    """
    Send the latest N messages to a freshly-connected client so the chat
    history appears even after the page was closed.
    Only the last 50 messages are fetched to keep latency low.
    """
    try:
        response = msg_table.query(
            KeyConditionExpression=Key("roomId").eq(ROOM_ID),
            ScanIndexForward=False,  # newest first
            Limit=50,
        )
        items = list(reversed(response.get("Items", [])))  # chronological order
        if not items:
            return

        payload = json.dumps({"type": "history", "messages": items}, ensure_ascii=False, default=str)
        _send_to_connection(connection_id, payload)
        logger.info("Pushed %d history messages to %s", len(items), connection_id)
    except Exception as exc:
        logger.warning("Failed to push history to %s: %s", connection_id, exc)


def _send_to_connection(connection_id: str, payload: str) -> bool:
    """
    Push a payload to a single WebSocket connection.
    Returns True on success, False when the connection is gone.
    """
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
        logger.error("post_to_connection failed for %s: %s", connection_id, exc)
        return False


# ============================================================
# Helper: invoke LINE notification Lambda
# ============================================================

def _invoke_line_notify(message: dict) -> None:
    """
    Asynchronously invoke the LINE notification Lambda.
    Uses InvocationType=Event (fire-and-forget) so it does not
    add latency to the chat response.
    """
    try:
        lambda_cli.invoke(
            FunctionName=LINE_FUNCTION_NAME,
            InvocationType="Event",  # async
            Payload=json.dumps(message, ensure_ascii=False, default=str).encode("utf-8"),
        )
        logger.info("LINE notify invoked for message from %s", message.get("userId"))
    except ClientError as exc:
        # Non-fatal: chat still works even if LINE notification fails
        logger.error("Failed to invoke LINE notify Lambda: %s", exc)


# ============================================================
# Helper: resolve userId from connection record
# ============================================================

def _get_user_id(connection_id: str) -> str:
    try:
        resp = conn_table.get_item(Key={"connectionId": connection_id})
        return resp.get("Item", {}).get("userId", "unknown")
    except ClientError as exc:
        logger.warning("Could not fetch userId for %s: %s", connection_id, exc)
        return "unknown"


# ============================================================
# Utilities
# ============================================================

def _parse_body(event: dict) -> dict | None:
    raw = event.get("body") or "{}"
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _ok() -> dict:
    return {"statusCode": 200, "body": "OK"}


def _error(status: int, message: str) -> dict:
    return {"statusCode": status, "body": json.dumps({"error": message})}