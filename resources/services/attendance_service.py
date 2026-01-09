"""
Attendance Service - 勤怠管理のビジネスロジック。
データの検証、DB保存の依頼、履歴の取得を担当します。
シングルワークスペース構成（将来のマルチ化を見据えたメール名寄せ対応版）
"""

from typing import List, Dict, Any, Optional
from dataclasses import dataclass
import sys
import os

# プロジェクトルートをパスに追加
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

# db.py からのインポート。整理後の関数名に合わせました。
from shared.db import (
    save_attendance_record, 
    get_single_attendance_record,
    get_user_history_from_db,  # get_attendances_by_email から変更
    get_today_records,
    delete_attendance_record_db,
    db_conn
)
from shared.logging_config import get_logger
from shared.errors import ValidationError

logger = get_logger(__name__)

@dataclass
class AttendanceRecord:
    """勤怠記録のデータ構造"""
    workspace_id: str
    user_id: str
    email: str 
    date: str
    status: str
    note: str = ""
    channel_id: Optional[str] = None
    ts: Optional[str] = None

class AttendanceService:
    """勤怠操作を管理するサービス"""

    def __init__(self):
        # 有効なステータスリスト
        self.valid_statuses = ["late", "early_leave", "out", "remote", "vacation", "other"]

    def save_attendance(self, workspace_id: str, user_id: str, email: Optional[str], date: str, status: str, 
                        note: str = "", channel_id: str = "", ts: str = "") -> AttendanceRecord:
        """勤怠記録を保存（新規・更新の両方に対応）"""
        
        # 型エラー回避: 引数の email (Optional) を内部用の safe_email (str) に変換
        safe_email = email if email is not None else ""
        
        # recordオブジェクト生成
        record = AttendanceRecord(workspace_id, user_id, safe_email, date, status, note, channel_id, ts)
        
        # バリデーションの実行
        self._validate_record(record)

        try:
            # DB側の引数に合わせて email を含めて保存
            save_attendance_record(workspace_id, user_id, safe_email, date, status, note, channel_id, ts)
            logger.info(f"勤怠記録保存成功: User={user_id}, Email={safe_email}, Date={date}")
            return record
        except Exception as e:
            logger.error(f"DB保存失敗: {e}")
            raise Exception("データベースへの保存に失敗しました。")

    def get_specific_date_record(self, workspace_id: str, user_id: str, date: str) -> Optional[Dict[str, Any]]:
        """特定の日付の記録を1件取得します。"""
        try:
            return get_single_attendance_record(workspace_id, user_id, date)
        except Exception as e:
            logger.error(f"データ取得失敗 (WS={workspace_id}): {e}")
            return None

    def delete_attendance(self, workspace_id: str, user_id: str, date: str) -> bool:
        """勤怠記録を削除します。"""
        existing = get_single_attendance_record(workspace_id, user_id, date)
        if not existing:
            raise ValidationError("削除対象が見つかりません", "⚠️ 削除対象の勤怠記録が見つかりません。")

        try:
            delete_attendance_record_db(workspace_id, user_id, date)
            logger.info(f"勤怠記録を削除しました: User={user_id}, Date={date}")
            return True
        except Exception as e:
            logger.error(f"削除失敗: {e}")
            return False

    def get_user_history(self, user_id: str, month_filter: Optional[str] = None, email: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        ユーザーの履歴を取得します（名寄せ対応）。
        db.py の get_user_history_from_db を内部で呼び出します。
        """
        try:
            # db.py 側の高度な検索ロジックを利用（email OR user_id）
            history = get_user_history_from_db(
                user_id=user_id,
                email=email,
                month_filter=month_filter or ""
            )
            return history
        except Exception as e:
            logger.error(f"履歴取得エラー: {e}")
            return []

    def get_daily_report(self, date_str: Optional[str] = None) -> List[Dict[str, Any]]:
        """指定日の全ユーザーの状況を取得（レポート用）"""
        return get_today_records(date_str)

    def get_daily_report_data(self, workspace_id: str, date_str: str, channel_id: str) -> Dict[str, List[Dict[str, Any]]]:
        """指定日の全課の勤怠データを一括取得（GLOBAL設定に基づく）。"""
        report_data = {}
        
        with db_conn() as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT 
                    m.section_id as section,
                    m.member_user_id as user_id,
                    a.status,
                    a.note
                FROM channel_members m
                LEFT JOIN attendance a ON 
                    m.member_user_id = a.user_id AND 
                    a.date = ?
                WHERE m.workspace_id = 'GLOBAL_WS'
                  AND m.channel_id = 'GLOBAL_CH'
            """, (date_str,))
            
            rows = cur.fetchall()
            for row in rows:
                sec = row['section'] or "未設定"
                if sec not in report_data:
                    report_data[sec] = []
                
                if row['status']:
                    report_data[sec].append({
                        "user_id": row['user_id'],
                        "status": row['status'],
                        "note": row['note'] or ""
                    })
        
        return report_data

    def _validate_record(self, record: AttendanceRecord) -> None:
        """入力データの整合性チェック"""
        if not record.workspace_id:
            raise ValidationError("Workspace ID missing", "⚠️ ワークスペース情報が正しく取得できませんでした。")
        if not record.user_id:
            raise ValidationError("User ID missing", "⚠️ ユーザー情報が正しく取得できませんでした。")
        
        if not record.date:
            raise ValidationError("Date missing", "⚠️ 日付が指定されていません。")
        if not record.status:
            raise ValidationError("Status missing", "⚠️ 区分を選択してください。")
        
        if record.status not in self.valid_statuses:
            raise ValidationError(f"Invalid status: {record.status}", "⚠️ 選択された区分が正しくありません。")

        if len(record.date) != 10:
            raise ValidationError(f"Invalid date format: {record.date}", "⚠️ 日付の形式が正しくありません。")