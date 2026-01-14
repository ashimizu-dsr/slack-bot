
import datetime
import logging
from typing import Optional, List, Dict, Any
from google.cloud import firestore

logger = logging.getLogger(__name__)

try:
    db = firestore.Client()
    logger.info("Firestore client successfully initialized.")
except Exception as e:
    logger.error(f"Failed to initialize Firestore client: {e}")
    raise

def init_db():
    """
    FirestoreはスキーマレスなためSQLのようなテーブル作成は不要ですが、
    接続確認を兼ねて初期ログを出力します。
    """
    logger.info("Initializing Firestore database connection...")
    # 疎通確認
    try:
        db.collection('system_metadata').document('init_check').get()
        logger.info("Firestore connectivity check: OK")
    except Exception as e:
        logger.warning(f"Firestore connectivity check hint: {e}")

def save_attendance_record(workspace_id, user_id, email, date, status, note, channel_id, ts):
    """勤怠レコードの保存または更新"""
    try:
        # ドキュメントIDを「ワークスペース_ユーザー_日付」で固定し、1日1レコードを保証
        doc_id = f"{workspace_id}_{user_id}_{date}"
        doc_ref = db.collection("attendance").document(doc_id)
        
        data = {
            "workspace_id": workspace_id,
            "user_id": user_id,
            "email": email or "",
            "date": date,
            "status": status,
            "note": note,
            "channel_id": channel_id,
            "ts": ts,
            "updated_at": firestore.SERVER_TIMESTAMP # Google側のサーバー時刻
        }
        doc_ref.set(data)
        logger.info(f"Saved attendance: {user_id} on {date} status={status}")
    except Exception as e:
        logger.error(f"Error saving attendance record: {e}")
        raise

def get_single_attendance_record(workspace_id, user_id, date):
    """特定の日付・ユーザーの記録を取得"""
    try:
        doc_id = f"{workspace_id}_{user_id}_{date}"
        doc = db.collection("attendance").document(doc_id).get()
        if doc.exists:
            return doc.to_dict()
        return None
    except Exception as e:
        logger.error(f"Error fetching single record: {e}")
        return None

def get_user_history_from_db(user_id: str, email: Optional[str], month_filter: str) -> List[Dict[str, Any]]:
    """ユーザーの月間履歴を取得"""
    try:
        query = db.collection("attendance")
        # emailがある場合は優先して検索（名寄せ対応）
        if email:
            docs = query.where("email", "==", email).stream()
        else:
            docs = query.where("user_id", "==", user_id).stream()
        
        results = [d.to_dict() for d in docs]
        # 月フィルター(YYYY-MM)に合致するものを抽出してソート
        filtered = [r for r in results if r['date'].startswith(month_filter)]
        return sorted(filtered, key=lambda x: x['date'], reverse=True)
    except Exception as e:
        logger.error(f"Error fetching user history: {e}")
        return []

def delete_attendance_record_db(workspace_id, user_id, date):
    """レコードの削除"""
    try:
        doc_id = f"{workspace_id}_{user_id}_{date}"
        db.collection("attendance").document(doc_id).delete()
        logger.info(f"Deleted record: {doc_id}")
    except Exception as e:
        logger.error(f"Error deleting record: {e}")

def get_channel_members_with_section():
    """メンバー設定（セクション情報）の取得"""
    try:
        doc = db.collection("system_metadata").document("member_config").get()
        if not doc.exists:
            return {}, "0"
        
        data = doc.to_dict()
        return data.get("section_user_map", {}), data.get("updated_at", "0")
    except Exception as e:
        logger.error(f"Error fetching channel members: {e}")
        return {}, "0"

def save_channel_members_db(workspace_id, channel_id, section_user_map, client_version):
    """メンバー設定の保存"""
    try:
        doc_ref = db.collection("system_metadata").document("member_config")
        new_version = datetime.datetime.now().isoformat()
        
        doc_ref.set({
            "section_user_map": section_user_map,
            "updated_at": new_version,
            "workspace_id": workspace_id
        })
        logger.info(f"Updated member config version to {new_version}")
    except Exception as e:
        logger.error(f"Error saving channel members: {e}")

def get_today_records(date_str: Optional[str] = None) -> List[Dict[str, Any]]:
    """指定日（デフォルト今日）の全記録取得"""
    try:
        target_date = date_str or datetime.datetime.now().strftime("%Y-%m-%d")
        docs = db.collection("attendance").where("date", "==", target_date).stream()
        return [d.to_dict() for d in docs]
    except Exception as e:
        logger.error(f"Error fetching today's records: {e}")
        return []

def get_attendance_records_by_sections(target_date: str, section_ids: List[str]) -> List[Dict[str, Any]]:
    """指定セクションのメンバー分をまとめて取得"""
    try:
        # 1. メンバーリストを共有設定から取得
        section_map, _ = get_channel_members_with_section()
        member_ids = []
        for s_id in section_ids:
            member_ids.extend(section_map.get(s_id, []))
        
        if not member_ids:
            return []

        # 2. FirestoreのINクエリ（1回30件まで）で取得
        # ※大量にいる場合はチャンク分けが必要ですが、1課あたり30人以内ならこれでOK
        docs = db.collection("attendance")\
                 .where("date", "==", target_date)\
                 .where("user_id", "in", member_ids[:30])\
                 .stream()
        
        return [d.to_dict() for d in docs]
    except Exception as e:
        logger.error(f"Error fetching records by sections: {e}")
        return []