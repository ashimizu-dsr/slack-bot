"""
Action Handlers - メッセージ上のボタン押下やグローバルショートカットの処理
本人チェック & 動的チャンネルID & 上書き対応版
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
        """修正モーダルを表示する（本人チェック付き）"""
        ack()
        user_id = body["user"]["id"]
        workspace_id = body["team"]["id"]
        trigger_id = body["trigger_id"]
        
        # ボタンの value には "YYYY-MM-DD" が、
        # block_id か metadata に record_owner_id が入っている想定
        # もしView側で未対応なら record から取得してチェックする
        date = body["actions"][0].get("value")
        
        # 動的にチャンネルとメッセージIDを特定（上書きに必須）
        channel_id = body["container"]["channel_id"]
        message_ts = body["container"]["message_ts"]

        try:
            # Firestoreから既存レコードを取得
            record = get_single_attendance_record(workspace_id, user_id, date)
            
            # --- 本人チェック ---
            # DB上の user_id と、今ボタンを押した user_id が一致するか確認
            if not record or record.get("user_id") != user_id:
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
            # view = create_attendance_modal_view(initial_data, private_metadata) # 下記の補足参照
            # ※既存の create_attendance_modal_view に metadata を渡せるように修正が必要
            view = create_attendance_modal_view(initial_data=initial_data, is_fixed_date=True)
            view["private_metadata"] = private_metadata # メタデータを上書きセット
            
            client.views_open(trigger_id=trigger_id, view=view)
            
        except Exception as e:
            logger.error(f"修正モーダル表示失敗: {e}")

    # ==========================================
    # 2. 勤怠の「取消」ボタン押下
    # ==========================================
    @app.action("delete_attendance_request")
    def open_delete_confirm_modal(ack, body, client):
        """確認用モーダルを開く（本人チェック付き）"""
        ack()
        user_id = body["user"]["id"]
        workspace_id = body["team"]["id"]
        trigger_id = body["trigger_id"]
        date = body["actions"][0]["value"]
        
        channel_id = body["container"]["channel_id"]

        try:
            record = get_single_attendance_record(workspace_id, user_id, date)
            
            if not record or record.get("user_id") != user_id:
                client.chat_postEphemeral(
                    channel=channel_id,
                    user=user_id,
                    text="⚠️ この操作は打刻した本人しか行えません。"
                )
                return

            # 削除対象のメッセージIDも渡しておく
            private_metadata = json.dumps({
                "date": date,
                "message_ts": body["container"]["message_ts"],
                "channel_id": channel_id
            })

            client.views_open(
                trigger_id=trigger_id,
                view=create_delete_confirm_modal(date, private_metadata=private_metadata)
            )
        except Exception as e:
            logger.error(f"取消モーダル表示失敗: {e}")

    # ==========================================
    # 3. 削除確認モーダルの「送信」
    # ==========================================
    @app.view("delete_attendance_confirm_callback")
    def handle_delete_confirm_submit(ack, body, client, view, attendance_service):
        """実際にDBから削除し、メッセージを消す"""
        ack()
        user_id = body["user"]["id"]
        workspace_id = body["team"]["id"]
        
        # メタデータから復元
        metadata = json.loads(view.get("private_metadata", "{}"))
        date = metadata.get("date")
        target_ts = metadata.get("message_ts")
        channel_id = metadata.get("channel_id")

        try:
            # 1. Firestoreから削除
            attendance_service.delete_attendance(workspace_id, user_id, date)
            
            # 2. メッセージ自体を削除（または削除済みメッセージに更新）
            # 消さないでほしい場合は update で「削除されました」にする
            client.chat_update(
                channel=channel_id,
                ts=target_ts,
                blocks=[{
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": f"~{date} の勤怠連絡を削除しました~"}
                }],
                text="勤怠を削除しました"
            )
            
            logger.info(f"Attendance deleted and message updated: {user_id} on {date}")
            
        except Exception as e:
            logger.error(f"取消処理失敗: {e}")

    # ==========================================
    # 3. メンバー設定ボタン or ショートカット
    # ==========================================
    @app.action("open_member_settings")
    @app.shortcut("open_member_setup_modal")
    def handle_member_setup(ack, body, client):
        """部署ごとのメンバー設定画面を開く"""
        ack()
        trigger_id = body["trigger_id"]

        try:
            # Firestoreから現在の設定を取得
            initial_members, version = get_channel_members_with_section()
            
            # チャンネルIDの特定（ボタンからか、ショートカットからかで取得先が変わる）
            channel_id = body.get("channel", {}).get("id") or \
                         (body.get("view", {}).get("private_metadata", {}).get("channel_id"))
            
            if not channel_id:
                # 取得できない場合は定数から取得（constants.py で定義済みと想定）
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
            # 今月の履歴を取得
            today = datetime.date.today()
            month_str = today.strftime("%Y-%m")
            
            # AttendanceService を通じて Firestore から履歴を取得
            history = attendance_service.get_user_history(user_id=user_id, month_filter=month_str)
            
            view = create_history_modal_view(
                history_records=history,
                selected_year=str(today.year),
                selected_month=f"{today.month:02d}"
            )
            client.views_open(trigger_id=body["trigger_id"], view=view)
            
        except Exception as e:
            logger.error(f"履歴表示失敗: {e}")