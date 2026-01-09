"""
Action Handlers - メッセージ上のボタン押下やグローバルショートカットの処理
"""
import datetime
import json
from shared.logging_config import get_logger
from views.modal_views import (
    create_attendance_modal_view, 
    create_history_modal_view,
    create_member_settings_modal_view
)
from shared.db import (
    get_single_attendance_record, 
    delete_attendance_record_db,
    get_channel_members_with_section
)

logger = get_logger(__name__)

def register_action_handlers(app, attendance_service, notification_service) -> None:

    # ==========================================
    # 1. 勤怠の「修正」ボタン押下
    # ==========================================
    @app.action("open_update_attendance") # AI投稿のボタンIDと合わせる
    def handle_open_update_modal(ack, body, client):
        """修正モーダルを表示する"""
        ack()
        user_id = body["user"]["id"]
        workspace_id = body["team"]["id"]
        trigger_id = body["trigger_id"]
        date = body["actions"][0].get("value")

        try:
            record = get_single_attendance_record(workspace_id, user_id, date)
            initial_data = {
                "user_id": user_id,
                "date": date,
                "status": record.get("status") if record else None,
                "note": record.get("note", "") if record else ""
            }
            view = create_attendance_modal_view(initial_data=initial_data, is_fixed_date=True)
            client.views_open(trigger_id=trigger_id, view=view)
        except Exception as e:
            logger.error(f"修正モーダル表示失敗: {e}")

    # ==========================================
    # 2. 勤怠の「取消」ボタン押下
    # ==========================================
    @app.action("delete_attendance_request") # AI投稿のボタンIDと合わせる
    def handle_attendance_delete(ack, body, client):
        """DBから削除し、メッセージを『削除済み』に書き換える"""
        ack()
        user_id = body["user"]["id"]
        workspace_id = body["team"]["id"]
        date = body["actions"][0]["value"]

        try:
            delete_attendance_record_db(workspace_id, user_id, date)
            
            # メッセージを「削除しました」という通知に更新
            client.chat_update(
                channel=body["channel"]["id"],
                ts=body["container"]["message_ts"],
                blocks=[
                    {"type": "context", "elements": [{"type": "mrkdwn", "text": f"ⓘ <@{user_id}>さんの {date} の勤怠連絡を削除しました。"}]}
                ]
            )
        except Exception as e:
            logger.error(f"取消処理失敗: {e}")

    # ==========================================
    # 3. メンバー設定ボタン or ショートカット
    # ==========================================
    @app.action("open_member_settings")
    @app.shortcut("open_member_setup_modal")
    def handle_member_setup(ack, body, client, logger): # loggerを忘れず追加
        """部署ごとのメンバー設定画面を開く"""
        ack()
        trigger_id = body["trigger_id"]

        try:
            # DBの定義に合わせて引数なしで呼び出し、タプルを展開する
            initial_members, version = get_channel_members_with_section()
            
            # チャンネルIDはモーダルの裏側に保持させるために取得
            channel_id = body.get("channel", {}).get("id") or \
                         (body.get("view", {}).get("private_metadata", {}).get("channel_id"))
            if not channel_id:
                from constants import ATTENDANCE_CHANNEL_ID
                channel_id = ATTENDANCE_CHANNEL_ID

            # モーダルを生成して表示
            view = create_member_settings_modal_view(
                channel_id=channel_id, 
                initial_members_by_section=initial_members,
                version=version
            )
            client.views_open(trigger_id=trigger_id, view=view)
            
        except Exception as e:
            # ログを出力して原因を追えるようにする
            logger.error(f"メンバー設定表示失敗: {e}", exc_info=True)

    # ==========================================
    # 4. 勤怠履歴（カレンダー）の表示
    # ==========================================
    @app.shortcut("open_kintai_history")
    def handle_history_shortcut(ack, body, client):
        """個人の勤怠履歴をモーダルで表示する"""
        ack()
        user_id = body["user"]["id"]
        
        try:
            today = datetime.date.today()
            history = attendance_service.get_user_history(user_id=user_id, month_filter=today.strftime("%Y-%m"))
            view = create_history_modal_view(
                history_records=history,
                selected_year=str(today.year),
                selected_month=f"{today.month:02d}"
            )
            client.views_open(trigger_id=body["trigger_id"], view=view)
        except Exception as e:
            logger.error(f"履歴表示失敗: {e}")