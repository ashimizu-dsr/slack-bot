"""
Slack勤怠管理Bot - メインエントリポイント

このモジュールは、Google Cloud Run/Functions上で動作するSlack Botの
エントリポイントです。HTTPリクエストを受け取り、Slackイベントまたは
Cloud Schedulerからのジョブリクエストを処理します。
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
    print("DEBUG: slack_bolt imported", file=sys.stderr)
except Exception as e:
    print(f"DEBUG: Import error: {e}", file=sys.stderr)

# 自作モジュールの読み込み
from resources.shared.setup_logger import setup_logger
from resources.shared.db import init_db
from resources.services.attendance_service import AttendanceService
from resources.services.notification_service import NotificationService
from resources.handlers import register_all_handlers 

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

# 4. ハンドラーの登録
register_all_handlers(app, attendance_service, notification_service)

# 5. Google Cloud Functions/Run 用のハンドラー
handler = SlackRequestHandler(app)

# ==========================================
# エントリポイント
# ==========================================

def slack_bot(request):
    """
    HTTPリクエストを受け取り、パスに応じてSlack処理か定期ジョブかを分岐させます。
    
    Args:
        request: Google Cloud RunのHTTPリクエストオブジェクト
        
    Returns:
        タプル: (レスポンスボディ, HTTPステータスコード)
        
    Note:
        パスによる分岐:
        - "/job/report": Cloud Schedulerからの日次レポート実行リクエスト
        - それ以外: Slackイベント（メッセージ、ボタン、ショートカットなど）
    """
    
    # パスの取得（Cloud RunのURL末尾）
    path = request.path
    
    # 1. Cloud Schedulerからのレポート実行リクエスト
    # Schedulerの設定で URL を https://[YOUR-URL]/job/report に設定してください
    if path == "/job/report":
        logger.info("Cloud Scheduler triggered: Starting daily report...")
        
        try:
            # 日本時間 (JST) の日付を取得
            # Cloud Runのシステム時間はUTCのため、+9時間して日付を確定させる
            from datetime import timezone, timedelta
            JST = timezone(timedelta(hours=9))
            today_str = datetime.datetime.now(JST).date().isoformat()
            
            logger.info(f"Target date for report: {today_str}")
            
            # workspace_idの取得（環境変数から）
            workspace_id = os.environ.get("SLACK_WORKSPACE_ID", "GLOBAL_WS")
            
            # レポート送信処理の実行
            notification_service.send_daily_report(today_str, workspace_id)
            
            return "Daily report sent successfully", 200
            
        except Exception as e:
            logger.error(f"Failed to send daily report: {e}", exc_info=True)
            return f"Internal Server Error: {e}", 500

    # 2. 通常のSlackイベント（メッセージ、ボタン、ショートカット等）
    # path が "/" やそれ以外の場合はすべて Bolt 側へ渡す
    return handler.handle(request)