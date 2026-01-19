import sys
import os

# 何よりも先にこれを出す（Cloud Logging の「stderr」に必ず載ります）
print("DEBUG: Starting main.py", file=sys.stderr)
print(f"DEBUG: Current working directory: {os.getcwd()}", file=sys.stderr)
print(f"DEBUG: sys.path: {sys.path}", file=sys.stderr)

# 既存のパス追加処理
current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(current_dir)
sys.path.insert(0, parent_dir)
sys.path.insert(0, current_dir)

print(f"DEBUG: Modified sys.path: {sys.path}", file=sys.stderr)

# ここから import。もしここでエラーが起きても、上の print はログに残るはずです
try:
    from slack_bolt import App
    print("DEBUG: slack_bolt imported", file=sys.stderr)
except Exception as e:
    print(f"DEBUG: Import error: {e}", file=sys.stderr)

# プロジェクトのルートディレクトリをパスに追加する（重要！）
# これにより resources/ の外にある shared や services を読み込めるようになります

# 1. 実行している main.py の絶対パスを取得
current_dir = os.path.dirname(os.path.abspath(__file__))
# 2. その親ディレクトリ（/workspace）を取得
parent_dir = os.path.dirname(current_dir)

# 3. 親ディレクトリと自分自身のディレクトリの両方を検索パスに追加
if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)
if current_dir not in sys.path:
    sys.path.insert(0, current_dir)

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

print("Starting slack_bot application...") # ログエクスプローラーに出るはずです

# 2. Slackアプリの準備
# 環境変数は Google Cloud のコンソール（設定画面）で登録します
app = App(
    token=os.environ.get("SLACK_BOT_TOKEN"),
    signing_secret=os.environ.get("SLACK_SIGNING_SECRET"),
    process_before_response=True  # FaaSでは必須、lazy化対応
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