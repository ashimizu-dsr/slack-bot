"""
Firestore データベース操作モジュール

このモジュールは、Firestoreとの全てのデータベース操作を統括します。
全ての関数はworkspace_idを引数に取り、マルチテナント環境に対応しています。
"""

import datetime
import logging
from typing import Optional, List, Dict, Any
from google.cloud import firestore

from resources.constants import get_collection_name

logger = logging.getLogger(__name__)

# Firestoreクライアントのグローバルインスタンス
try:
    db = firestore.Client()
    logger.info("Firestore client successfully initialized.")
except Exception as e:
    logger.error(f"Failed to initialize Firestore client: {e}")
    raise


def init_db() -> None:
    """
    データベース接続の初期化と疎通確認を実行します。
    
    FirestoreはスキーマレスなためSQLのようなテーブル作成は不要ですが、
    接続確認を兼ねて初期ログを出力します。
    
    Raises:
        Exception: Firestore接続に失敗した場合（警告のみ）
    """
    logger.info("Initializing Firestore database connection...")
    try:
        db.collection(get_collection_name('system_metadata')).document('init_check').get()
        logger.info("Firestore connectivity check: OK")
    except Exception as e:
        logger.warning(f"Firestore connectivity check hint: {e}")

def save_attendance_record(
    workspace_id: str, 
    user_id: str, 
    email: Optional[str], 
    date: str, 
    status: str, 
    note: str, 
    channel_id: str, 
    ts: str
) -> None:
    """
    勤怠レコードをFirestoreに保存または更新します。
    
    Args:
        workspace_id: Slackワークスペースの一意ID
        user_id: SlackユーザーID
        email: ユーザーのメールアドレス（取得できない場合はNone）
        date: 対象日（YYYY-MM-DD形式）
        status: 勤怠区分（late, early_leave, out, remote, vacation, other）
        note: 備考
        channel_id: 投稿されたチャンネルのID
        ts: Slackメッセージのタイムスタンプ
        
    Raises:
        Exception: Firestore書き込みに失敗した場合
    """
    try:
        # ドキュメントID: {workspace_id}_{user_id}_{date}
        doc_id = f"{workspace_id}_{user_id}_{date}"
        doc_ref = db.collection(get_collection_name("attendance")).document(doc_id)
        
        data = {
            "workspace_id": workspace_id,
            "user_id": user_id,
            "email": email or "",
            "date": date,
            "status": status,
            "note": note,
            "channel_id": channel_id,
            "ts": ts,
            "updated_at": firestore.SERVER_TIMESTAMP
        }
        doc_ref.set(data)
        logger.info(f"Saved attendance: {doc_id}")
    except Exception as e:
        logger.error(f"Error saving attendance record: {e}", exc_info=True)
        raise

def get_single_attendance_record(workspace_id: str, user_id: str, date: str) -> Optional[Dict[str, Any]]:
    """
    特定の日付・ユーザーの勤怠記録を取得します。
    
    Args:
        workspace_id: Slackワークスペースの一意ID
        user_id: SlackユーザーID
        date: 対象日（YYYY-MM-DD形式）
        
    Returns:
        勤怠記録の辞書（存在しない場合はNone）
        
    Raises:
        Exception: Firestore読み取りに失敗した場合（ログのみ、Noneを返却）
    """
    try:
        doc_id = f"{workspace_id}_{user_id}_{date}"
        doc = db.collection(get_collection_name("attendance")).document(doc_id).get()
        if doc.exists:
            return doc.to_dict()
        return None
    except Exception as e:
        logger.error(f"Error fetching single record: {e}", exc_info=True)
        return None

def get_user_history_from_db(
    workspace_id: str, 
    user_id: str, 
    email: Optional[str], 
    month_filter: str
) -> List[Dict[str, Any]]:
    """
    ユーザーの勤怠履歴を月単位で取得します。
    
    Args:
        workspace_id: Slackワークスペースの一意ID
        user_id: SlackユーザーID
        email: ユーザーのメールアドレス（優先的に使用）
        month_filter: 対象月（YYYY-MM形式、例: "2026-01"）
        
    Returns:
        勤怠記録の配列（日付の降順でソート済み）
        
    Raises:
        Exception: Firestore読み取りに失敗した場合（ログのみ、空配列を返却）
    """
    try:
        # workspace_idでフィルタリングを開始
        query = db.collection(get_collection_name("attendance")).where("workspace_id", "==", workspace_id)
        
        # emailが存在する場合は優先的に使用（複数デバイスでの同一性確保）
        if email:
            docs = query.where("email", "==", email).stream()
        else:
            docs = query.where("user_id", "==", user_id).stream()
        
        results = [d.to_dict() for d in docs]
        
        # クライアント側で月フィルタリング（Firestoreの制約回避）
        filtered = [r for r in results if r.get('date', '').startswith(month_filter)]
        
        # 日付の降順でソート（新しい順）
        return sorted(filtered, key=lambda x: x['date'], reverse=True)
    except Exception as e:
        logger.error(f"Error fetching user history: {e}", exc_info=True)
        return []

