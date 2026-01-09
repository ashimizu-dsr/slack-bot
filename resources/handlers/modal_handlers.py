"""
Modal Handlers - モーダル内のフォーム送信(Submit)を処理
"""
import json
from shared.logging_config import get_logger
from shared.db import save_channel_members_db

logger = get_logger(__name__)

def register_modal_handlers(app, attendance_service, notification_service) -> None:

    # ==========================================
    # 1. 勤怠入力モーダル：「保存」押下
    # ==========================================
    @app.view("attendance_submit")
    def handle_attendance_save(ack, body, client, view):
        ack()
        user_id = body["user"]["id"]
        workspace_id = body["team"]["id"]
        metadata = json.loads(view.get("private_metadata", "{}"))
        vals = view["state"]["values"]
        
        date = metadata.get("date")
        status = vals["status_block"]["status_select"]["selected_option"]["value"]
        note = vals["note_block"]["note_input"]["value"] or ""

        try:
            existing = attendance_service.get_specific_date_record(workspace_id, user_id, date)
            record = attendance_service.save_attendance(
                workspace_id=workspace_id, user_id=user_id,
                date=date, status=status, note=note,
                channel_id=existing.get("channel_id"), ts=existing.get("ts")
            )
            notification_service.notify_attendance_change(
                record=record, is_update=True,
                channel=existing.get("channel_id"), thread_ts=existing.get("ts")
            )
        except Exception as e:
            logger.error(f"勤怠保存失敗: {e}")

    # ==========================================
    # 2. メンバー設定モーダル：「保存」押下
    # ==========================================
    @app.view("member_settings_submit")
    def handle_member_settings_save(ack, body, view):
        workspace_id = body["team"]["id"]
        metadata = json.loads(view.get("private_metadata", "{}"))
        vals = view["state"]["values"]
        sections = ["sec_1", "sec_2", "sec_3", "sec_4", "sec_5", "sec_6", "sec_7", "sec_finance"]
        
        settings = {}
        for s in sections:
            block_id = f"user_select_block_{s}"
            if block_id in vals:
                settings[s] = vals[block_id]["user_select"].get("selected_users", [])

        try:
            save_channel_members_db(
                workspace_id=workspace_id,
                channel_id=metadata.get("channel_id"),
                section_user_map=settings,
                client_version=metadata.get("version")
            )
            ack()
        except Exception as e:
            if "CONCURRENCY_ERROR" in str(e):
                ack(response_action="errors", errors={"user_select_block_sec_1": "⚠️ 他の管理者が更新しました。開き直してください。"})
            else:
                ack()