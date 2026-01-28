"""
Slack勤怠管理Bot - メインエントリポイント

このモジュールは、Google Cloud Run上で動作するマルチテナント対応Slack Botの
エントリポイントです。HTTPリクエストを受け取り、Slackイベント、OAuth認証、
Pub/Subからの非同期処理、またはCloud Schedulerからのジョブリクエストを処理します。

マルチテナント対応:
    - 各ワークスペースのbot_tokenはFirestoreの`workspaces`コレクションに保存
    - イベント処理時にteam_idを取得し、動的にWebClientを生成
    - OAuth Flow により、複数のワークスペースへのインストールが可能
"""

import sys
import os
import datetime
import logging
from typing import Optional, Dict, Any
import base64
import json

# --- 強制ログフラッシュ設定 ---
# Pythonの出力をバッファリングせず、即座にCloud Runのログへ送る
os.environ["PYTHONUNBUFFERED"] = "1"
# 標準出力をラインバッファリング（一行ごとに即送信）に設定
sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)

# 非常にシンプルなログフォーマットを強制適用
logging.basicConfig(
    level=logging.INFO,
    format='%(levelname)s: %(message)s',
    stream=sys.stdout
)
logger = logging.getLogger(__name__)

# 起動直後に「絶対に」出るはずのログ
print("!!! CRITICAL: SYSTEM BOOTING UP !!!", file=sys.stdout, flush=True)
logger.info("!!! LOGGER: SYSTEM BOOTING UP !!!")



# パス追加処理（プロジェクトルートを認識させる）
current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(current_dir)
if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)
if current_dir not in sys.path:
    sys.path.insert(0, current_dir)

# ログ設定
from resources.shared.setup_logger import setup_logger
setup_logger()
logger = logging.getLogger(__name__)

# Firestore
from google.cloud import firestore
from resources.shared.db import init_db
from resources.constants import get_collection_name

# Slack Bolt
from slack_bolt import App
from slack_bolt.adapter.google_cloud_functions import SlackRequestHandler
from slack_sdk import WebClient

# OAuth関連
from slack_bolt.oauth.oauth_settings import OAuthSettings
from slack_sdk.oauth.installation_store import InstallationStore, Installation, Bot

# 自作モジュール
from resources.services.attendance_service import AttendanceService
from resources.services.notification_service import NotificationService
from resources.listeners import register_all_listeners

logger.info(f"Initializing Slack Attendance Bot (Multi-tenant mode)")


# ==========================================
# Firestore Installation Store の実装
# ==========================================

