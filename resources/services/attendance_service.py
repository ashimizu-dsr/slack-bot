"""
Attendance Service - 勤怠管理のビジネスロジック。
データの検証、Firestore保存の依頼、履歴の取得を担当します。
"""

from typing import List, Dict, Any, Optional
from dataclasses import dataclass
import logging

# db.py からクラウド対応済みの関数をインポート
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

class AttendanceService:
    """勤怠操作を管理するサービス"""

    def __init__(self):
        # 有効なステータスリスト
        self.valid_statuses = ["late", "early_leave", "out", "remote", "vacation", "other"]

    def save_attendance(self, workspace_id: str, user_id: str, email: Optional[str], date: str, status: str, 
                        note: str = "", channel_id: str = "", ts: str = "") -> AttendanceRecord:
        """勤怠記録を保存（新規・更新の両方に対応）"""
        
        safe_email = email if email is not None else ""
        record = AttendanceRecord(workspace_id, user_id, safe_email, date, status, note, channel_id, ts)
        
        # バリデーションの実行
        self._validate_record(record)

        try:
            # db.py 経由で Firestore に保存
            save_attendance_record(workspace_id, user_id, safe_email, date, status, note, channel_id, ts)
            logger.info(f"勤怠記録保存成功: User={user_id}, Date={date}")
            return record
        except Exception as e:
            logger.error(f"Firestore保存失敗: {e}")
            raise Exception("データベースへの保存に失敗しました。")

    def get_specific_date_record(self, workspace_id: str, user_id: str, date: str) -> Optional[Dict[str, Any]]:
        """特定の日付の記録を1件取得"""
        try:
            return get_single_attendance_record(workspace_id, user_id, date)
        except Exception as e:
            logger.error(f"データ取得失敗: {e}")
            return None

    def delete_attendance(self, workspace_id: str, user_id: str, date: str, silent: bool = False) -> bool:
        """
        勤怠記録を削除
        silent=True の場合、対象が存在しなくても例外を投げずに正常終了を返す（AI解析用）
        """
        existing = get_single_attendance_record(workspace_id, user_id, date)
        if not existing:
            if silent:
                return True
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
        ユーザーの履歴を取得（名寄せ対応）
        不具合①対策：filter文字列のトリミング処理を追加
        """
        try:
            # 空文字やNoneの揺れを吸収
            clean_month = month_filter.strip() if month_filter else ""
            return get_user_history_from_db(
                user_id=user_id,
                email=email,
                month_filter=clean_month
            )
        except Exception as e:
            logger.error(f"履歴取得エラー: {e}")
            return []

    def get_daily_report(self, date_str: Optional[str] = None) -> List[Dict[str, Any]]:
        """指定日の全ユーザーの状況を取得"""
        return get_today_records(date_str)

    def get_daily_report_data(self, workspace_id: str, date_str: str, channel_id: str) -> Dict[str, List[Dict[str, Any]]]:
        """指定日の全課の勤怠データを一括取得（Firestore対応版）"""
        report_data = {}
        
        try:
            section_user_map, _ = get_channel_members_with_section()
            all_today_records = self.get_daily_report(date_str)
            attendance_lookup = {r['user_id']: r for r in all_today_records}

            for section, user_ids in section_user_map.items():
                if section not in report_data:
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

    def process_ai_extraction_result(self, workspace_id: str, user_id: str, email: str, 
                                     extracted_data: Dict[str, Any], 
                                     channel_id: str = "", ts: str = "") -> List[AttendanceRecord]:
        """
        AIの抽出結果（複数日対応）をループで回し、保存または削除を自動実行する
        """
        processed_records = []
        all_items = []
        if extracted_data:
            all_items.append(extracted_data)
            if "_additional_attendances" in extracted_data:
                all_items.extend(extracted_data["_additional_attendances"])

        for item in all_items:
            try:
                date = item.get("date")
                action = item.get("action", "save") # デフォルトは保存

                if action == "delete":
                    # AIが「打ち消し線等による完全削除」と判断した日付を削除
                    # 既にデータがない場合も考慮し silent=True に設定
                    logger.info(f"AIによる自動削除を実行: User={user_id}, Date={date}")
                    self.delete_attendance(workspace_id, user_id, date, silent=True)
                else:
                    # 通常の保存（不具合②対策：打ち消し線があってもaction=saveならこちらに来る）
                    record = self.save_attendance(
                        workspace_id=workspace_id,
                        user_id=user_id,
                        email=email,
                        date=date,
                        status=item.get("status", "other"),
                        note=item.get("note", ""),
                        channel_id=channel_id,
                        ts=ts
                    )
                    processed_records.append(record)
            except Exception as e:
                # 複数日の場合、1件のエラーで全停止させない
                logger.error(f"AI抽出結果の個別処理中にエラーが発生 ({item.get('date')}): {e}")
                continue
        
        return processed_records