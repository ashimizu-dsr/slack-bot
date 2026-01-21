"""
モーダルハンドラー

このモジュールは、Slackモーダル内のフォーム送信（Submit）を処理します。
勤怠入力モーダルやメンバー設定モーダルの保存処理を担当します。
"""
import json
import logging
from resources.shared.db import save_channel_members_db

logger = logging.getLogger(__name__)


def register_modal_handlers(app, attendance_service, notification_service) -> None:
    """
    モーダル関連のハンドラーをSlack Appに登録します。
    
    Args:
        app: Slack Bolt Appインスタンス
        attendance_service: AttendanceServiceインスタンス
        notification_service: NotificationServiceインスタンス
    """

    # ==========================================
    # 1. 勤怠入力モーダル：「保存」押下（修正・更新）
    # ==========================================
    @app.view("attendance_submit")
    def handle_attendance_save(ack, body, client, view):
        """
        勤怠入力モーダルの「保存」ボタン押下時の処理。
        
        既存レコードを更新する場合、元のメッセージを上書きします。
        """
        # モーダルを閉じる
        ack()
        
        user_id = body["user"]["id"]
        workspace_id = body["team"]["id"]
        
        # モーダル生成時に仕込んだメタデータを取得
        metadata = json.loads(view.get("private_metadata", "{}"))
        vals = view["state"]["values"]
        
        date = metadata.get("date")
        # どのメッセージを上書きするか（message_ts）をメタデータから取得
        target_message_ts = metadata.get("message_ts") 

        status = vals["status_block"]["status_select"]["selected_option"]["value"]
        note = vals["note_block"]["note_input"]["value"] or ""

        try:
            # 1. 既存レコードを取得（修正: get_specific_date_recordメソッドを使用）
            existing = attendance_service.get_specific_date_record(workspace_id, user_id, date) or {}
            
            # 2. 保存実行
            record = attendance_service.save_attendance(
                workspace_id=workspace_id,
                user_id=user_id,
                email=existing.get("email"),
                date=date,
                status=status,
                note=note,
                channel_id=existing.get("channel_id") or metadata.get("channel_id"),
                ts=existing.get("ts")
            )
            
            # 3. 通知（上書き実行）
            notification_service.notify_attendance_change(
                record=record,
                is_update=True,
                channel=existing.get("channel_id") or metadata.get("channel_id"),
                thread_ts=existing.get("ts"),
                message_ts=target_message_ts
            )
            logger.info(f"Modal update success: {user_id} on {date}, target_ts: {target_message_ts}")
            
        except Exception as e:
            logger.error(f"勤怠保存失敗 (Modal): {e}", exc_info=True)

    # ==========================================
    # 2. メンバー設定モーダル：「保存」押下
    # ==========================================
    @app.view("member_settings_submit")
    def handle_member_settings_save(ack, body, view):
        """
        メンバー設定モーダルの「保存」ボタン押下時の処理。
        
        課別にメンバーを割り当て、Firestoreに保存します。
        """
        workspace_id = body["team"]["id"]
        metadata = json.loads(view.get("private_metadata", "{}"))
        vals = view["state"]["values"]
        
        # 集計対象のセクションIDリスト
        sections = ["sec_1", "sec_2", "sec_3", "sec_4", "sec_5", "sec_6", "sec_7", "sec_finance"]
        
        settings = {}
        for s in sections:
            block_id = f"user_select_block_{s}"
            if block_id in vals:
                # selected_users (複数選択) からリストを取得
                settings[s] = vals[block_id]["user_select"].get("selected_users", [])

        try:
            # Firestoreへメンバー構成を保存
            save_channel_members_db(
                workspace_id=workspace_id,
                channel_id=metadata.get("channel_id"),
                section_user_map=settings,
                client_version=metadata.get("version")
            )
            # 成功したらモーダルを閉じる
            ack()
            logger.info(f"Member settings updated in WS: {workspace_id}")
            
        except Exception as e:
            # TODO: 楽観的ロックが実装されていないため、
            # 現状は CONCURRENCY_ERROR は発生しません。
            # 将来的にはdb.pyでバージョンチェックを実装する必要があります。
            if "CONCURRENCY_ERROR" in str(e) or "conflict" in str(e).lower():
                ack(response_action="errors", errors={
                    "user_select_block_sec_1": "⚠️ 他の管理者が更新しました。再度開き直してください。"
                })
            else:
                logger.error(f"メンバー設定保存失敗: {e}", exc_info=True)
                ack()

    # ==========================================
    # 3. v2.0: 設定モーダル「保存」押下（動的グループ管理）
    # ==========================================
    @app.view("member_settings_v2")
    def handle_member_settings_v2_save(ack, body, view):
        """
        v2.0設定モーダルの「保存」ボタン押下時の処理。
        
        管理者とグループメンバーを保存します。
        """
        workspace_id = body["team"]["id"]
        metadata = json.loads(view.get("private_metadata", "{}"))
        vals = view["state"]["values"]
        
        try:
            from resources.services.group_service import GroupService
            from resources.services.workspace_service import WorkspaceService
            from resources.shared.errors import ValidationError
            
            group_service = GroupService()
            workspace_service = WorkspaceService()
            
            # 1. 管理者IDを取得
            admin_ids = vals["admin_users_block"]["admin_users_select"].get("selected_users", [])
            
            if not admin_ids:
                # バリデーションエラー
                ack(response_action="errors", errors={
                    "admin_users_block": "⚠️ 少なくとも1人の管理者を選択してください。"
                })
                return
            
            # 2. グループIDとメンバーを取得
            selected_group_id = metadata.get("selected_group_id")
            
            # グループが選択されている場合のみメンバーを更新
            if selected_group_id and selected_group_id != "action_new_group":
                # 所属者を取得
                member_ids = vals.get("target_members_block", {}).get("target_members_select", {}).get("selected_users", [])
                
                # グループメンバーを更新
                group_service.update_group_members(
                    workspace_id=workspace_id,
                    group_id=selected_group_id,
                    member_ids=member_ids
                )
                logger.info(f"グループメンバー保存: GroupID={selected_group_id}, Members={len(member_ids)}")
            
            # 3. 管理者IDを保存
            workspace_service.save_admin_ids(workspace_id, admin_ids)
            logger.info(f"管理者保存: Workspace={workspace_id}, Admins={len(admin_ids)}")
            
            # 成功
            ack()
            logger.info(f"設定保存成功(v2.0): Workspace={workspace_id}")
            
        except ValidationError as ve:
            logger.warning(f"バリデーションエラー: {ve}")
            ack(response_action="errors", errors={
                "admin_users_block": ve.user_message
            })
        except Exception as e:
            logger.error(f"設定保存失敗(v2.0): {e}", exc_info=True)
            ack()