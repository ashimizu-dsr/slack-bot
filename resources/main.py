"""
Slack勤怠管理Bot - メインエントリポイント

このモジュールは、Google Cloud Run/Functions上で動作するSlack Botの
エントリポイントです。HTTPリクエストを受け取り、Slackイベント、
Pub/Subからの非同期処理、またはCloud Schedulerからのジョブリクエストを処理します。
"""
import sys
import os
import datetime
import logging

# 何よりも先にこれを出す（Cloud Logging の「stderr」に必ず載ります）
print("DEBUG: Starting main.py", file=sys.stderr)

# パス追加処理（プロジェクトルートを認識させる）
current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(current_dir)
if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)
if current_dir not in sys.path:
    sys.path.insert(0, current_dir)

# ロガーのセットアップ
logger = logging.getLogger(__name__)

# Slack Bolt のインポート
try:
    from slack_bolt import App
    from slack_bolt.adapter.google_cloud_functions import SlackRequestHandler
    from slack_sdk import WebClient
    print("DEBUG: slack_bolt imported", file=sys.stderr)
except Exception as e:
    print(f"DEBUG: Import error: {e}", file=sys.stderr)

# 自作モジュールの読み込み
from resources.shared.setup_logger import setup_logger
from resources.shared.db import init_db
from resources.services.attendance_service import AttendanceService
from resources.services.notification_service import NotificationService
from resources.listeners import register_all_listeners

# Pub/Sub関連のインポート（オプション）
PUBSUB_ENABLED = os.environ.get("ENABLE_PUBSUB", "false").lower() == "true"

if PUBSUB_ENABLED:
    try:
        from resources.handlers.interaction_dispatcher import InteractionDispatcher
        from resources.handlers.interaction_processor import InteractionProcessor, create_pubsub_endpoint
        print("DEBUG: Pub/Sub modules imported", file=sys.stderr)
    except Exception as e:
        print(f"DEBUG: Pub/Sub import error: {e}", file=sys.stderr)
        PUBSUB_ENABLED = False

# ==========================================
# 初期化
# ==========================================

# 1. ログとDBの準備（起動時に1回だけ実行される）
setup_logger()
init_db()

print("Starting slack_bot application...", file=sys.stderr)

# 2. Slackアプリの準備（マルチテナント対応: tokenなし）
app = App(
    signing_secret=os.environ.get("SLACK_SIGNING_SECRET"),
    process_before_response=False  # Cloud Runでの非同期処理（直列）に対応
)

# 3. サービスの準備（マルチテナント対応: clientは動的取得）
attendance_service = AttendanceService()
# notification_service は各リクエストで動的に生成するため、ここでは初期化しない
notification_service = None

# 4. Pub/Sub関連の準備（有効な場合）
dispatcher = None
processor = None

if PUBSUB_ENABLED:
    try:
        dispatcher = InteractionDispatcher()
        # processor も動的にクライアントを取得する必要があるため、ここでは初期化しない
        logger.info("Pub/Sub機能が有効化されました（マルチテナント対応）")
    except Exception as e:
        logger.error(f"Pub/Sub初期化失敗: {e}", exc_info=True)
        dispatcher = None
        processor = None

# 5. ハンドラーの登録（新しいリスナー構造）
# notification_service は None でも OK（リスナー内で動的に生成）
register_all_listeners(app, attendance_service, notification_service, dispatcher=dispatcher)

# 6. Google Cloud Functions/Run 用のハンドラー
handler = SlackRequestHandler(app)

# ==========================================
# エントリポイント
# ==========================================

