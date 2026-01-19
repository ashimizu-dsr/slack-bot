"""
定数定義モジュール
ステータス翻訳、セクション翻訳、その他の定数を集約
マルチワークスペース対応：動的な値はここでの固定を避ける
"""

import os
from dotenv import load_dotenv

load_dotenv()

# --- 1. 全ワークスペース共通の定数 (変更不要) ---

# 勤怠ステータスの日本語訳
STATUS_TRANSLATION = {
    "late": "遅刻",
    "early_leave": "早退",
    "out": "外出",
    "remote": "在宅",
    "vacation": "休暇",
    "other": "その他",
}

# 課のIDと日本語訳
SECTION_TRANSLATION = {
    "sec_1": "1課",
    "sec_2": "2課",
    "sec_3": "3課",
    "sec_4": "4課",
    "sec_5": "5課",
    "sec_6": "6課",
    "sec_7": "7課",
    "sec_finance": "金融開発課",
}

# スケジューラのポーリング間隔（秒）
SCHEDULER_POLL_INTERVAL_SEC = 0

# デフォルトレポート時刻
DEFAULT_REPORT_TIME = "14:41"

# 環境変数から取得する設定値
ENABLE_CHANNEL_NLP = os.getenv("ENABLE_CHANNEL_NLP", "true").lower() == "true"

# --- 2. ワークスペースごとに異なる動的な値 (初期化のみ) ---

# 注意: これらは app.py 起動時や、各イベントハンドラ内で
# DBや環境変数から取得した値をセットするために、空の状態にしておきます。
SLACK_CLIENT_ID = os.environ.get("SLACK_CLIENT_ID")
SLACK_CLIENT_SECRET = os.environ.get("SLACK_CLIENT_SECRET")
SLACK_SIGNING_SECRET = os.environ.get("SLACK_SIGNING_SECRET")
REPORT_CHANNEL_ID = os.environ.get("REPORT_CHANNEL_ID")
ATTENDANCE_CHANNEL_ID = os.environ.get("CHANREPORT_CHANNEL_IDNEL_ID")
SECTION_CHANNELS = {}

# --- 3. パス設定 (追加) ---

# データベースファイルの保存場所
DB_PATH = os.getenv("DB_PATH", "attendance.db")

# OAuthのState（一時的な認証キー）を保存するディレクトリ
# SQLite3InstallationStoreとは別に、インストール中の一時状態を管理するために使用
DATA_DIR = "data"
if not os.path.exists(DATA_DIR):
    os.makedirs(DATA_DIR)

STATE_STORE_PATH = os.path.join(DATA_DIR, "oauth_states")