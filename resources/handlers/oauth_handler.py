"""
OAuth インストールハンドラー（旧版・参考用）

注意: このモジュールは現在使用されていません。
OAuth処理は main.py の slack-bolt 組み込み機能により自動的に処理されます。

- インストールURL: /slack/install
- コールバックURL: /slack/oauth_redirect

これらのエンドポイントは slack-bolt の OAuthSettings により自動的に処理されます。
インストール情報は FirestoreInstallationStore (main.py内) により Firestore に保存されます。

このファイルは、カスタムOAuth処理が必要な場合の参考実装として保持されています。
"""

import logging
import os
from slack_sdk.oauth import AuthorizeUrlGenerator, OAuthStateUtils
from slack_sdk.web import WebClient

logger = logging.getLogger(__name__)


def handle_oauth_redirect(request):
    """
    OAuth リダイレクトハンドラー（カスタム実装版・未使用）
    
    現在は使用されていません。slack-bolt が自動的に処理します。
    
    Args:
        request: Google Cloud Run の HTTPリクエストオブジェクト
        
    Returns:
        タプル: (レスポンスボディ, HTTPステータスコード)
    """
    from resources.shared.db import save_workspace_config
    
    code = request.args.get("code")
    state = request.args.get("state")
    
    if not code:
        logger.error("OAuth code が見つかりません")
        return "OAuth Error: Missing code parameter", 400
    
    client_id = os.environ.get("SLACK_CLIENT_ID")
    client_secret = os.environ.get("SLACK_CLIENT_SECRET")
    
    if not client_id or not client_secret:
        logger.error("SLACK_CLIENT_ID または SLACK_CLIENT_SECRET が設定されていません")
        return "OAuth Error: Missing credentials", 500
    
    try:
        client = WebClient()
        response = client.oauth_v2_access(
            client_id=client_id,
            client_secret=client_secret,
            code=code
        )
        
        if not response.get("ok"):
            logger.error(f"OAuth トークン交換失敗: {response.get('error')}")
            return f"OAuth Error: {response.get('error')}", 400
        
        team_id = response["team"]["id"]
        team_name = response["team"]["name"]
        bot_token = response["access_token"]
        
        save_workspace_config(
            team_id=team_id,
            team_name=team_name,
            bot_token=bot_token,
            report_channel_id=None
        )
        
        logger.info(f"OAuth インストール成功: Team={team_name} (ID={team_id})")
        
        return f"""
        <!DOCTYPE html>
        <html lang="ja">
        <head>
            <meta charset="UTF-8">
            <title>インストール完了</title>
            <style>
                body {{
                    font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
                    max-width: 600px;
                    margin: 100px auto;
                    padding: 20px;
                    text-align: center;
                }}
                h1 {{ color: #2eb886; }}
                p {{ color: #666; line-height: 1.6; }}
            </style>
        </head>
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
    OAuth インストール用URLを生成します（カスタム実装版・未使用）
    
    現在は使用されていません。slack-bolt が自動的に処理します。
    
    Returns:
        インストール用URL文字列
    """
    client_id = os.environ.get("SLACK_CLIENT_ID")
    redirect_uri = os.environ.get("OAUTH_REDIRECT_URI")
    
    if not client_id or not redirect_uri:
        logger.error("SLACK_CLIENT_ID または OAUTH_REDIRECT_URI が設定されていません")
        return None
    
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
    
    user_scopes = []
    
    state = OAuthStateUtils.generate_state()
    
    generator = AuthorizeUrlGenerator(
        client_id=client_id,
        scopes=scopes,
        user_scopes=user_scopes,
        redirect_uri=redirect_uri
    )
    
    url = generator.generate(state)
    logger.info(f"OAuth インストールURL生成成功: {url}")
    
    return url
