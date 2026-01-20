"""
Attendance Service - 勤怠管理のビジネスロジック。
"""

from typing import List, Dict, Any, Optional
from dataclasses import dataclass
import logging

from resources.shared.db import (
    save_attendance_record, 
    get_single_attendance_record,
    get_user_history_from_db,
    get_today_records,
    delete_attendance_record_db,
    get_channel_members_with_section
)
from resources.shared.errors import ValidationError

logger = logging.getLogger(__name__)

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
    action_taken: str = "save"  # 追加: どの処理が行われたか(save/delete)を識別

class AttendanceService:
    """勤怠操作を管理するサービス"""

    def __init__(self):
        self.valid_statuses = ["late", "early_leave", "out", "remote", "vacation", "other"]

    def save_attendance(self, workspace_id: str, user_id: str, email: Optional[str], date: str, status: str, 
                        note: str = "", channel_id: str = "", ts: str = "") -> AttendanceRecord:
        """勤怠記録を保存"""
        safe_email = email if email is not None else ""
        record = AttendanceRecord(workspace_id, user_id, safe_email, date, status, note, channel_id, ts, action_taken="save")
        
        self._validate_record(record)

        try:
            save_attendance_record(workspace_id, user_id, safe_email, date, status, note, channel_id, ts)
            logger.info(f"勤怠記録保存成功: User={user_id}, Date={date}")
            return record
        except Exception as e:
            logger.error(f"Firestore保存失敗: {e}")
            raise Exception("データベースへの保存に失敗しました。")

    def delete_attendance(self, workspace_id: str, user_id: str, date: str, silent: bool = False) -> bool:
        """勤怠記録を削除"""
        existing = get_single_attendance_record(workspace_id, user_id, date)
        if not existing:
            if silent: return True
            raise ValidationError("削除対象が見つかりません", "⚠️ 削除対象の勤怠記録が見つかりません。")

        try:
            delete_attendance_record_db(workspace_id, user_id, date)
            logger.info(f"勤怠記録を削除しました: User={user_id}, Date={date}")
            return True
        except Exception as e:
            logger.error(f"削除失敗: {e}")
            return False

    def get_user_history(self, workspace_id: str, user_id: str, month_filter: Optional[str] = None, email: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        ユーザーの履歴を取得
        修正ポイント: Action Handlerからの呼び出しに合わせて workspace_id を追加
        """
        try:
            clean_month = month_filter.strip() if month_filter else ""
            return get_user_history_from_db(
                workspace_id=workspace_id, # DB関数側が対応している前提
                user_id=user_id,
                email=email,
                month_filter=clean_month
            )
        except Exception as e:
            logger.error(f"履歴取得エラー: {e}")
            return []

    def get_daily_report_data(self, workspace_id: str, date_str: str, channel_id: str) -> Dict[str, List[Dict[str, Any]]]:
        report_data = {}
        try:
            section_user_map, _ = get_channel_members_with_section()
            all_today_records = get_today_records(date_str)
            attendance_lookup = {r['user_id']: r for r in all_today_records}

            for section, user_ids in section_user_map.items():
                report_data[section] = []
                for u_id in user_ids:
                    if u_id in attendance_lookup:
                        record = attendance_lookup[u_id]
                        report_data[section].append({
                            "user_id": u_id,
                            "status": record.get('status'),
                            "note": record.get('note') or ""
                        })
            return report_data
        except Exception as e:
            logger.error(f"レポートデータ一括取得失敗: {e}")
            return {}

    def _validate_record(self, record: AttendanceRecord) -> None:
        if not record.workspace_id: raise ValidationError("Workspace ID missing", "⚠️ ワークスペース情報が正しくありません。")
        if not record.user_id: raise ValidationError("User ID missing", "⚠️ ユーザー情報が正しくありません。")
        if not record.date: raise ValidationError("Date missing", "⚠️ 日付が指定されていません。")
        if not record.status: raise ValidationError("Status missing", "⚠️ 区分を選択してください。")
        if record.status not in self.valid_statuses:
            raise ValidationError(f"Invalid status: {record.status}", "⚠️ 選択された区分が正しくありません。")

    def process_ai_extraction_result(self, workspace_id: str, user_id: str, email: str, 
                                     extracted_data: Dict[str, Any], 
                                     channel_id: str = "", ts: str = "") -> List[AttendanceRecord]:
        """
        AIの抽出結果を処理
        不具合②対策: 打ち消し線があっても内容があるなら保存として扱う
        """
        processed_records = []
        all_items = []
        if extracted_data:
            # 辞書自体が1つのデータの場合と、リスト形式の場合の両方を考慮
            base_item = {k: v for k, v in extracted_data.items() if k != "_additional_attendances"}
            if base_item.get("date"):
                all_items.append(base_item)
            if "_additional_attendances" in extracted_data:
                all_items.extend(extracted_data["_additional_attendances"])

        for item in all_items:
            try:
                date = item.get("date")
                action = item.get("action", "save")
                status = item.get("status")

                # 【重要】不具合②対策ロジック
                # AIがdeleteと判定していても、status（区分）が抽出されている場合は「書き換え」とみなす
                if action == "delete" and status and status in self.valid_statuses:
                    logger.info(f"打ち消し線後の訂正を検知: {date} のアクションを save に変更します。")
                    action = "save"

                if action == "delete":
                    success = self.delete_attendance(workspace_id, user_id, date, silent=True)
                    if success:
                        # 削除成功時も通知を出せるよう、ダミーレコードをリストに入れる
                        processed_records.append(AttendanceRecord(
                            workspace_id, user_id, email, date, status="deleted", action_taken="delete"
                        ))
                else:
                    record = self.save_attendance(
                        workspace_id=workspace_id,
                        user_id=user_id,
                        email=email,
                        date=date,
                        status=status or "other",
                        note=item.get("note", ""),
                        channel_id=channel_id,
                        ts=ts
                    )
                    processed_records.append(record)
            except Exception as e:
                logger.error(f"AI抽出結果の個別処理中にエラーが発生 ({item.get('date')}): {e}")
                continue
        
        return processed_records