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
    # v2.22: 新しいレポート設定モーダル
    create_admin_settings_modal,
    create_add_group_modal,
    create_edit_group_modal,
    create_attendance_delete_confirm_modal,
    create_member_delete_confirm_modal
)
from resources.shared.db import (
    get_single_attendance_record, 
    # get_channel_members_with_section
)

logger = logging.getLogger(__name__)


def register_action_handlers(app, attendance_service, notification_service, dispatcher=None) -> None:
    """
    アクション関連のハンドラーをSlack Appに登録します。
    
    Args:
        app: Slack Bolt Appインスタンス
        attendance_service: AttendanceServiceインスタンス
        notification_service: NotificationServiceインスタンス
        dispatcher: InteractionDispatcherインスタンス（非同期処理用、オプション）
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

            view = create_attendance_delete_confirm_modal(date) 
            # 呼び出し側(ハンドラー)で期待している callback_id に強制的に合わせる
            view["callback_id"] = "delete_attendance_confirm_callback"
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
        
        非同期処理対応:
        - dispatcherが提供されている場合、Pub/Sub経由で処理
        - 提供されていない場合、同期処理
        """
        ack()
        
        # 非同期処理が有効な場合
        if dispatcher:
            try:
                dispatcher.dispatch(body, "delete_attendance_confirm")
                logger.info("削除リクエストをキューに投げました（非同期）")
            except Exception as e:
                logger.error(f"非同期ディスパッチ失敗、同期処理にフォールバック: {e}", exc_info=True)
                # フォールバック: 同期処理
                _delete_attendance_sync(body, client, view, attendance_service)
        else:
            # 同期処理
            _delete_attendance_sync(body, client, view, attendance_service)
    
    def _delete_attendance_sync(body, client, view, attendance_service):
        """
        勤怠削除の同期処理（内部関数）
        """
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
            logger.info(f"削除成功（同期）: User={user_id}, Date={metadata['date']}")
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

    # # ==========================================
    # # 6. v2.2: 設定モーダルを開く（複数グループ一括管理版）
    # # 【注意】v2.22でスラッシュコマンド方式に移行したため、このハンドラーは無効化されています
    # # ==========================================
    # # @app.shortcut("open_member_setup_modal")
    # def handle_settings_v2_shortcut_deprecated(ack, body, client):
    #     """
    #     グローバルショートカット「設定」の処理（v2.2版）。
        
    #     v2.1との違い:
    #     - 複数グループを同時編集可能
    #     - 既存グループを全て表示
    #     - 動的にグループを追加可能
    #     """
    #     ack()
    #     workspace_id = body["team"]["id"]
        
    #     try:
    #         from resources.services.group_service import GroupService
    #         from resources.services.workspace_service import WorkspaceService
            
    #         group_service = GroupService()
    #         workspace_service = WorkspaceService()
            
    #         # 管理者IDを取得
    #         admin_ids = workspace_service.get_admin_ids(workspace_id)
            
    #         # 全グループを取得
    #         existing_groups = group_service.get_all_groups(workspace_id)
            
    #         # グループデータを整形（nameフィールドを使用）
    #         groups_data = [
    #             {
    #                 "group_id": g.get("group_id"),  # UUID（保存時の識別用）
    #                 "name": g.get("name", ""),       # グループ名（画面表示用）
    #                 "member_ids": g.get("member_ids", [])
    #             }
    #             for g in existing_groups
    #         ]
            
    #         # モーダルを生成（v2.2版）
    #         view = create_member_settings_modal_v2(
    #             admin_ids=admin_ids,
    #             groups_data=groups_data
    #         )
            
    #         client.views_open(trigger_id=body["trigger_id"], view=view)
    #         logger.info(f"設定モーダル表示(v2.2): Workspace={workspace_id}, Groups={len(groups_data)}")
    #     except Exception as e:
    #         logger.error(f"設定モーダル表示失敗: {e}", exc_info=True)

    # # ==========================================
    # # 7. v2.2: グループ追加ボタン押下
    # # 【注意】v2.22では個別の追加モーダルに移行したため、このハンドラーは無効化されています
    # # ==========================================
    # # @app.action("add_group_button_action")
    # def handle_add_group_button_deprecated(ack, body, client):
    #     """
    #     グループ追加ボタンのハンドラー（v2.2で追加）。
        
    #     現在の入力値を保持しつつ、新しいグループ入力セットを追加してモーダルを更新します。
        
    #     動作:
    #     1. 現在のモーダルからmetadataとstate.valuesを取得
    #     2. 既に入力されている「通知先」「各グループ名」「各メンバー」を抽出
    #     3. group_countを+1
    #     4. 新しいモーダルを生成（既存の値を initial_value/initial_users として設定）
    #     5. views.updateでモーダルを更新
        
    #     Note:
    #     - views.updateは既存の入力値をクリアするため、明示的に保持する必要があります
    #     - 最大10グループまで追加可能
    #     """
    #     ack()
        
    #     try:
    #         # 1. 現在の状態を取得
    #         metadata = json.loads(body["view"].get("private_metadata", "{}"))
    #         current_count = metadata.get("group_count", 1)
    #         existing_groups_data = metadata.get("groups_data", [])
            
    #         # 2. 現在の入力値を保存
    #         state_values = body["view"]["state"]["values"]
            
    #         # 通知先（管理者）
    #         admin_ids = state_values.get("admin_users_block", {}).get("admin_users_select", {}).get("selected_users", [])
            
    #         # 各グループの情報（画面の入力値 + metadataのgroup_id）
    #         groups_data = []
    #         for i in range(1, current_count + 1):
    #             name_block = f"group_name_{i}"
    #             members_block = f"group_members_{i}"
                
    #             name_raw = state_values.get(name_block, {}).get("group_name_input", {}).get("value", "")
    #             name = name_raw.strip() if name_raw else ""
                
    #             member_ids = state_values.get(members_block, {}).get("target_members_select", {}).get("selected_users", [])
                
    #             # metadataから既存のgroup_idを取得（存在する場合）
    #             group_id = None
    #             if i <= len(existing_groups_data):
    #                 group_id = existing_groups_data[i - 1].get("group_id")
                
    #             groups_data.append({
    #                 "group_id": group_id,  # 既存グループの場合はUUID、新規の場合はNone
    #                 "name": name,
    #                 "member_ids": member_ids
    #             })
            
    #         # 3. 新しいグループ数（最大10）
    #         new_count = min(current_count + 1, 10)
            
    #         # 4. 新しいモーダルを生成
    #         view = create_member_settings_modal_v2(
    #             admin_ids=admin_ids,
    #             groups_data=groups_data,
    #             group_count=new_count
    #         )
            
    #         # 5. モーダルを更新
    #         client.views_update(
    #             view_id=body["view"]["id"],
    #             hash=body["view"]["hash"],
    #             view=view
    #         )
    #         logger.info(f"グループ追加: {current_count} → {new_count}")
    #     except Exception as e:
    #         logger.error(f"グループ追加失敗: {e}", exc_info=True)

    # # ==========================================
    # # 7. v2.0: グループ選択時の動的更新（v2.1では廃止予定）
    # # ==========================================
    # # Note: v2.1ではグループ選択をテキスト入力に変更したため、
    # # このハンドラーは将来的に削除予定です。
    # # v2.0のモーダルを使用している場合のみ動作します。
    # @app.action("group_select_action")
    # def handle_group_select_change(ack, body, client):
    #     """
    #     設定モーダルでグループ選択が変更された時の処理（v2.0のみ）。
        
    #     選択されたグループのメンバーを表示するためにモーダルを動的更新します。
    #     v2.1では不要になりました。
    #     """
    #     ack()
    #     workspace_id = body["team"]["id"]
        
    #     try:
    #         from resources.services.group_service import GroupService
    #         from resources.services.workspace_service import WorkspaceService
            
    #         group_service = GroupService()
    #         workspace_service = WorkspaceService()
            
    #         # 選択されたグループIDを取得
    #         selected_value = body["actions"][0]["selected_option"]["value"]
            
    #         # 管理者IDを取得
    #         admin_ids = workspace_service.get_admin_ids(workspace_id)
            
    #         # 全グループを取得
    #         all_groups = group_service.get_all_groups(workspace_id)
            
    #         selected_group_id = None
    #         selected_group_members = []
            
    #         if selected_value == "action_new_group":
    #             # 新規グループを作成
    #             new_group_id = group_service.create_group(
    #                 workspace_id=workspace_id,
    #                 name="新規グループ",
    #                 member_ids=[],
    #                 created_by=body["user"]["id"]
    #             )
                
    #             # 全グループを再取得（新規グループを含む）
    #             all_groups = group_service.get_all_groups(workspace_id)
                
    #             selected_group_id = new_group_id
    #             selected_group_members = []
    #             logger.info(f"新規グループ作成: {new_group_id}")
    #         else:
    #             # 既存グループを選択
    #             selected_group = group_service.get_group_by_id(workspace_id, selected_value)
    #             if selected_group:
    #                 selected_group_id = selected_value
    #                 selected_group_members = selected_group.get("member_ids", [])
    #                 logger.info(f"グループ選択: {selected_group_id}, Members={len(selected_group_members)}")
            
    #         # モーダルを更新
    #         updated_view = create_member_settings_modal_v2(
    #             admin_ids=admin_ids,
    #             all_groups=all_groups,
    #             selected_group_id=selected_group_id,
    #             selected_group_members=selected_group_members
    #         )
            
    #         client.views_update(
    #             view_id=body["view"]["id"],
    #             hash=body["view"]["hash"],
    #             view=updated_view
    #         )
    #         logger.info(f"モーダル更新成功: Workspace={workspace_id}")
    #     except Exception as e:
    #         logger.error(f"グループ選択処理失敗: {e}", exc_info=True)

    # ==========================================
    # 8. v2.22: グローバルショートカット「レポート設定」
    # ==========================================
    @app.shortcut("open_member_setup_modal")
    # @app.shortcut("open_report_admin")
    def handle_report_admin_shortcut(ack, body, client):
        """
        グローバルショートカット「レポート設定」のハンドラー（v2.22）。
        
        レポート設定モーダル（一覧表示）を開きます。
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
            groups = group_service.get_all_groups(workspace_id)

            # --- 追加：user_name_map の生成 ---
            # 全グループのメンバーIDと管理者IDを重複なく集める
            all_uids = set(admin_ids)
            for g in groups:
                all_uids.update(g.get("member_ids", []))
            
            # ワークスペースのユーザー情報を取得してマップを作成
            # (人数が多い場合は users_info をループするより users_list の方が効率的です)
            user_name_map = {}
            try:
                users_data = client.users_list()
                if users_data["ok"]:
                    for u in users_data["members"]:
                        if u["id"] in all_uids:
                            profile = u.get("profile", {})
                            # 優先順位: 表示名(display_name) > 本名(real_name) > アカウント名(name)
                            name = profile.get("display_name") or u.get("real_name") or u.get("name")
                            user_name_map[u["id"]] = name
            except Exception as e:
                logger.error(f"Failed to fetch user list: {e}")

            # モーダルを生成（user_name_mapを渡す）
            view = create_admin_settings_modal(
                admin_ids=admin_ids, 
                groups=groups, 
                user_name_map=user_name_map
            )
            
            # モーダルを生成（v2.22版）
            client.views_open(trigger_id=body["trigger_id"], view=view)
            
            logger.info(f"レポート設定モーダル表示(v2.22): Workspace={workspace_id}, Groups={len(groups)}")
        except Exception as e:
            logger.error(f"レポート設定モーダル表示失敗: {e}", exc_info=True)

    # ==========================================
    # 9. v2.22: 追加ボタン押下
    # ==========================================
    @app.action("add_new_group")
    def handle_add_new_group_button(ack, body, client):
        """
        「追加」ボタンのハンドラー（v2.22）。
        
        views.pushでグループ追加モーダルを表示します。
        
        Note:
            Cloud Run制約対応のため、views.pushを先に実行してからack()を呼びます。
        """
        try:
            # 追加モーダルを生成
            view = create_add_group_modal()
            
            # Cloud Run制約対応: views.pushを先に実行
            # client.views_push(view_id=body["view"]["id"], view=view)
            client.views_push(trigger_id=body["trigger_id"], view=view)
            logger.info("グループ追加モーダル表示(v2.22)")
            
            # Cloud Run制約対応: 最後にack()
            ack()
        except Exception as e:
            logger.error(f"グループ追加モーダル表示失敗: {e}", exc_info=True)
            ack()

    # ==========================================
    # 10. v2.22: オーバーフローメニュー（...）押下
    # ==========================================
    @app.action("group_overflow_action")
    def handle_group_overflow_menu(ack, body, client):
        """
        オーバーフローメニュー（...）のハンドラー（v2.22）。
        
        action_value:
          - "edit_{group_id}": 編集モーダルをpush
          - "delete_{group_id}": 削除確認モーダルをpush
          
        Note:
            Cloud Run制約対応のため、views.pushを先に実行してからack()を呼びます。
        """
        workspace_id = body["team"]["id"]
        
        try:
            from resources.services.group_service import GroupService
            
            group_service = GroupService()
            
            # 選択されたアクションの値（edit_xxx または delete_xxx）
            action_value = body["actions"][0]["selected_option"]["value"]
            
            # アクションタイプとgroup_idを分離
            action_type, group_id = action_value.split("_", 1)
            
            if action_type == "edit":
                # 編集モーダルを表示
                group = group_service.get_group_by_id(workspace_id, group_id)
                
                if not group:
                    logger.error(f"グループが見つかりません: {group_id}")
                    ack()
                    return
                
                view = create_edit_group_modal(
                    group_id=group["group_id"],
                    group_name=group["name"],
                    member_ids=group.get("member_ids", [])
                )
                
                # Cloud Run制約対応: views.pushを先に実行
                # client.views_push(view_id=body["view"]["id"], view=view)
                client.views_push(trigger_id=body["trigger_id"], view=view)
                logger.info(f"編集モーダル表示(v2.22): {group_id}")
                
            elif action_type == "delete":
                # 削除確認モーダルを表示
                group = group_service.get_group_by_id(workspace_id, group_id)
                
                if not group:
                    logger.error(f"グループが見つかりません: {group_id}")
                    ack()
                    return
                
                view = create_member_delete_confirm_modal(
                    group_id=group["group_id"],
                    group_name=group["name"]
                )
                
                # Cloud Run制約対応: views.pushを先に実行
                # client.views_push(view_id=body["view"]["id"], view=view)
                client.views_push(trigger_id=body["trigger_id"], view=view)
                logger.info(f"削除確認モーダル表示(v2.22): {group_id}")
            
            # Cloud Run制約対応: 最後にack()
            ack()
                
        except Exception as e:
            logger.error(f"オーバーフローメニュー処理失敗: {e}", exc_info=True)
            ack()