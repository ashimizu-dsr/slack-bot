"""
勤怠管理サービス

このモジュールは、勤怠記録の保存・更新・削除・取得などの
ビジネスロジックを提供します。
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
from resources.shared.errors import ValidationError, AuthorizationError

logger = logging.getLogger(__name__)


@dataclass
class AttendanceRecord:
    """
    勤怠記録のデータ構造を表すデータクラス。
    
    Attributes:
        workspace_id: Slackワークスペースの一意ID
        user_id: SlackユーザーID
        email: ユーザーのメールアドレス
        date: 対象日（YYYY-MM-DD形式）
        status: 勤怠区分（late, early_leave, out, remote, vacation, other）
        note: 備考
        channel_id: 投稿されたチャンネルID
        ts: Slackメッセージのタイムスタンプ
        action_taken: 実行されたアクション（save/delete）
    """
    workspace_id: str
    user_id: str
    email: str 
    date: str
    status: str
    note: str = ""
    channel_id: Optional[str] = None
    ts: Optional[str] = None
    action_taken: str = "save"

class AttendanceService:
    """
    勤怠操作を管理するサービスクラス。
    
    勤怠記録のCRUD操作、検証、AI抽出結果の処理などを提供します。
    """

    def __init__(self):
        """
        サービスの初期化。
        
        有効なステータスのリストを定義します（最新ルール 2026-01-27）。
        """
        self.valid_statuses = [
            "vacation", "vacation_am", "vacation_pm", "vacation_hourly",
            "late", "late_delay",
            "early_leave",
            "out",
            "remote", "shift",
            "other"
        ]

    def save_attendance(
        self, 
        workspace_id: str, 
        user_id: str, 
        email: Optional[str], 
        date: str, 
        status: str, 
        note: str = "", 
        channel_id: str = "", 
        ts: str = ""
    ) -> AttendanceRecord:
        """
        勤怠記録をFirestoreに保存します。
        
        Args:
            workspace_id: Slackワークスペースの一意ID
            user_id: SlackユーザーID
            email: ユーザーのメールアドレス（取得できない場合はNone）
            date: 対象日（YYYY-MM-DD形式）
            status: 勤怠区分
            note: 備考
            channel_id: 投稿されたチャンネルID
            ts: Slackメッセージのタイムスタンプ
            
        Returns:
            保存されたAttendanceRecordオブジェクト
            
        Raises:
            ValidationError: 入力値が不正な場合
            Exception: データベース保存に失敗した場合
        """
        safe_email = email if email is not None else ""
        record = AttendanceRecord(
            workspace_id, user_id, safe_email, date, status, note, 
            channel_id, ts, action_taken="save"
        )
        
        # 検証を実行
        self._validate_record(record)

        try:
            save_attendance_record(
                workspace_id, user_id, safe_email, date, status, note, channel_id, ts
            )
            logger.info(f"勤怠記録保存成功: User={user_id}, Date={date}, Status={status}")
            return record
        except Exception as e:
            logger.error(f"Firestore保存失敗: {e}", exc_info=True)
            raise Exception("データベースへの保存に失敗しました。")

    def get_specific_date_record(
        self, 
        workspace_id: str, 
        user_id: str, 
        date: str
    ) -> Optional[Dict[str, Any]]:
        """
        特定の日付・ユーザーの勤怠記録を取得します。
        
        Args:
            workspace_id: Slackワークスペースの一意ID
            user_id: SlackユーザーID
            date: 対象日（YYYY-MM-DD形式）
            
        Returns:
            勤怠記録の辞書（存在しない場合はNone）
            
        Note:
            この関数はmodal_handlers.pyで使用するために追加されました。
        """
        return get_single_attendance_record(workspace_id, user_id, date)

    def delete_attendance(
        self, 
        workspace_id: str, 
        user_id: str, 
        date: str, 
        silent: bool = False
    ) -> bool:
        """
        勤怠記録をFirestoreから削除します。
        
        Args:
            workspace_id: Slackワークスペースの一意ID
            user_id: SlackユーザーID
            date: 対象日（YYYY-MM-DD形式）
            silent: Trueの場合、レコードが存在しなくてもエラーを発生させない
            
        Returns:
            削除成功の場合True、失敗の場合False
            
        Raises:
            ValidationError: レコードが存在せず、silentがFalseの場合
        """
        existing = get_single_attendance_record(workspace_id, user_id, date)
        if not existing:
            if silent:
                logger.info(f"削除対象が存在しません（silent=True）: User={user_id}, Date={date}")
                return True
            raise ValidationError(
                "削除対象が見つかりません", 
                "⚠️ 削除対象の勤怠記録が見つかりません。"
            )

        try:
            delete_attendance_record_db(workspace_id, user_id, date)
            logger.info(f"勤怠記録を削除しました: User={user_id}, Date={date}")
            return True
        except Exception as e:
            logger.error(f"削除失敗: {e}", exc_info=True)
            return False

    def get_user_history(
        self, 
        workspace_id: str, 
        user_id: str, 
        month_filter: Optional[str] = None, 
        email: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        ユーザーの勤怠履歴を月単位で取得します。
        
        Args:
            workspace_id: Slackワークスペースの一意ID
            user_id: SlackユーザーID
            month_filter: 対象月（YYYY-MM形式、例: "2026-01"）
            email: ユーザーのメールアドレス（優先的に使用）
            
        Returns:
            勤怠記録の配列（日付の降順でソート済み）
        """
        try:
            clean_month = month_filter.strip() if month_filter else ""
            history = get_user_history_from_db(
                workspace_id=workspace_id,
                user_id=user_id,
                email=email,
                month_filter=clean_month
            )
            logger.info(f"履歴取得成功: User={user_id}, Month={clean_month}, Count={len(history)}")
            return history
        except Exception as e:
            logger.error(f"履歴取得エラー: {e}", exc_info=True)
            return []

    def get_daily_report_data(
        self, 
        workspace_id: str, 
        date_str: str, 
    ) -> Dict[str, Any]:
        """
        日次レポート用のデータを「設定されたグループ」に基づいて取得します。
        
        Note:
            v2.3以降、この関数は使用されていません。
            レポート送信はnotification_service.send_daily_reportで直接行われます。
        """
        from services.workspace_service import WorkspaceService # 循環参照回避
        from services.group_service import GroupService
        ws_service = WorkspaceService()
        group_service = GroupService()
        
        report_data = {
            "groups": []
        }

        try:
            # 1. モーダルで設定したグループ一覧を取得
            groups = group_service.get_all_groups(workspace_id)
            
            # 2. その日の全勤怠記録を一括取得
            all_today_records = get_today_records(workspace_id, date_str)
            attendance_lookup = {r['user_id']: r for r in all_today_records}

            # 3. グループごとにメンバーの勤怠を紐付け
            for group in groups:
                group_results = []
                for u_id in group.get("member_ids", []):
                    if u_id in attendance_lookup:
                        record = attendance_lookup[u_id]
                        group_results.append({
                            "user_id": u_id,
                            "status": record.get('status'),
                            "note": record.get('note') or ""
                        })
                
                # グループ名と結果を格納
                report_data["groups"].append({
                    "name": group["name"],
                    "results": group_results,
                    "admin_ids": group.get("admin_ids", [])
                })
            
            return report_data
        except Exception as e:
            logger.error(f"レポートデータ構築失敗: {e}", exc_info=True)
            return {"groups": []}

    def _validate_record(self, record: AttendanceRecord) -> None:
        """
        勤怠記録の妥当性を検証します。
        
        Args:
            record: 検証対象のAttendanceRecord
            
        Raises:
            ValidationError: 必須フィールドが欠落している、または不正な値が含まれている場合
        """
        if not record.workspace_id:
            raise ValidationError("Workspace ID missing", "⚠️ ワークスペース情報が正しくありません。")
        if not record.user_id:
            raise ValidationError("User ID missing", "⚠️ ユーザー情報が正しくありません。")
        if not record.date:
            raise ValidationError("Date missing", "⚠️ 日付が指定されていません。")
        if not record.status:
            raise ValidationError("Status missing", "⚠️ 区分を選択してください。")
        if record.status not in self.valid_statuses:
            raise ValidationError(
                f"Invalid status: {record.status}", 
                "⚠️ 選択された区分が正しくありません。"
            )

    def process_ai_extraction_result(
        self, 
        workspace_id: str, 
        user_id: str, 
        email: str, 
        extracted_data: Dict[str, Any], 
        channel_id: str = "", 
        ts: str = ""
    ) -> List[AttendanceRecord]:
        """
        AIの抽出結果を処理し、勤怠記録の保存または削除を実行します。
        
        Args:
            workspace_id: Slackワークスペースの一意ID
            user_id: SlackユーザーID
            email: ユーザーのメールアドレス
            extracted_data: nlp_service.extract_attendance_from_text()の戻り値
            channel_id: 投稿されたチャンネルID
            ts: Slackメッセージのタイムスタンプ
            
        Returns:
            処理されたAttendanceRecordの配列（削除の場合はaction_taken="delete"のダミーレコード）
            
        Note:
            打ち消し線の処理ロジック:
            - AIが action="delete" と判定しても、有効な status が抽出されている場合は
              「訂正」とみなして action を "save" に変更します。
            - これにより「~8:00出勤~ 10:00出勤」のようなメッセージを
              「削除→新規保存」ではなく「更新」として扱います。
        """
        processed_records = []
        all_items = []
        
        if extracted_data:
            # メインアイテムを追加
            base_item = {k: v for k, v in extracted_data.items() if k != "_additional_attendances"}
            if base_item.get("date"):
                all_items.append(base_item)
            
            # 追加アイテム（複数日対応）を追加
            if "_additional_attendances" in extracted_data:
                all_items.extend(extracted_data["_additional_attendances"])

        for item in all_items:
            try:
                date = item.get("date")
                action = item.get("action", "save")
                status = item.get("status")

                # 【打ち消し線の後処理ロジック】
                # AIが delete と判定していても、有効なステータスがある場合は save に変更
                if action == "delete" and status and status in self.valid_statuses:
                    logger.info(
                        f"打ち消し線後の訂正を検知: {date} のアクションを save に変更します。"
                        f"(Status={status})"
                    )
                    action = "save"

                if action == "delete":
                    # 削除処理
                    success = self.delete_attendance(workspace_id, user_id, date, silent=True)
                    if success:
                        # 削除成功時も通知を出せるよう、ダミーレコードをリストに入れる
                        processed_records.append(AttendanceRecord(
                            workspace_id, user_id, email, date, 
                            status="deleted", action_taken="delete"
                        ))
                else:
                    # 保存処理
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
                logger.error(
                    f"AI抽出結果の個別処理中にエラーが発生 ({item.get('date')}): {e}",
                    exc_info=True
                )
                continue
        
        return processed_records

    def process_historical_message(
        self,
        workspace_id: str,
        user_id: str,
        email: str,
        text: str,
        channel_id: str,
        ts: str
    ) -> bool:
        """
        過去メッセージから勤怠情報を解析・保存します（通知なし）。
        
        Args:
            workspace_id: Slackワークスペースの一意ID
            user_id: SlackユーザーID
            email: ユーザーのメールアドレス
            text: メッセージテキスト
            channel_id: チャンネルID
            ts: メッセージのタイムスタンプ
            
        Returns:
            勤怠情報が抽出・保存された場合True、それ以外False
            
        Note:
            この関数はBot参加時の過去ログ遡り専用です。
            通知は一切行いません。
        """
        try:
            from resources.services.nlp_service import extract_attendance_from_text
            
            logger.info(f"[過去ログ] AI解析開始: User={user_id}, Text='{text[:30]}...', TS={ts}")
            
            # AI解析実行（過去ログの場合はメッセージのタイムスタンプを渡す）
            extraction = extract_attendance_from_text(
                text, 
                team_id=workspace_id, 
                user_id=user_id,
                message_ts=ts
            )
            
            if not extraction:
                logger.info(f"[過去ログ] AI解析結果: 勤怠情報なし (User={user_id})")
                return False
            
            logger.info(
                f"[過去ログ] AI解析成功: User={user_id}, "
                f"Date={extraction.get('date')}, Status={extraction.get('status')}, "
                f"Action={extraction.get('action')}"
            )
            
            # 抽出結果を処理
            processed_records = self.process_ai_extraction_result(
                workspace_id=workspace_id,
                user_id=user_id,
                email=email,
                extracted_data=extraction,
                channel_id=channel_id,
                ts=ts
            )
            
            if processed_records:
                for record in processed_records:
                    logger.info(
                        f"[過去ログ] DB保存完了: User={user_id}, "
                        f"Date={record.date}, Status={record.status}, "
                        f"Action={record.action_taken}"
                    )
            
            return len(processed_records) > 0
            
        except Exception as e:
            logger.error(f"[過去ログ] 処理エラー: User={user_id}, Error={e}", exc_info=True)
            return False