def slack_bot(request):
    """
    HTTPリクエストを受け取り、パスに応じて処理を分岐させます。
    
    Args:
        request: Google Cloud RunのHTTPリクエストオブジェクト
        
    Returns:
        タプル: (レスポンスボディ, HTTPステータスコード)
        
    Note:
        パスによる分岐:
        - "/job/report": Cloud Schedulerからの日次レポート実行リクエスト
        - "/pubsub/interactions": Pub/Subからのプッシュリクエスト（非同期処理）
        - それ以外: Slackイベント（メッセージ、ボタン、ショートカットなど）
    """
    
    # パスの取得（Cloud RunのURL末尾）
    path = request.path
    
    # 1. Cloud Schedulerからのレポート実行リクエスト
    if path == "/job/report":
        logger.info("Cloud Scheduler triggered: Starting daily report...")
        
        try:
            # 日本時間 (JST) の日付を取得
            from datetime import timezone, timedelta
            JST = timezone(timedelta(hours=9))
            today_str = datetime.datetime.now(JST).date().isoformat()
            
            # マルチテナント対応: 全ワークスペースに対してレポートを送信
            from resources.shared.db import db
            from resources.clients.slack_client import get_slack_client
            
            workspaces_docs = db.collection("workspaces").stream()
            
            for ws_doc in workspaces_docs:
                workspace_id = ws_doc.id
                workspace_data = ws_doc.to_dict()
                
                logger.info(f"Target date: {today_str}, Workspace ID: {workspace_id}")
                
                try:
                    # ワークスペースごとに WebClient を取得
                    client = get_slack_client(workspace_id)
                    
                    # NotificationService を動的に生成
                    from resources.services.notification_service import NotificationService
                    notification_service = NotificationService(client, attendance_service)
                    
                    # レポート送信処理の実行
                    notification_service.send_daily_report(today_str, workspace_id)
                    
                except Exception as ws_error:
                    logger.error(f"レポート送信失敗 (workspace={workspace_id}): {ws_error}", exc_info=True)
                    continue
            
            return "Daily report sent successfully", 200
            
        except Exception as e:
            logger.error(f"Failed to send daily report: {e}", exc_info=True)
            return f"Internal Server Error: {e}", 500
    
    # 2. Pub/Subからのプッシュリクエスト（非同期処理）
    if path == "/pubsub/interactions" and PUBSUB_ENABLED and processor:
        logger.info("Pub/Sub push request received")
        
        try:
            pubsub_handler = create_pubsub_endpoint(app, processor)
            response, status = pubsub_handler(request)
            return response, status
        except Exception as e:
            logger.error(f"Pub/Sub処理失敗: {e}", exc_info=True)
            return {"status": "error", "message": str(e)}, 500
    
    # 3. OAuth コールバック（アプリインストール時）
    if path == "/oauth/callback":
        logger.info("OAuth callback request received")
        
        try:
            from resources.handlers.oauth_handler import handle_oauth_redirect
            response, status = handle_oauth_redirect(request)
            return response, status
        except Exception as e:
            logger.error(f"OAuth処理失敗: {e}", exc_info=True)
            return f"OAuth Error: {e}", 500
    
    # 4. OAuth インストールURL取得（テスト用）
    if path == "/oauth/install":
        logger.info("OAuth install URL request received")
        
        try:
            from resources.handlers.oauth_handler import get_oauth_install_url
            install_url = get_oauth_install_url()
            
            if not install_url:
                return "OAuth設定が不完全です。環境変数を確認してください。", 500
            
            return f"""
            <html>
            <head><title>アプリのインストール</title></head>
            <body>
                <h1>勤怠管理Botをインストール</h1>
                <p>以下のボタンをクリックして、ワークスペースにBotをインストールしてください。</p>
                <a href="{install_url}">
                    <img alt="Add to Slack" height="40" width="139" 
                         src="https://platform.slack-edge.com/img/add_to_slack.png" 
                         srcSet="https://platform.slack-edge.com/img/add_to_slack.png 1x, 
                                https://platform.slack-edge.com/img/add_to_slack@2x.png 2x" />
                </a>
            </body>
            </html>
            """, 200
        except Exception as e:
            logger.error(f"OAuth URL生成失敗: {e}", exc_info=True)
            return f"Error: {e}", 500

    # 5. 通常のSlackイベント（メッセージ、ボタン、ショートカット等）
    return handler.handle(request)