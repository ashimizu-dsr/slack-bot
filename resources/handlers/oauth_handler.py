"""
OAuth インストールハンドラー

このモジュールは、Slack AppをワークスペースにインストールするためのOAuth処理を提供します。
マルチテナント対応のため、インストール時に bot_token を Firestore の workspaces コレクションに保存します。
"""

import logging
import os
from slack_sdk.oauth import AuthorizeUrlGenerator, OAuthStateUtils
from slack_sdk.oauth.installation_store import Installation
from slack_sdk.web import WebClient

logger = logging.getLogger(__name__)


def handle_oauth_redirect(request):
    """
    OAuth リダイレクトハンドラー
    
    Args:
        request: Google Cloud Run の HTTPリクエストオブジェクト
        
    Returns:
        タプル: (レスポンスボディ, HTTPステータスコード)
    """
    from resources.shared.db import save_workspace_config
    
    # クエリパラメータから code を取得
    code = request.args.get("code")
    state = request.args.get("state")
    
    if not code:
        logger.error("OAuth code が見つかりません")
        return "OAuth Error: Missing code parameter", 400
    
    # Client ID と Secret を環境変数から取得
    client_id = os.environ.get("SLACK_CLIENT_ID")
    client_secret = os.environ.get("SLACK_CLIENT_SECRET")
    
    if not client_id or not client_secret:
        logger.error("SLACK_CLIENT_ID または SLACK_CLIENT_SECRET が設定されていません")
        return "OAuth Error: Missing credentials", 500
    
    try:
        # OAuth トークン交換
        client = WebClient()
        response = client.oauth_v2_access(
            client_id=client_id,
            client_secret=client_secret,
            code=code
        )
        
        if not response.get("ok"):
            logger.error(f"OAuth トークン交換失敗: {response.get('error')}")
            return f"OAuth Error: {response.get('error')}", 400
        
        # インストール情報を取得
        team_id = response["team"]["id"]
        team_name = response["team"]["name"]
        bot_token = response["access_token"]
        
        # Firestore に保存
        save_workspace_config(
            team_id=team_id,
            team_name=team_name,
            bot_token=bot_token,
            report_channel_id=None  # 後でモーダルから設定可能
        )
        
        logger.info(f"OAuth インストール成功: Team={team_name} (ID={team_id})")
        
        return f"""
        <html>
        <head><title>インストール完了</title></head>
        <body>
            <h1>✅ インストール完了</h1>
            <p>ワークスペース「{team_name}」に勤怠管理Botがインストールされました。</p>
            <p>Slackアプリに戻って使用を開始してください。</p>
        </body>
        </html>
        """, 200
        
    except Exception as e:
        logger.error(f"OAuth 処理エラー: {e}", exc_info=True)
        return f"OAuth Error: {e}", 500


def get_oauth_install_url():
    """
    OAuth インストール用URLを生成します。
    
    Returns:
        インストール用URL文字列
    """
    client_id = os.environ.get("SLACK_CLIENT_ID")
    redirect_uri = os.environ.get("OAUTH_REDIRECT_URI")  # 例: https://your-app.run.app/oauth/callback
    
    if not client_id or not redirect_uri:
        logger.error("SLACK_CLIENT_ID または OAUTH_REDIRECT_URI が設定されていません")
        return None
    
    # スコープの定義（必要な権限）
    scopes = [
        "app_mentions:read",
        "channels:history",
        "channels:read",
        "chat:write",
        "commands",
        "users:read",
        "users:read.email",
        "reactions:write",
        "im:history",
        "groups:history",
    ]
    
    # ユーザースコープ（必要な場合）
    user_scopes = []
    
    # State パラメータの生成（CSRF対策）
    state = OAuthStateUtils.generate_state()
    
    # URL生成
    generator = AuthorizeUrlGenerator(
        client_id=client_id,
        scopes=scopes,
        user_scopes=user_scopes,
        redirect_uri=redirect_uri
    )
    
    url = generator.generate(state)
    logger.info(f"OAuth インストールURL生成成功: {url}")
    
    return url