def delete_attendance_record_db(workspace_id: str, user_id: str, date: str) -> None:
    """
    勤怠レコードをFirestoreから削除します。
    
    Args:
        workspace_id: Slackワークスペースの一意ID
        user_id: SlackユーザーID
        date: 対象日（YYYY-MM-DD形式）
        
    Raises:
        Exception: Firestore削除に失敗した場合
    """
    try:
        doc_id = f"{workspace_id}_{user_id}_{date}"
        db.collection(get_collection_name("attendance")).document(doc_id).delete()
        logger.info(f"Deleted attendance record: {doc_id}")
    except Exception as e:
        logger.error(f"Error deleting record: {e}", exc_info=True)
        raise

def get_channel_members_with_section(workspace_id: Optional[str] = None) -> tuple[Dict[str, List[str]], str]:
    """
    課別メンバー設定をFirestoreから取得します。
    
    Args:
        workspace_id: Slackワークスペースの一意ID（将来的なマルチWS対応用、現在は未使用）
        
    Returns:
        タプル: (section_user_map辞書, updated_at文字列)
        - section_user_map: {セクションID: [ユーザーID配列]}
        - updated_at: ISO8601形式の更新日時（楽観的ロック用）
        
    Note:
        現状は全ワークスペース共通の設定を返します。
        将来的には workspace_id ごとに異なる設定を保存する想定です。
    """
    try:
        doc = db.collection(get_collection_name("system_metadata")).document("member_config").get()
        if not doc.exists:
            logger.info("Member config not found, returning empty configuration")
            return {}, "0"
        
        data = doc.to_dict()
        section_user_map = data.get("section_user_map", {})
        updated_at = data.get("updated_at", "0")
        
        return section_user_map, updated_at
    except Exception as e:
        logger.error(f"Error fetching channel members: {e}", exc_info=True)
        return {}, "0"

def save_channel_members_db(
    workspace_id: str, 
    channel_id: str, 
    section_user_map: Dict[str, List[str]], 
    client_version: Optional[str] = None
) -> str:
    """
    課別メンバー設定をFirestoreに保存します。
    
    Args:
        workspace_id: Slackワークスペースの一意ID
        channel_id: 対象チャンネルID（現状は未使用、将来の拡張用）
        section_user_map: {セクションID: [ユーザーID配列]}
        client_version: クライアント側が保持するバージョン（楽観的ロック用、現状は未使用）
        
    Returns:
        新しく生成されたバージョン文字列（ISO8601形式）
        
    Note:
        現状は楽観的ロックが機能していません。
        将来的にはclient_versionと保存済みバージョンを比較し、
        不一致の場合はCONCURRENCY_ERRORを発生させる必要があります。
        
    Raises:
        Exception: Firestore書き込みに失敗した場合
    """
    try:
        doc_ref = db.collection(get_collection_name("system_metadata")).document("member_config")
        new_version = datetime.datetime.now().isoformat()
        
        # TODO: 楽観的ロックの実装
        # if client_version:
        #     current_doc = doc_ref.get()
        #     if current_doc.exists and current_doc.to_dict().get("updated_at") != client_version:
        #         raise Exception("CONCURRENCY_ERROR: Configuration was updated by another user")
        
        doc_ref.set({
            "section_user_map": section_user_map,
            "updated_at": new_version,
            "workspace_id": workspace_id
        })
        logger.info(f"Updated member config version to {new_version} for workspace {workspace_id}")
        return new_version
    except Exception as e:
        logger.error(f"Error saving channel members: {e}", exc_info=True)
        raise

