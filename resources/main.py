import os
import sys
import logging

# プロジェクトのルートディレクトリをパスに追加する（重要！）
# これにより resources/ の外にある shared や services を読み込めるようになります
current_dir = os.path.dirname(os.path.abspath(__file__)) # resourcesフォルダ
project_root = os.path.dirname(current_dir)             # ルートフォルダ
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from slack_bolt import App
from slack_bolt.adapter.google_cloud_functions import SlackRequestHandler

# 自作モジュールの読み込み
from resources.shared.setup_logger import setup_logger
from resources.shared.db import init_db
from resources.services.attendance_service import AttendanceService
from resources.services.notification_service import NotificationService
from resources.handlers import register_all_handlers 

# --- 初期化 ---

# 1. ログとDBの準備（起動時に1回だけ実行される）
setup_logger() # 引数なしで標準出力に流す
init_db()

# 2. Slackアプリの準備
# 環境変数は Google Cloud のコンソール（設定画面）で登録します
app = App(
    token=os.environ.get("SLACK_BOT_TOKEN"),
    signing_secret=os.environ.get("SLACK_SIGNING_SECRET"),
    process_before_response=True  # FaaSでは必須
)

# 3. サービスの準備
attendance_service = AttendanceService()
notification_service = NotificationService(app.client, attendance_service)

# 4. ハンドラーの登録
register_all_handlers(app, attendance_service, notification_service)

# 5. Google Cloud Functions/Run 用のハンドラー（受付窓口）
handler = SlackRequestHandler(app)

#これが「関数ターゲット」に指定する名前になります
def slack_bot(request):
    """HTTPリクエストを受け取ってBoltに渡す"""
    return handler.handle(request)