class FirestoreInstallationStore(InstallationStore):
    """
    Firestoreを使用したInstallationStoreの実装。
    
    OAuth Flow により取得した bot_token などを Firestore の workspaces コレクションに保存します。
    各ワークスペース（team_id）ごとに独立したドキュメントとして管理されます。
    """
    
    def __init__(self, client: firestore.Client):
        """
        Args:
            client: google.cloud.firestore.Client インスタンス
        """
        self.db = client
        logger.info("FirestoreInstallationStore initialized")
    
    def save(self, installation: Installation) -> None:
        """
        インストール情報をFirestoreに保存します。
        
        Args:
            installation: slack_bolt.oauth.installation_store.Installation オブジェクト
        """
        try:
            team_id = installation.team_id
            
            data = {
                "team_id": team_id,
                "team_name": installation.team_name or "",
                "bot_token": installation.bot_token,
                "bot_id": installation.bot_id or "",
                "bot_user_id": installation.bot_user_id or "",
                "enterprise_id": installation.enterprise_id or "",
                "is_enterprise_install": installation.is_enterprise_install or False,
                "installed_at": firestore.SERVER_TIMESTAMP,
                "updated_at": firestore.SERVER_TIMESTAMP
            }
            
            self.db.collection(get_collection_name("workspaces")).document(team_id).set(data, merge=True)
            logger.info(f"Installation saved to Firestore: team_id={team_id}, team_name={installation.team_name}")
            
        except Exception as e:
            logger.error(f"Failed to save installation: {e}", exc_info=True)
            raise
    
    def find_installation(
        self,
        *,
        enterprise_id: Optional[str],
        team_id: Optional[str],
        user_id: Optional[str] = None,
        is_enterprise_install: Optional[bool] = False
    ) -> Optional[Installation]:
        """
        インストール情報を取得します。
        
        Args:
            enterprise_id: エンタープライズID（通常はNone）
            team_id: ワークスペースID
            user_id: ユーザーID（使用しない）
            is_enterprise_install: エンタープライズインストールか否か
            
        Returns:
            Installation オブジェクト、見つからない場合は None
        """
        try:
            if not team_id:
                logger.warning("team_id is None, cannot find installation")
                return None
            
            doc = self.db.collection(get_collection_name("workspaces")).document(team_id).get()
            
            if not doc.exists:
                logger.warning(f"Installation not found: team_id={team_id}")
                return None
            
            data = doc.to_dict()
            
            installation = Installation(
                app_id=os.environ.get("SLACK_APP_ID", ""),
                enterprise_id=data.get("enterprise_id") or None,
                team_id=team_id,
                team_name=data.get("team_name", ""),
                bot_token=data.get("bot_token"),
                bot_id=data.get("bot_id", ""),
                bot_user_id=data.get("bot_user_id", ""),
                bot_scopes=["app_mentions:read", "channels:history", "channels:read", 
                           "chat:write", "commands", "users:read", "users:read.email",
                           "reactions:write", "im:history", "groups:history"],
                user_id=user_id or "",
                user_token=None,
                user_scopes=[],
                is_enterprise_install=data.get("is_enterprise_install", False),
                installed_at=data.get("installed_at")
            )
            
            logger.info(f"Installation found: team_id={team_id}")
            return installation
            
        except Exception as e:
            logger.error(f"Failed to find installation: {e}", exc_info=True)
            return None


# ==========================================
# 初期化
# ==========================================

# Firestoreクライアント
init_db()
db_client = firestore.Client()

# OAuth設定
oauth_settings = None
enable_oauth = os.environ.get("ENABLE_OAUTH", "false").lower() == "true"

if enable_oauth:
    client_id = os.environ.get("SLACK_CLIENT_ID")
    client_secret = os.environ.get("SLACK_CLIENT_SECRET")
    
    if client_id and client_secret:
        try:
            oauth_settings = OAuthSettings(
                client_id=client_id,
                client_secret=client_secret,
                scopes=[
                    "app_mentions:read",
                    "channels:history",
                    "channels:read",
                    "chat:write",
                    "commands",
                    "users:read",
                    "users:read.email",
                    "reactions:write",
                    "im:history",
                    "groups:history"
                ],
                installation_store=FirestoreInstallationStore(db_client)
                # state_store を指定しない → デフォルトの CookieStateStore を使用
            )
            logger.info("OAuth settings configured successfully")
        except Exception as e:
            logger.error(f"Failed to configure OAuth: {e}", exc_info=True)
            oauth_settings = None
    else:
        logger.warning("SLACK_CLIENT_ID or SLACK_CLIENT_SECRET not set, OAuth disabled")

# Slack Appの初期化
if oauth_settings:
    # OAuth有効時
    app = App(
        signing_secret=os.environ.get("SLACK_SIGNING_SECRET"),
        oauth_settings=oauth_settings,
        process_before_response=False
    )
    logger.info("Slack App initialized with OAuth")
else:
    # OAuth無効時（従来の単一ワークスペースモード）
    app = App(
        token=os.environ.get("SLACK_BOT_TOKEN"),
        signing_secret=os.environ.get("SLACK_SIGNING_SECRET"),
        process_before_response=False
    )
    logger.info("Slack App initialized without OAuth (single workspace mode)")

# サービスの準備
attendance_service = AttendanceService()

# リスナーの登録（Pub/Sub対応版）
listener_map = register_all_listeners(app, attendance_service)
logger.info("All listeners registered")

# Google Cloud Functions/Run用のハンドラー
handler = SlackRequestHandler(app)


# ==========================================
# ヘルパー関数
# ==========================================

