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
import logging

# ロギング設定（最優先で実行）
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# 環境変数の読み込み
# 既にAPP_ENVが設定されていれば.envファイルは読み込まない
# この判定により、本番環境で設定した環境変数が.envファイルで上書きされることを防ぐ
if "APP_ENV" not in os.environ:
    # APP_ENVが未設定の場合のみ.envファイルを読み込む（ローカル開発用）
    from dotenv import load_dotenv
    load_dotenv(override=False)
    logger.info("[constants.py] APP_ENV not found in OS environment, loaded .env file")
else:
    logger.info("[constants.py] APP_ENV found in OS environment, skipping .env file")

# ==========================================
# 1. 全ワークスペース共通の定数
# ==========================================

# アプリケーション環境（production / develop）
APP_ENV_RAW = os.environ.get("APP_ENV", "develop")
# 空文字列の場合もデフォルト値を使用
APP_ENV = APP_ENV_RAW.strip() if APP_ENV_RAW and APP_ENV_RAW.strip() else "develop"

# デバッグログ：APP_ENVの値を確認
logger.info(f"[constants.py] APP_ENV_RAW: '{APP_ENV_RAW}' (from {'env var' if 'APP_ENV' in os.environ else 'default'})")
logger.info(f"[constants.py] APP_ENV loaded: '{APP_ENV}'")


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

# 勤怠ステータスの日本語訳（最新ルール 2026-01-27）
# 表示順: 休暇 > 遅刻 > 在宅 > 外出 > シフト勤務 > その他
STATUS_TRANSLATION = {
    # 大分類：休暇
    "vacation": "全休",
    "vacation_am": "AM休",
    "vacation_pm": "PM休",
    "vacation_hourly": "時間休",
    
    # 大分類：遅刻
    "late_delay": "電車遅延",
    "late": "遅刻",
    
    # 大分類：その他
    "remote": "在宅",
    "out": "外出",
    "shift": "シフト勤務",
    
    # その他
    "early_leave": "早退",
    "other": "その他",
}

# AI判定用の逆引き・エイリアス定義（追加）
# ここに「AIに紐づけさせたい言葉」を列挙します
STATUS_AI_ALIASES = {
    "vacation_am": ["vacation_am", "am休", "午前休", "午前半休", "午前"],
    "vacation_pm": ["vacation_pm", "pm休", "午後休", "午後半休", "午後"],
    "vacation_hourly": ["vacation_hourly", "時間休", "時休"],
    "vacation": ["vacation", "休暇", "休み", "全休", "有給"],
    "late_delay": ["late_delay", "電車遅延", "遅延", "交通乱れ"],
    "late": ["late", "遅刻"],
    "out": ["out", "外出", "直行", "直帰", "情報センター", "楽天損保"],
    "shift": ["shift", "シフト", "交代勤務", "シフト勤務"],
    "remote": ["remote", "在宅", "リモート"],
    "early_leave": ["early_leave", "早退", "退勤"],
}

# レポート表示用のステータス順序（大分類順）
STATUS_ORDER = [
    # 休暇
    "vacation", "vacation_am", "vacation_pm", "vacation_hourly",
    # 遅刻
    "late_delay", "late",
    # その他
    "remote", "out", "shift",
    # その他（レポートには通常表示しない）
    "early_leave", "other"
]

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