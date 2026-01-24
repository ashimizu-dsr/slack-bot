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

# 2. Slackアプリの準備
app = App(
    token=os.environ.get("SLACK_BOT_TOKEN"),
    signing_secret=os.environ.get("SLACK_SIGNING_SECRET"),
    process_before_response=False  # Cloud Runでの非同期処理（直列）に対応
)

# 3. サービスの準備
attendance_service = AttendanceService()
notification_service = NotificationService(app.client, attendance_service)

# 4. Pub/Sub関連の準備（有効な場合）
dispatcher = None
processor = None

if PUBSUB_ENABLED:
    try:
        dispatcher = InteractionDispatcher()
        processor = InteractionProcessor(
            slack_client=WebClient(token=os.environ.get("SLACK_BOT_TOKEN")),
            attendance_service=attendance_service,
            notification_service=notification_service
        )
        logger.info("Pub/Sub機能が有効化されました")
    except Exception as e:
        logger.error(f"Pub/Sub初期化失敗: {e}", exc_info=True)
        dispatcher = None
        processor = None

# 5. ハンドラーの登録（新しいリスナー構造）
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
            
            # --- 環境変数を使わないワークスペースIDの取得 ---
            # 自分の情報を確認して Team ID を取得する
            auth_info = app.client.auth_test()
            workspace_id = auth_info.get("team_id")
            
            if not workspace_id:
                # 万が一取得できなかった場合のフォールバック
                workspace_id = os.environ.get("SLACK_WORKSPACE_ID", "GLOBAL_WS")
            
            logger.info(f"Target date: {today_str}, Workspace ID: {workspace_id}")
            
            # レポート送信処理の実行
            notification_service.send_daily_report(today_str, workspace_id)
            
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

    # 3. 通常のSlackイベント（メッセージ、ボタン、ショートカット等）
    return handler.handle(request)