def get_team_id_from_request(request) -> Optional[str]:
    """
    HTTPリクエストからteam_idを抽出します。
    
    Args:
        request: Google Cloud RunのHTTPリクエストオブジェクト
        
    Returns:
        team_id文字列、取得できない場合はNone
    """
    try:
        import json
        
        # JSONボディから取得
        if request.is_json:
            data = request.get_json(silent=True)
            if data:
                # イベントAPI
                if "team_id" in data:
                    return data["team_id"]
                # インタラクション
                if "team" in data and isinstance(data["team"], dict):
                    return data["team"].get("id")
        
        # フォームデータから取得（インタラクション、コマンド等）
        if request.form:
            payload_str = request.form.get("payload")
            if payload_str:
                payload = json.loads(payload_str)
                if "team" in payload and isinstance(payload["team"], dict):
                    return payload["team"].get("id")
            
            # スラッシュコマンド
            if "team_id" in request.form:
                return request.form.get("team_id")
        
        logger.warning("team_id not found in request")
        return None
        
    except Exception as e:
        logger.error(f"Failed to extract team_id: {e}", exc_info=True)
        return None


# ==========================================
# エントリポイント
# ==========================================

def slack_bot(request): 

     # 届いたリクエストのヘッダーをすべて出す
    print(f"!!! HEADERS: {dict(request.headers)}", flush=True) 

    
    path = request.path
    logger.info(f"Request received: path={path}, method={request.method}")
    
    # イベントの詳細をログ出力（デバッグ用）
    if request.is_json:
        try:
            body = request.get_json(silent=True)
            if body:
                event_type = body.get("type")
                event_data = body.get("event", {})
                event_subtype = event_data.get("type") if isinstance(event_data, dict) else None
                
                logger.info(
                    f"Slack Event: type={event_type}, "
                    f"event.type={event_subtype}, "
                    f"team_id={body.get('team_id')}"
                )
                
                # member_joined_channelイベントの詳細ログ
                if event_subtype == "member_joined_channel":
                    logger.info(
                        f"[member_joined_channel] Detected: "
                        f"channel={event_data.get('channel')}, "
                        f"user={event_data.get('user')}, "
                        f"team={event_data.get('team')}"
                    )
        except Exception as e:
            logger.debug(f"Could not parse request body for logging: {e}")
    
    # # 1. OAuth インストールページ
    # if path == "/slack/install":
    #     logger.info("OAuth install page requested")
        
    #     if not oauth_settings:
    #         return "OAuth is not configured. Please set SLACK_CLIENT_ID and SLACK_CLIENT_SECRET.", 500
        
    #     try:
    #         from slack_sdk.oauth import AuthorizeUrlGenerator
    #         from slack_bolt.oauth.oauth_settings import OAuthSettings
            
    #         # インストールURLを生成
    #         authorize_url_generator = AuthorizeUrlGenerator(
    #             client_id=oauth_settings.client_id,
    #             scopes=oauth_settings.scopes,
    #             user_scopes=oauth_settings.user_scopes or []
    #         )
            
    #         state = oauth_settings.state_store.issue()
    #         install_url = authorize_url_generator.generate(state)
            
    #         return f"""
    #         <!DOCTYPE html>
    #         <html lang="ja">
    #         <head>
    #             <meta charset="UTF-8">
    #             <meta name="viewport" content="width=device-width, initial-scale=1.0">
    #             <title>勤怠管理Botのインストール</title>
    #             <style>
    #                 body {{
    #                     font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
    #                     max-width: 600px;
    #                     margin: 100px auto;
    #                     padding: 20px;
    #                     text-align: center;
    #                 }}
    #                 h1 {{
    #                     color: #333;
    #                     margin-bottom: 20px;
    #                 }}
    #                 p {{
    #                     color: #666;
    #                     line-height: 1.6;
    #                     margin-bottom: 30px;
    #                 }}
    #                 .install-button {{
    #                     display: inline-block;
    #                     margin: 20px 0;
    #                 }}
    #             </style>
    #         </head>
    #         <body>
    #             <h1>📊 勤怠管理Botをインストール</h1>
    #             <p>以下のボタンをクリックして、ワークスペースにBotをインストールしてください。</p>
    #             <div class="install-button">
    #                 <a href="{install_url}">
    #                     <img alt="Add to Slack" 
    #                          height="40" 
    #                          width="139" 
    #                          src="https://platform.slack-edge.com/img/add_to_slack.png" 
    #                          srcSet="https://platform.slack-edge.com/img/add_to_slack.png 1x, 
    #                                 https://platform.slack-edge.com/img/add_to_slack@2x.png 2x" />
    #                 </a>
    #             </div>
    #             <p style="font-size: 0.9em; color: #999;">
    #                 インストール後、Slackアプリに戻って使用を開始できます。
    #             </p>
    #         </body>
    #         </html>
    #         """, 200
            
    #     except Exception as e:
    #         logger.error(f"Failed to generate install URL: {e}", exc_info=True)
    #         return f"Error: {e}", 500

    logger.info(f"--- INCOMING REQUEST --- Path: {request.path}")
    if request.is_json:
        logger.info(f"Body: {request.get_json()}")
    
    # 1. OAuth インストールページ
    if path == "/slack/oauth_redirect":
        logger.info("OAuth redirect request received")
        return handler.handle(request)
    
    # 2. Cloud Schedulerからのレポート実行リクエスト
    if path == "/job/report":
        # logger.info("Cloud Scheduler triggered: Starting daily report...")
        
        try:
            from datetime import timezone, timedelta
            from resources.clients.slack_client import get_slack_client
            
            JST = timezone(timedelta(hours=9))
            today_str = datetime.datetime.now(JST).date().isoformat()
            
            # マルチテナント対応: 全ワークスペースに対してレポートを送信
            workspaces_docs = db_client.collection(get_collection_name("workspaces")).stream()
            
            success_count = 0
            error_count = 0
            
            for ws_doc in workspaces_docs:
                workspace_id = ws_doc.id
                workspace_data = ws_doc.to_dict()
                
                logger.info(f"Processing daily report: date={today_str}, workspace={workspace_id}")
                
                try:
                    # ワークスペースごとに WebClient を取得
                    client = get_slack_client(workspace_id)
                    
                    # NotificationService を動的に生成
                    notification_service_instance = NotificationService(client, attendance_service)
                    
                    # レポート送信処理の実行
                    notification_service_instance.send_daily_report(today_str, workspace_id)
                    success_count += 1
                    
                except Exception as ws_error:
                    logger.error(f"Failed to send report for workspace {workspace_id}: {ws_error}", exc_info=True)
                    error_count += 1
                    continue
            
            return {
                "status": "completed",
                "date": today_str,
                "success": success_count,
                "errors": error_count
            }, 200
            
        except Exception as e:
            logger.error(f"Failed to send daily report: {e}", exc_info=True)
            return {"status": "error", "message": str(e)}, 500
    
    # 4. Pub/Subからのプッシュリクエスト(非同期処理)
    if path == "/pubsub/interactions":        
        try:
            # Pub/Subメッセージのデコード
            envelope = request.get_json()
            if not envelope or "message" not in envelope:
                return "Invalid Pub/Sub message", 400
                
            pubsub_data = envelope["message"].get("data", "")
            data_str = base64.b64decode(pubsub_data).decode("utf-8")
            payload = json.loads(data_str)

            action_type = payload.get("action_type")
            team_id = payload.get("team_id")
            event = payload.get("event")

            # リスナーマップから適切なリスナーを取得して実行
            listener = listener_map.get(action_type)
            if listener:
                logger.info(f"Pub/Sub: Dispatching to {action_type}")
                listener.handle_async(team_id, event)
            else:
                logger.warning(f"Unknown action_type: {action_type}")

            # 正常終了を返す（リトライを防ぐ）
            return "OK", 200

        except Exception as e:
            logger.error(f"Pub/Sub dispatch failed: {e}", exc_info=True)
            # 500を返すとPub/Subが無限再送するため、エラーでも一旦200で止める運用を推奨
            return {"status": "error", "message": str(e)}, 200
    
    # 5. 通常のSlackイベント（メッセージ、ボタン、ショートカット等）
    try:
        return handler.handle(request)
    except Exception as e:
        logger.error(f"Failed to handle Slack event: {e}", exc_info=True)
        return {"status": "error", "message": str(e)}, 200
