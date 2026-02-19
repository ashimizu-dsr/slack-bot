"""
共通認証モジュール

Cloud Run エンドポイント用の OIDC トークン検証を提供します。
Cloud Scheduler / Pub/Sub からのリクエストを認可する際に使用します。
"""

import logging

import google.auth.transport.requests
import google.oauth2.id_token

from resources.constants import APP_ENV
from resources.shared.errors import AuthorizationError

logger = logging.getLogger(__name__)

# 環境ごとの Cloud Run サービス URL（OIDC audience として使用）
_AUDIENCE_MAP = {
    "production": "https://slack-kintai-bot-prod-478434513686.asia-northeast1.run.app",
    "develop":    "https://slack-kintai-bot-dev-478434513686.asia-northeast1.run.app",
}


def verify_oidc_token(request) -> None:
    """
    リクエストの Authorization ヘッダーから OIDC トークンを取得し、
    Google の公開鍵で署名を検証します。

    Args:
        request: Flask の Request オブジェクト

    Raises:
        AuthorizationError: トークンが存在しない、または検証に失敗した場合
    """
    auth_header: str = request.headers.get("Authorization", "")

    if not auth_header or not auth_header.startswith("Bearer "):
        logger.warning("[OIDC] Authorization header missing or malformed")
        raise AuthorizationError("Missing or malformed Authorization header")

    token = auth_header[len("Bearer "):]
    audience = _AUDIENCE_MAP.get(APP_ENV, _AUDIENCE_MAP["develop"])

    try:
        transport = google.auth.transport.requests.Request()
        google.oauth2.id_token.verify_oauth2_token(token, transport, audience=audience)
        logger.info(f"[OIDC] Token verified successfully (audience={audience})")
    except Exception as e:
        logger.warning(f"[OIDC] Token verification failed: {e}")
        raise AuthorizationError(f"OIDC token verification failed: {e}")