def get_today_records(workspace_id: str, date_str: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    特定の日付の全勤怠記録を取得します。
    
    Args:
        workspace_id: Slackワークスペースの一意ID
        date_str: 対象日（YYYY-MM-DD形式、省略時は今日）
        
    Returns:
        その日の全勤怠記録の配列
        
    Note:
        workspace_idでフィルタリングすることで、
        マルチワークスペース環境でのデータ混在を防ぎます。
    """
    try:
        target_date = date_str or datetime.datetime.now().strftime("%Y-%m-%d")
        
        # workspace_idとdateの複合クエリ
        docs = db.collection(get_collection_name("attendance"))\
                 .where("workspace_id", "==", workspace_id)\
                 .where("date", "==", target_date).stream()
        
        results = [d.to_dict() for d in docs]
        logger.info(f"Retrieved {len(results)} records for {target_date} in workspace {workspace_id}")
        return results
    except Exception as e:
        logger.error(f"Error fetching today's records: {e}", exc_info=True)
        return []

def get_attendance_records_by_sections(
    workspace_id: str, 
    target_date: str, 
    section_ids: List[str]
) -> List[Dict[str, Any]]:
    """
    指定されたセクション（課）に所属するメンバーの勤怠記録を取得します。
    
    Args:
        workspace_id: Slackワークスペースの一意ID
        target_date: 対象日（YYYY-MM-DD形式）
        section_ids: セクションIDの配列（例: ["sec_1", "sec_2"]）
        
    Returns:
        該当メンバーの勤怠記録配列
        
    Note:
        Firestoreの制約により、IN句は最大30件までです。
        セクションに所属するメンバーが30名を超える場合は追加の実装が必要です。
    """
    try:
        # メンバー設定を取得（TODO: workspace_id対応が必要）
        section_map, _ = get_channel_members_with_section(workspace_id)
        
        # 指定セクションに所属する全メンバーIDを集約
        member_ids = []
        for s_id in section_ids:
            member_ids.extend(section_map.get(s_id, []))
        
        if not member_ids:
            logger.info(f"No members found in sections {section_ids}")
            return []
        
        # Firestoreの制約: IN句は最大30件
        # 30件を超える場合は先頭30件のみ取得（暫定対応）
        if len(member_ids) > 30:
            logger.warning(f"Member count ({len(member_ids)}) exceeds Firestore IN clause limit (30). Truncating.")
            member_ids = member_ids[:30]
        
        docs = db.collection(get_collection_name("attendance"))\
                 .where("workspace_id", "==", workspace_id)\
                 .where("date", "==", target_date)\
                 .where("user_id", "in", member_ids)\
                 .stream()
        
        results = [d.to_dict() for d in docs]
        logger.info(f"Retrieved {len(results)} records for sections {section_ids} on {target_date}")
        return results
    except Exception as e:
        logger.error(f"Error fetching records by sections: {e}", exc_info=True)
        return []


# ==========================================
# ワークスペース管理（マルチテナント対応）
# ==========================================

def get_workspace_config(team_id: str) -> Optional[Dict[str, Any]]:
    """
    ワークスペース設定（bot_token、report_channel_idなど）を取得します。
    
    Args:
        team_id: Slackワークスペースの一意ID
        
    Returns:
        ワークスペース設定の辞書:
        {
            "team_id": "T01234567",
            "team_name": "Example Workspace",
            "bot_token": "xoxb-...",
            "report_channel_id": "C01234567",
            "installed_at": "2026-01-24T10:00:00"
        }
        存在しない場合はNone
    """
    try:
        doc = db.collection(get_collection_name("workspaces")).document(team_id).get()
        
        if not doc.exists:
            logger.warning(f"ワークスペース設定が見つかりません: {team_id}")
            return None
        
        data = doc.to_dict()
        logger.info(f"ワークスペース設定取得成功: {team_id}")
        return data
    except Exception as e:
        logger.error(f"ワークスペース設定取得エラー: {e}", exc_info=True)
        return None


def save_workspace_config(
    team_id: str,
    team_name: str,
    bot_token: str,
    report_channel_id: Optional[str] = None
) -> None:
    """
    ワークスペース設定を保存します（OAuth インストール時に使用）。
    
    Args:
        team_id: Slackワークスペースの一意ID
        team_name: ワークスペース名
        bot_token: Bot User OAuth Token
        report_channel_id: レポート送信先チャンネルID（オプション）
        
    Raises:
        Exception: Firestore書き込みに失敗した場合
    """
    try:
        doc_ref = db.collection(get_collection_name("workspaces")).document(team_id)
        
        doc_ref.set({
            "team_id": team_id,
            "team_name": team_name,
            "bot_token": bot_token,
            "report_channel_id": report_channel_id or "",
            "installed_at": firestore.SERVER_TIMESTAMP,
            "updated_at": firestore.SERVER_TIMESTAMP
        }, merge=True)
        
        logger.info(f"ワークスペース設定保存成功: {team_id} ({team_name})")
    except Exception as e:
        logger.error(f"ワークスペース設定保存エラー: {e}", exc_info=True)
        raise