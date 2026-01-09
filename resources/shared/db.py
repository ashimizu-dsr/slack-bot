import os
import sqlite3
import logging
import datetime
from contextlib import contextmanager
from typing import Optional, List, Dict, Any, Tuple
# constants から必要な設定をインポート
try:
    from constants import DB_PATH, SLACK_CLIENT_ID
except ImportError:
    DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "kintai.db")
    SLACK_CLIENT_ID = os.environ.get("SLACK_CLIENT_ID", "")

logger = logging.getLogger(__name__)

@contextmanager
def db_conn():
    # check_same_thread=False は、非同期処理(AI解析スレッド等)でDBを共有するために必須です
    conn = sqlite3.connect(DB_PATH, timeout=10.0, check_same_thread=False)
    try:
        conn.execute("PRAGMA journal_mode=WAL")  # 書き込み中の読み取りを許可し、同時実行性を向上
        conn.row_factory = sqlite3.Row
        yield conn
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise
    finally:
        conn.close()

def init_db():
    """データベースの初期化：勤怠テーブルおよびOAuth用テーブルの作成"""
    with db_conn() as conn:
        cur = conn.cursor()
        
        # 1. 勤怠テーブル（email列を含む）
        cur.execute("""
            CREATE TABLE IF NOT EXISTS attendance (
                workspace_id TEXT, user_id TEXT, email TEXT, date TEXT, 
                status TEXT, note TEXT, channel_id TEXT, ts TEXT,
                PRIMARY KEY (workspace_id, user_id, date)
            )
        """)

        # 2. チャンネルメンバー（共通設定用）
        cur.execute("""
            CREATE TABLE IF NOT EXISTS channel_members (
                workspace_id TEXT, channel_id TEXT, section_id TEXT, member_user_id TEXT,
                PRIMARY KEY (workspace_id, channel_id, section_id, member_user_id)
            )
        """)

        # 3. メタデータ（バージョン管理用）
        cur.execute("CREATE TABLE IF NOT EXISTS system_metadata (key TEXT PRIMARY KEY, value TEXT, updated_at TEXT)")

        # --- 4. OAuth用テーブル (将来の拡張用。シングルモードでは使用しませんが定義は残します) ---
        cur.execute("""
            CREATE TABLE IF NOT EXISTS slack_installations (
                id INTEGER PRIMARY KEY AUTOINCREMENT, client_id TEXT, app_id TEXT, team_id TEXT, 
                bot_token TEXT, bot_id TEXT, bot_user_id TEXT, installed_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS slack_bots (
                id INTEGER PRIMARY KEY AUTOINCREMENT, client_id TEXT, app_id TEXT, team_id TEXT, 
                bot_token TEXT, installed_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
            
    logger.info("Database initialized (Single/Multi Hybrid Mode)")

def get_installation_store():
    """Slackのインストール情報を保存するためのストアを取得（OAuth用）"""
    from slack_sdk.oauth.installation_store.sqlite3 import SQLite3InstallationStore
    return SQLite3InstallationStore(database=DB_PATH, client_id=SLACK_CLIENT_ID)

# --- データ操作関数 ---

def save_attendance_record(workspace_id, user_id, email, date, status, note, channel_id, ts):
    """勤怠レコードの保存または更新"""
    with db_conn() as conn:
        # INSERT OR REPLACE により、同一日の記録は上書きされます
        conn.execute("""
            INSERT OR REPLACE INTO attendance 
            (workspace_id, user_id, email, date, status, note, channel_id, ts) 
            VALUES (?,?,?,?,?,?,?,?)
        """, (workspace_id, user_id, email or "", date, status, note, channel_id, ts))

def get_single_attendance_record(workspace_id, user_id, date):
    """特定の日付・ユーザーの記録を取得"""
    with db_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM attendance WHERE workspace_id = ? AND user_id = ? AND date = ?", 
                   (workspace_id, user_id, date))
        row = cur.fetchone()
        return dict(row) if row else None

def get_user_history_from_db(user_id: str, email: Optional[str], month_filter: str) -> List[Dict[str, Any]]:
    """
    名寄せ（メールアドレス）を優先した履歴取得。
    別ワークスペースでの過去ログもメールアドレスが一致すれば取得できます。
    """
    with db_conn() as conn:
        cur = conn.cursor()
        # emailがある場合はemailを、ない場合はuser_idをキーに検索
        if email:
            cur.execute("""
                SELECT * FROM attendance 
                WHERE (email = ? OR user_id = ?) AND date LIKE ? 
                ORDER BY date DESC
            """, (email, user_id, f"{month_filter}%"))
        else:
            cur.execute("""
                SELECT * FROM attendance 
                WHERE user_id = ? AND date LIKE ? 
                ORDER BY date DESC
            """, (user_id, f"{month_filter}%"))
            
        return [dict(row) for row in cur.fetchall()]

def delete_attendance_record_db(workspace_id, user_id, date):
    with db_conn() as conn:
        conn.execute("DELETE FROM attendance WHERE workspace_id = ? AND user_id = ? AND date = ?", 
                    (workspace_id, user_id, date))

def get_channel_members_with_section():
    """メンバー設定の取得（現在はGLOBAL設定として共有）"""
    with db_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT updated_at FROM system_metadata WHERE key = 'member_config_version'")
        res = cur.fetchone()
        version = res["updated_at"] if res else "0"
        
        cur.execute("SELECT section_id, member_user_id FROM channel_members")
        rows = cur.fetchall()
        result = {}
        for row in rows:
            s_id, u_id = row["section_id"], row["member_user_id"]
            if s_id not in result: result[s_id] = []
            result[s_id].append(u_id)
        return result, version

def save_channel_members_db(workspace_id, channel_id, section_user_map, client_version):
    """メンバー設定の保存"""
    with db_conn() as conn:
        cur = conn.cursor()
        # 今回は簡易的に全削除・全挿入
        cur.execute("DELETE FROM channel_members")
        for sid, uids in section_user_map.items():
            for uid in uids:
                cur.execute("""
                    INSERT INTO channel_members (workspace_id, channel_id, section_id, member_user_id) 
                    VALUES (?, ?, ?, ?)
                """, ('GLOBAL_WS', 'GLOBAL_CH', sid, uid))
        
        new_version = datetime.datetime.now().isoformat()
        cur.execute("INSERT OR REPLACE INTO system_metadata (key, value, updated_at) VALUES ('member_config_version', '1', ?)", (new_version,))

def get_today_records(date_str: Optional[str] = None) -> List[Dict[str, Any]]:
    """指定日（デフォルトは今日）の全記録取得"""
    target_date = date_str or datetime.datetime.now().strftime("%Y-%m-%d")
    with db_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM attendance WHERE date = ?", (target_date,))
        return [dict(row) for row in cur.fetchall()]

# --- データ操作関数 (追加・修正) ---

def get_attendance_records_by_sections(target_date: str, section_ids: List[str]) -> List[Dict[str, Any]]:
    """
    指定されたセクション（課）に所属するメンバーの、特定の日付の勤怠レコードを取得する。
    """
    with db_conn() as conn:
        cur = conn.cursor()
        
        # 1. 指定されたセクションに所属するユーザーIDのリストを取得
        # section_ids が ['sec_1', 'sec_2'] のようなリストであることを想定
        placeholders = ', '.join(['?'] * len(section_ids))
        query_members = f"SELECT member_user_id FROM channel_members WHERE section_id IN ({placeholders})"
        cur.execute(query_members, section_ids)
        
        member_ids = [row["member_user_id"] for row in cur.fetchall()]
        
        if not member_ids:
            return []

        # 2. それらのメンバーの指定日の勤怠レコードを取得
        placeholders_members = ', '.join(['?'] * len(member_ids))
        query_attendance = f"""
            SELECT * FROM attendance 
            WHERE date = ? AND user_id IN ({placeholders_members})
        """
        # 引数を組み立て: [日付, user_id1, user_id2, ...]
        params = [target_date] + member_ids
        cur.execute(query_attendance, params)
        
        return [dict(row) for row in cur.fetchall()]

def get_attendance_by_section(self, target_date: str, section_id: str):
    """
    (AttendanceService経由で呼ばれる場合を想定したエイリアス)
    特定の1つのセクションのレコードを返す
    """
    return get_attendance_records_by_sections(target_date, [section_id])