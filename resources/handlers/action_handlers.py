"""
アクションハンドラー

このモジュールは、メッセージ上のボタン押下やグローバルショートカットを処理します。
勤怠カードの修正・取消ボタン、履歴表示ショートカットなどを担当します。
"""
import datetime
import logging
import json
from resources.views.modal_views import (
    create_attendance_modal_view, 
    create_history_modal_view,
    create_member_settings_modal_view,
    create_member_settings_modal_v2,
    create_member_settings_modal_v2_1,
    create_delete_confirm_modal
)
from resources.shared.db import (
    get_single_attendance_record, 
    get_channel_members_with_section
)

logger = logging.getLogger(__name__)


def register_action_handlers(app, attendance_service, notification_service) -> None:
    """
    アクション関連のハンドラーをSlack Appに登録します。
    
    Args:
        app: Slack Bolt Appインスタンス
        attendance_service: AttendanceServiceインスタンス
        notification_service: NotificationServiceインスタンス
    """

    # ==========================================
    # 1. 勤怠の「修正」ボタン押下
    # ==========================================
    @app.action("open_update_attendance")
    def handle_open_update_modal(ack, body, client):
        """
        勤怠カードの「修正」ボタン押下時の処理。
        
        本人確認を行い、編集モーダルを表示します。
        """
        ack()
        user_id = body["user"]["id"]
        workspace_id = body["team"]["id"]
        trigger_id = body["trigger_id"]
        date = body["actions"][0].get("value")
        
        channel_id = body["container"]["channel_id"]
        message_ts = body["container"]["message_ts"]

        try:
            record = get_single_attendance_record(workspace_id, user_id, date)
            
            # 本人チェック：データがあり、かつ自分のものではない場合のみ拒絶
            if record and record.get("user_id") != user_id:
                client.chat_postEphemeral(
                    channel=channel_id,
                    user=user_id,
                    text="⚠️ この操作は打刻した本人しか行えません。"
                )
                logger.warning(f"権限エラー: User {user_id} が User {record.get('user_id')} の記録を編集しようとしました")
                return

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
        """
        勤怠カードの「取消」ボタン押下時の処理。
        
        本人確認を行い、削除確認モーダルを表示します。
        """
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
                    text="⚠️ 本人のみ取消可能です。"
                )
                logger.warning(f"権限エラー: User {user_id} が User {record.get('user_id')} の記録を削除しようとしました")
                return

            view = create_delete_confirm_modal(date) 
            view["private_metadata"] = json.dumps({
                "date": date,
                "message_ts": body["container"]["message_ts"],
                "channel_id": channel_id
            })
            client.views_open(trigger_id=trigger_id, view=view)
        except Exception as e:
            logger.error(f"取消モーダル表示失敗: {e}", exc_info=True)

    # ==========================================
    # 3. 削除確認モーダルの「送信」
    # ==========================================
    @app.view("delete_attendance_confirm_callback")
    def handle_delete_confirm_submit(ack, body, client, view):
        """
        削除確認モーダルの「削除する」ボタン押下時の処理。
        
        実際の削除を実行し、元のメッセージを更新します。
        """
        ack()
        user_id = body["user"]["id"]
        workspace_id = body["team"]["id"]
        metadata = json.loads(view.get("private_metadata", "{}"))

        try:
            attendance_service.delete_attendance(workspace_id, user_id, metadata["date"])
            client.chat_update(
                channel=metadata["channel_id"],
                ts=metadata["message_ts"],
                blocks=[{
                    "type": "context", 
                    "elements": [{
                        "type": "mrkdwn", 
                        "text": f"ⓘ <@{user_id}>さんの {metadata['date']} の勤怠連絡を取り消しました"
                    }]
                }],
                text="勤怠を取り消しました"
            )
            logger.info(f"削除成功: User={user_id}, Date={metadata['date']}")
        except Exception as e:
            logger.error(f"取消処理失敗: {e}", exc_info=True)

    # ==========================================
    # 4. 勤怠履歴表示（グローバルショートカット）
    # ==========================================
    @app.shortcut("open_kintai_history")
    def handle_history_shortcut(ack, body, client):
        """
        グローバルショートカット「勤怠連絡の確認」の処理。
        
        ユーザー自身の勤怠履歴を月単位で表示します。
        """
        ack()
        user_id = body["user"]["id"]
        workspace_id = body["team"]["id"] 
        
        try:
            today = datetime.date.today()
            month_str = today.strftime("%Y-%m")
            
            # workspace_id を追加して履歴取得
            history = attendance_service.get_user_history(
                workspace_id=workspace_id, 
                user_id=user_id, 
                month_filter=month_str
            )
            
            view = create_history_modal_view(
                history_records=history,
                selected_year=str(today.year),
                selected_month=f"{today.month:02d}",
                user_id=user_id
            )
            client.views_open(trigger_id=body["trigger_id"], view=view)
            logger.info(f"履歴表示: User={user_id}, Month={month_str}, Count={len(history)}")
        except Exception as e:
            logger.error(f"履歴表示失敗: {e}", exc_info=True)

    # ==========================================
    # 5. 履歴モーダルの年月変更
    # ==========================================
    @app.action("history_year_change")
    @app.action("history_month_change")
    def handle_history_filter_update(ack, body, client):
        """
        履歴モーダル内の年月ドロップダウン変更時の処理。
        
        選択された年月で履歴を再取得し、モーダルをリアルタイム更新します。
        """
        ack()
        workspace_id = body["team"]["id"]
        try:
            metadata = json.loads(body["view"]["private_metadata"])
            target_user_id = metadata.get("target_user_id")
            
            state_values = body["view"]["state"]["values"]
            selected_year = state_values["history_filter"]["history_year_change"]["selected_option"]["value"]
            selected_month = state_values["history_filter"]["history_month_change"]["selected_option"]["value"]
            
            month_filter = f"{selected_year}-{selected_month}"
            
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
            logger.info(f"履歴フィルタ更新: User={target_user_id}, Month={month_filter}, Count={len(history)}")
        except Exception as e:
            logger.error(f"履歴フィルタ更新失敗: {e}", exc_info=True)

    # ==========================================
    # 6. v2.1: 設定モーダルを開く（テキスト入力版・UPSERT方式）
    # ==========================================
    @app.shortcut("open_member_setup_modal")
    def handle_settings_v2_1_shortcut(ack, body, client):
        """
        グローバルショートカット「設定」の処理（v2.1版）。
        
        v2.0との違い:
        - グループ選択のドロップダウンを廃止
        - グループ名をテキスト入力で指定
        - 動的更新が不要になったため、シンプルな実装
        """
        ack()
        workspace_id = body["team"]["id"]
        
        try:
            from resources.services.group_service import GroupService
            from resources.services.workspace_service import WorkspaceService
            
            group_service = GroupService()
            workspace_service = WorkspaceService()
            
            # 管理者IDを取得
            admin_ids = workspace_service.get_admin_ids(workspace_id)
            
            # 全グループを取得
            all_groups = group_service.get_all_groups(workspace_id)
            
            # モーダルを生成（v2.1版）
            view = create_member_settings_modal_v2_1(
                admin_ids=admin_ids,
                all_groups=all_groups
            )
            
            client.views_open(trigger_id=body["trigger_id"], view=view)
            logger.info(f"設定モーダル表示(v2.1): Workspace={workspace_id}, Groups={len(all_groups)}")
        except Exception as e:
            logger.error(f"設定モーダル表示失敗: {e}", exc_info=True)

    # ==========================================
    # 7. v2.0: グループ選択時の動的更新（v2.1では廃止予定）
    # ==========================================
    # Note: v2.1ではグループ選択をテキスト入力に変更したため、
    # このハンドラーは将来的に削除予定です。
    # v2.0のモーダルを使用している場合のみ動作します。
    @app.action("group_select_action")
    def handle_group_select_change(ack, body, client):
        """
        設定モーダルでグループ選択が変更された時の処理（v2.0のみ）。
        
        選択されたグループのメンバーを表示するためにモーダルを動的更新します。
        v2.1では不要になりました。
        """
        ack()
        workspace_id = body["team"]["id"]
        
        try:
            from resources.services.group_service import GroupService
            from resources.services.workspace_service import WorkspaceService
            
            group_service = GroupService()
            workspace_service = WorkspaceService()
            
            # 選択されたグループIDを取得
            selected_value = body["actions"][0]["selected_option"]["value"]
            
            # 管理者IDを取得
            admin_ids = workspace_service.get_admin_ids(workspace_id)
            
            # 全グループを取得
            all_groups = group_service.get_all_groups(workspace_id)
            
            selected_group_id = None
            selected_group_members = []
            
            if selected_value == "action_new_group":
                # 新規グループを作成
                new_group_id = group_service.create_group(
                    workspace_id=workspace_id,
                    name="新規グループ",
                    member_ids=[],
                    created_by=body["user"]["id"]
                )
                
                # 全グループを再取得（新規グループを含む）
                all_groups = group_service.get_all_groups(workspace_id)
                
                selected_group_id = new_group_id
                selected_group_members = []
                logger.info(f"新規グループ作成: {new_group_id}")
            else:
                # 既存グループを選択
                selected_group = group_service.get_group_by_id(workspace_id, selected_value)
                if selected_group:
                    selected_group_id = selected_value
                    selected_group_members = selected_group.get("member_ids", [])
                    logger.info(f"グループ選択: {selected_group_id}, Members={len(selected_group_members)}")
            
            # モーダルを更新
            updated_view = create_member_settings_modal_v2(
                admin_ids=admin_ids,
                all_groups=all_groups,
                selected_group_id=selected_group_id,
                selected_group_members=selected_group_members
            )
            
            client.views_update(
                view_id=body["view"]["id"],
                hash=body["view"]["hash"],
                view=updated_view
            )
            logger.info(f"モーダル更新成功: Workspace={workspace_id}")
        except Exception as e:
            logger.error(f"グループ選択処理失敗: {e}", exc_info=True)