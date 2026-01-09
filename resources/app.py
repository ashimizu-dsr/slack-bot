import os
import sys
from dotenv import load_dotenv

from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

project_root = os.path.dirname(os.path.abspath(__file__))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from shared.db import init_db
from services.attendance_service import AttendanceService
from services.notification_service import NotificationService
from handlers import register_all_handlers 

from apscheduler.schedulers.background import BackgroundScheduler
from services.report_service import send_daily_report

from views.modal_views import create_setup_message_blocks
from constants import ATTENDANCE_CHANNEL_ID, DEFAULT_REPORT_TIME # DEFAULT_REPORT_TIMEを追加

load_dotenv()

def main():

    # 1. データベース初期化
    init_db()
    
    # 2. アプリの初期化
    app = App(
        token=os.environ.get("SLACK_BOT_TOKEN"),
        signing_secret=os.environ.get("SLACK_SIGNING_SECRET")
    )

    # --- スケジューラーの設定 (追加) ---
    scheduler = BackgroundScheduler(timezone="Asia/Tokyo")
    report_time = os.environ.get("DEFAULT_REPORT_TIME", DEFAULT_REPORT_TIME)
    hour, minute = report_time.split(":")
    
    # 毎日決まった時間に実行するジョブを追加
    scheduler.add_job(send_daily_report, 'cron', hour=hour, minute=minute)
    scheduler.start()
    print(f"レポート予約を次の時間で実行します: {hour}:{minute}")

    #  予約されているジョブを一覧表示して確認
    for job in scheduler.get_jobs():
        print(f"現在予約されているジョブ: {job}")

    # 3. サービスの初期化
    attendance_service = AttendanceService()
    notification_service = NotificationService(app.client, attendance_service)
    
    # 4. ハンドラー登録
    register_all_handlers(app, attendance_service, notification_service)

    # --- 起動時にセットアップメッセージを投稿する処理 ---

    
    try:
        app.client.chat_postMessage(
            channel=ATTENDANCE_CHANNEL_ID,
            blocks=create_setup_message_blocks(),
            text="ⓘ 勤怠連絡の管理を開始します。下のボタンより各課のメンバー設定をお願いします。"
        )
    except Exception as e:
        print(f"起動時メッセージ送信失敗: {e}")

    # アプリ開始
    handler = SocketModeHandler(app, os.environ["SLACK_APP_TOKEN"])
    handler.start()
    # 5. Socket Mode 起動
    app_token = os.environ.get("SLACK_APP_TOKEN")
    if not app_token:
        print("❌ エラー: SLACK_APP_TOKEN が設定されていません。")
        return

    # 6. handlerの起動
    handler = SocketModeHandler(app, app_token)
    handler.start()

if __name__ == "__main__":
    main()