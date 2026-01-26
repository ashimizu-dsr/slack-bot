"""
定数定義モジュール

このモジュールは、アプリケーション全体で使用する定数を集約します。
ステータス翻訳、セクション定義、環境変数の読み込みなどを提供します。

注意: マルチテナント対応により、以下の環境変数は非推奨となりました:
- SLACK_BOT_TOKEN (workspaces コレクションから取得)
- REPORT_CHANNEL_ID (workspaces コレクションから取得)
- SLACK_WORKSPACE_ID (リクエストの team_id から取得)
"""

import os
from dotenv import load_dotenv

# 環境変数の読み込み
load_dotenv()

# ==========================================
# 1. 全ワークスペース共通の定数
# ==========================================

# アプリケーション環境（production / develop）
APP_ENV = os.environ.get("APP_ENV", "develop")


def get_collection_name(base_name: str) -> str:
    """
    環境変数に応じたFirestoreコレクション名を返します。
    
    Args:
        base_name: ベースとなるコレクション名（例: "attendance", "users", "workspaces", "groups"）
        
    Returns:
        環境に応じたコレクション名:
        - production: base_name をそのまま返す
        - develop: base_name + "_dev" を返す
        
    Examples:
        >>> os.environ["APP_ENV"] = "production"
        >>> get_collection_name("attendance")
        'attendance'
        >>> os.environ["APP_ENV"] = "develop"
        >>> get_collection_name("attendance")
        'attendance_dev'
    """
    if APP_ENV == "production":
        return base_name
    else:
        return f"{base_name}_dev"

# 勤怠ステータスの日本語訳
STATUS_TRANSLATION = {
    "late": "遅刻",
    "early_leave": "早退",
    "out": "外出",
    "remote": "在宅",
    "vacation": "年休",
    "other": "その他",
}

# 課（セクション）のIDと日本語訳
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

# ==========================================
# 2. 環境変数から取得する設定値
# ==========================================

# Slack App 認証情報（マルチテナント対応）
SLACK_SIGNING_SECRET = os.environ.get("SLACK_SIGNING_SECRET")
SLACK_CLIENT_ID = os.environ.get("SLACK_CLIENT_ID")  # OAuth Flow用
SLACK_CLIENT_SECRET = os.environ.get("SLACK_CLIENT_SECRET")  # OAuth Flow用

# OpenAI API設定
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")

# 機能フラグ
ENABLE_CHANNEL_NLP = os.getenv("ENABLE_CHANNEL_NLP", "true").lower() == "true"

# チャンネルID設定（指定すると、そのチャンネルのみで動作）
ATTENDANCE_CHANNEL_ID = os.environ.get("ATTENDANCE_CHANNEL_ID")  # AI解析対象

# ==========================================
# 3. その他の設定
# ==========================================

# スケジューラのポーリング間隔（秒）※現在は未使用
SCHEDULER_POLL_INTERVAL_SEC = 0

# デフォルトレポート時刻※現在は未使用
DEFAULT_REPORT_TIME = "14:41"

# データベースファイルの保存場所（SQLite使用時）※現在はFirestore使用
DB_PATH = os.getenv("DB_PATH", "attendance.db")

# データディレクトリ
DATA_DIR = "data"
if not os.path.exists(DATA_DIR):
    os.makedirs(DATA_DIR)

# OAuthのState保存パス※OAuth未実装のため現在は未使用
STATE_STORE_PATH = os.path.join(DATA_DIR, "oauth_states")