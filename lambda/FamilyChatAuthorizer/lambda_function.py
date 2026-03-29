"""
FamilyChatAuthorizer - Lambda Authorizer for WebSocket $connect
---------------------------------------------------------------
Validates the Cognito ID token passed as a query-string parameter
and returns an IAM policy that allows or denies the $connect route.

Flow:
  1. Extract `token` from the query-string
  2. Fetch Cognito JWKS and verify the JWT signature / claims
  3. Return Allow policy (with userId in context) or Deny policy

Environment variables:
  COGNITO_USER_POOL_ID  - e.g. ap-northeast-1_xxxxxxxxx
  COGNITO_APP_CLIENT_ID - Cognito app client ID
  AWS_REGION_NAME       - e.g. ap-northeast-1

Dependencies (add to Lambda layer or package):
  PyJWT[crypto]==2.8.0
  cryptography==42.0.5
  requests==2.31.0

Note: these are NOT included in the Lambda Python runtime by default.
See README for packaging instructions.
"""

import json
import os
import logging
import time
from functools import lru_cache

import requests
import jwt
from jwt.algorithms import RSAAlgorithm

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# ── Environment variables ────────────────────────────────────────────────────
REGION = os.environ["AWS_REGION_NAME"]
USER_POOL_ID = os.environ["COGNITO_USER_POOL_ID"]
APP_CLIENT_ID = os.environ["COGNITO_APP_CLIENT_ID"]

JWKS_URL = (
    f"https://cognito-idp.{REGION}.amazonaws.com"
    f"/{USER_POOL_ID}/.well-known/jwks.json"
)
ISSUER = f"https://cognito-idp.{REGION}.amazonaws.com/{USER_POOL_ID}"


# ============================================================
# Entry point
# ============================================================


def lambda_handler(event: dict, context) -> dict:
    """
    API Gateway calls this function before establishing the WebSocket.
    Must return an IAM policy document.
    """
    logger.info("Authorizer invoked: methodArn=%s", event.get("methodArn"))

    token = _extract_token(event)
    if not token:
        logger.warning("No token provided")
        return _deny(event["methodArn"])

    try:
        claims = _verify_token(token)
    except Exception as exc:
        logger.warning("Token verification failed: %s", exc)
        return _deny(event["methodArn"])

    user_id = claims.get("cognito:username") or claims.get("sub", "unknown")
    logger.info("Authorized: userId=%s", user_id)

    # Pass userId to $connect handler via requestContext.authorizer
    return _allow(event["methodArn"], principal_id=user_id, context={"userId": user_id})


# ============================================================
# Token extraction
# ============================================================


def _extract_token(event: dict) -> str | None:
    """
    Extract JWT from query-string parameter `token`.
    API Gateway WebSocket passes query params under
    event["queryStringParameters"].
    """
    qs = event.get("queryStringParameters") or {}
    return qs.get("token") or None


# ============================================================
# JWT verification
# ============================================================


@lru_cache(maxsize=1)
def _get_jwks() -> dict:
    """
    Fetch and cache Cognito JWKS.
    lru_cache keeps the result for the lifetime of the Lambda container,
    so warm invocations skip the HTTP round-trip.
    """
    resp = requests.get(JWKS_URL, timeout=5)
    resp.raise_for_status()
    jwks = resp.json()
    # Build a kid → public-key mapping for O(1) lookup
    return {key["kid"]: RSAAlgorithm.from_jwk(json.dumps(key)) for key in jwks["keys"]}


def _verify_token(token: str) -> dict:
    """
    Verify a Cognito ID token and return its claims.

    Checks performed:
      - JWT signature (RS256, Cognito public key)
      - Expiry (exp)
      - Issuer (iss) matches our User Pool
      - Audience (aud) matches our App Client ID
      - Token use is "id"
    """
    # Decode header without verification to get kid
    header = jwt.get_unverified_header(token)
    kid = header.get("kid")
    if not kid:
        raise ValueError("JWT header missing 'kid'")

    public_keys = _get_jwks()
    public_key = public_keys.get(kid)
    if not public_key:
        # Kid not found — JWKS may have rotated; clear cache and retry once
        _get_jwks.cache_clear()
        public_keys = _get_jwks()
        public_key = public_keys.get(kid)
        if not public_key:
            raise ValueError(f"Unknown kid: {kid}")

    claims = jwt.decode(
        token,
        public_key,
        algorithms=["RS256"],
        audience=APP_CLIENT_ID,
        issuer=ISSUER,
        options={"require": ["exp", "iss", "aud", "sub"]},
    )

    # Cognito-specific claim: token_use must be "id"
    if claims.get("token_use") != "id":
        raise ValueError(f"Invalid token_use: {claims.get('token_use')}")

    return claims


# ============================================================
# IAM policy builders
# ============================================================


def _allow(method_arn: str, principal_id: str, context: dict) -> dict:
    return _policy(
        effect="Allow",
        method_arn=method_arn,
        principal_id=principal_id,
        context=context,
    )


def _deny(method_arn: str) -> dict:
    return _policy(
        effect="Deny", method_arn=method_arn, principal_id="unauthorized", context={}
    )


def _policy(effect: str, method_arn: str, principal_id: str, context: dict) -> dict:
    """
    Build the IAM policy document expected by API Gateway.
    The resource ARN is wildcarded to the stage so the same policy
    covers all routes ($connect, sendMessage, etc.) within the same
    deployment — though only $connect actually requires authorization.
    """
    # Wildcard the ARN to cover the entire stage:
    # arn:aws:execute-api:region:account:api-id/stage/*
    parts = method_arn.split("/")
    stage_arn = "/".join(parts[:2]) + "/*"

    return {
        "principalId": principal_id,
        "policyDocument": {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Action": "execute-api:Invoke",
                    "Effect": effect,
                    "Resource": stage_arn,
                }
            ],
        },
        "context": context,  # available in Lambda as event["requestContext"]["authorizer"]
    }
