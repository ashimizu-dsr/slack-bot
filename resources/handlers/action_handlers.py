"""
Action Handlers - メッセージ上のボタン押下やグローバルショートカットの処理
"""
import datetime
import logging
import json
from resources.views.modal_views import (
    create_attendance_modal_view, 
    create_history_modal_view,
    create_member_settings_modal_view,
    create_delete_confirm_modal
)
from resources.shared.db import (
    get_single_attendance_record, 
    get_channel_members_with_section
)

# ロガーの統一
logger = logging.getLogger(__name__)

def register_action_handlers(app, attendance_service, notification_service) -> None:

    # ==========================================
    # 1. 勤怠の「修正」ボタン押下
    # ==========================================
    @app.action("open_update_attendance")
    def handle_open_update_modal(ack, body, client):
        """修正モーダルを表示する（不具合③対策：チェックロジックの適正化）"""
        ack()
        user_id = body["user"]["id"]
        workspace_id = body["team"]["id"]
        trigger_id = body["trigger_id"]
        date = body["actions"][0].get("value")
        
        channel_id = body["container"]["channel_id"]
        message_ts = body["container"]["message_ts"]

        try:
            # Firestoreから既存レコードを取得
            record = get_single_attendance_record(workspace_id, user_id, date)
            
            # --- 本人チェックの修正 ---
            # レコードが存在し、かつ所有者が自分ではない場合のみエラーを出す
            if record and record.get("user_id") != user_id:
                client.chat_postEphemeral(
                    channel=channel_id,
                    user=user_id,
                    text="⚠️ この操作は打刻した本人しか行えません。"
                )
                return

            # 上書きに必要な情報を metadata に詰め込む
            private_metadata = json.dumps({
                "date": date,
                "channel_id": channel_id,
                "message_ts": message_ts
            })
            
            initial_data = {
                "user_id": user_id,
                "date": date,
                "status": record.get("status") if record else None,
                "note": record.get("note", "") if record else ""
            }

            # モーダル表示
            view = create_attendance_modal_view(initial_data=initial_data, is_fixed_date=True)
            view["private_metadata"] = private_metadata 
            
            client.views_open(trigger_id=trigger_id, view=view)
            
        except Exception as e:
            logger.error(f"修正モーダル表示失敗: {e}", exc_info=True)

    # ==========================================
    # 2. 勤怠の「取消」ボタン押下
    # ==========================================
    @app.action("delete_attendance_request")
    def open_delete_confirm_modal_handler(ack, body, client):
        """確認用モーダルを開く（本人チェック付き）"""
        ack()
        user_id = body["user"]["id"]
        workspace_id = body["team"]["id"]
        trigger_id = body["trigger_id"]
        date = body["actions"][0]["value"]
        channel_id = body["container"]["channel_id"]

        try:
            record = get_single_attendance_record(workspace_id, user_id, date)
            
            if record and record.get("user_id") != user_id:
                client.chat_postEphemeral(
                    channel=channel_id,
                    user=user_id,
                    text="⚠️ この操作は打刻した本人しか行えません。"
                )
                return

            metadata_dict = {
                "date": date,
                "message_ts": body["container"]["message_ts"],
                "channel_id": channel_id
            }

            view = create_delete_confirm_modal(date) 
            view["private_metadata"] = json.dumps(metadata_dict)

            client.views_open(trigger_id=trigger_id, view=view)
        except Exception as e:
            logger.error(f"取消モーダル表示失敗: {e}")

    # ==========================================
    # 3. 削除確認モーダルの「送信」
    # ==========================================
    @app.view("delete_attendance_confirm_callback")
    def handle_delete_confirm_submit(ack, body, client, view):
        ack()
        user_id = body["user"]["id"]
        workspace_id = body["team"]["id"]
        
        metadata = json.loads(view.get("private_metadata", "{}"))
        date = metadata.get("date")
        target_ts = metadata.get("message_ts")
        channel_id = metadata.get("channel_id")

        try:
            attendance_service.delete_attendance(workspace_id, user_id, date)
            
            client.chat_update(
                channel=channel_id,
                ts=target_ts,
                blocks=[{
                    "type": "context",
                    "elements": [
                        {"type": "mrkdwn", "text": f"ⓘ <@{user_id}>さんの {date} の勤怠連絡を取り消しました"}
                    ]
                }],
                text="勤怠を取り消しました"
            )
        except Exception as e:
            logger.error(f"取消処理失敗: {e}", exc_info=True)

    # ==========================================
    # 4. メンバー設定
    # ==========================================
    @app.action("open_member_settings")
    @app.shortcut("open_member_setup_modal")
    def handle_member_setup(ack, body, client):
        ack()
        trigger_id = body["trigger_id"]
        try:
            initial_members, version = get_channel_members_with_section()
            channel_id = body.get("channel", {}).get("id") or \
                         (json.loads(body.get("view", {}).get("private_metadata", "{}")).get("channel_id"))
            
            if not channel_id:
                from constants import ATTENDANCE_CHANNEL_ID
                channel_id = ATTENDANCE_CHANNEL_ID

            view = create_member_settings_modal_view(
                channel_id=channel_id, 
                initial_members_by_section=initial_members,
                version=version
            )
            client.views_open(trigger_id=trigger_id, view=view)
        except Exception as e:
            logger.error(f"メンバー設定表示失敗: {e}", exc_info=True)

    # ==========================================
    # 5. 勤怠履歴（不具合①対策：workspace_idの追加）
    # ==========================================
    @app.shortcut("open_kintai_history")
    def handle_history_shortcut(ack, body, client):
        """個人の勤怠履歴をモーダルで表示する"""
        ack()
        user_id = body["user"]["id"]
        workspace_id = body["team"]["id"] # 追加
        
        try:
            today = datetime.date.today()
            month_str = today.strftime("%Y-%m")
            
            # 修正ポイント：workspace_id を引数に追加
            history = attendance_service.get_user_history(
                workspace_id=workspace_id, 
                user_id=user_id, 
                month_filter=month_str
            )
            
            view = create_history_modal_view(
                history_records=history,
                selected_year=str(today.year),
                selected_month=f"{today.month:02d}",
                user_id=user_id # 追加した引数
            )
            client.views_open(trigger_id=body["trigger_id"], view=view)
        except Exception as e:
            logger.error(f"履歴表示失敗: {e}", exc_info=True)

    # ==========================================
    # 6. 履歴モーダルの年月変更（不具合①対策）
    # ==========================================
    @app.action("history_year_change")
    @app.action("history_month_change")
    def handle_history_filter_update(ack, body, client):
        ack()
        workspace_id = body["team"]["id"] # 追加
        try:
            metadata = json.loads(body["view"]["private_metadata"])
            target_user_id = metadata.get("target_user_id")
            
            state_values = body["view"]["state"]["values"]
            selected_year = state_values["history_filter"]["history_year_change"]["selected_option"]["value"]
            selected_month = state_values["history_filter"]["history_month_change"]["selected_option"]["value"]
            
            month_filter = f"{selected_year}-{selected_month}"
            
            # 修正ポイント：workspace_id を引数に追加
            history = attendance_service.get_user_history(
                workspace_id=workspace_id, 
                user_id=target_user_id, 
                month_filter=month_filter
            )
            
            updated_view = create_history_modal_view(
                history_records=history,
                selected_year=selected_year,
                selected_month=selected_month,
                user_id=target_user_id
            )
            
            client.views_update(
                view_id=body["view"]["id"],
                hash=body["view"]["hash"],
                view=updated_view
            )
        except Exception as e:
            logger.error(f"履歴フィルタ更新失敗: {e}", exc_info=True)