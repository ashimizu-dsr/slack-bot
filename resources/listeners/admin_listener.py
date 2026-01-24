"""
管理機能リスナー

このモジュールは、管理者向けのSlackイベントを受け取ります。
- レポート設定ショートカット
- グループ追加・編集・削除

命名規則:
- on_xxx: Slackイベントを受け取る
"""
import json
import logging

logger = logging.getLogger(__name__)


def register_admin_listeners(app):
    """
    管理機能関連のリスナーをSlack Appに登録します。
    
    Args:
        app: Slack Bolt Appインスタンス
    """

    # ==========================================
    # 1. グローバルショートカット「レポート設定」
    # ==========================================
    @app.shortcut("open_member_setup_modal")
    def on_admin_settings_shortcut(ack, body, client):
        """
        グローバルショートカット「レポート設定」のハンドラー（v2.22）。
        
        レポート設定モーダル（一覧表示）を開きます。
        """
        ack()
        from resources.services.group_service import GroupService
        from resources.services.workspace_service import WorkspaceService
        from resources.templates.modals import create_admin_settings_modal
        
        workspace_id = body["team"]["id"]
        
        try:
            group_service = GroupService()
            workspace_service = WorkspaceService()
            
            # 管理者IDを取得
            admin_ids = workspace_service.get_admin_ids(workspace_id)
            
            # 全グループを取得
            groups = group_service.get_all_groups(workspace_id)

            # ユーザー名マップの生成
            all_uids = set(admin_ids)
            for g in groups:
                all_uids.update(g.get("member_ids", []))
            
            user_name_map = {}
            try:
                users_data = client.users_list()
                if users_data["ok"]:
                    for u in users_data["members"]:
                        if u["id"] in all_uids:
                            profile = u.get("profile", {})
                            name = (
                                profile.get("display_name") or 
                                u.get("real_name") or 
                                u.get("name")
                            )
                            user_name_map[u["id"]] = name
            except Exception as e:
                logger.error(f"Failed to fetch user list: {e}")

            # モーダルを生成
            view = create_admin_settings_modal(
                admin_ids=admin_ids, 
                groups=groups, 
                user_name_map=user_name_map
            )
            
            client.views_open(trigger_id=body["trigger_id"], view=view)
            
            logger.info(
                f"レポート設定モーダル表示(v2.22): Workspace={workspace_id}, Groups={len(groups)}"
            )
        except Exception as e:
            logger.error(f"レポート設定モーダル表示失敗: {e}", exc_info=True)

    # ==========================================
    # 2. レポート設定モーダル「保存」押下
    # ==========================================
    @app.view("admin_settings_modal")
    def on_admin_settings_submitted(ack, body, view):
        """
        レポート設定モーダル（一覧）の「保存」ボタン押下時の処理（v2.22）。
        
        通知先（admin_ids）のみを保存します。
        """
        ack()
        from resources.services.workspace_service import WorkspaceService
        
        workspace_id = body["team"]["id"]
        vals = view["state"]["values"]
        
        try:
            workspace_service = WorkspaceService()
            
            # 通知先（管理者）IDを取得
            admin_ids = vals["admin_block"]["admin_select"].get("selected_users", [])
            
            # 通知先を保存
            workspace_service.save_admin_ids(workspace_id, admin_ids)
            logger.info(f"通知先保存(v2.22): Workspace={workspace_id}, Admins={len(admin_ids)}")
            
        except Exception as e:
            logger.error(f"通知先保存失敗(v2.22): {e}", exc_info=True)

    # ==========================================
    # 3. 「追加」ボタン押下
    # ==========================================
    @app.action("add_new_group")
    def on_add_group_button_clicked(ack, body, client):
        """
        「追加」ボタンのハンドラー（v2.22）。
        
        views.pushでグループ追加モーダルを表示します。
        """
        from resources.templates.modals import create_add_group_modal
        
        try:
            view = create_add_group_modal()
            client.views_push(trigger_id=body["trigger_id"], view=view)
            logger.info("グループ追加モーダル表示(v2.22)")
            ack()
        except Exception as e:
            logger.error(f"グループ追加モーダル表示失敗: {e}", exc_info=True)
            ack()

    # ==========================================
    # 4. グループ追加モーダル「保存」押下
    # ==========================================
    @app.view("add_group_modal")
    def on_add_group_submitted(ack, body, view, client):
        """
        グループ追加モーダルの「保存」ボタン押下時の処理（v2.22）。
        
        新しいグループを作成し、親モーダル（一覧）を更新します。
        """
        from resources.services.group_service import GroupService
        
        workspace_id = body["team"]["id"]
        vals = view["state"]["values"]
        
        try:
            group_service = GroupService()
            
            # 入力値を取得
            group_name_raw = vals["name_block"]["name_input"].get("value", "")
            group_name = group_name_raw.strip() if group_name_raw else ""
            member_ids = vals["members_block"]["members_select"].get("selected_users", [])
            
            # バリデーション
            if not group_name:
                ack(response_action="errors", errors={
                    "name_block": "⚠️ グループ名称を入力してください。"
                })
                return
            
            # グループを作成
            group_service.create_group(
                workspace_id=workspace_id,
                name=group_name,
                member_ids=member_ids,
                created_by=body["user"]["id"]
            )
            logger.info(f"グループ作成(v2.22): {group_name}, Members={len(member_ids)}")
            
            ack()
            
            # 親モーダル（一覧）を更新
            _update_parent_admin_modal(client, body["view"]["previous_view_id"], workspace_id)
            
        except Exception as e:
            logger.error(f"グループ作成失敗(v2.22): {e}", exc_info=True)
            ack()

    # ==========================================
    # 5. オーバーフローメニュー（...）押下
    # ==========================================
    @app.action("group_overflow_action")
    def on_group_overflow_menu_selected(ack, body, client):
        """
        オーバーフローメニュー（...）のハンドラー（v2.22）。
        
        action_value:
          - "edit_{group_id}": 編集モーダルをpush
          - "delete_{group_id}": 削除確認モーダルをpush
        """
        from resources.services.group_service import GroupService
        from resources.templates.modals import (
            create_edit_group_modal,
            create_member_delete_confirm_modal
        )
        
        workspace_id = body["team"]["id"]
        
        try:
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
                
                client.views_push(trigger_id=body["trigger_id"], view=view)
                logger.info(f"削除確認モーダル表示(v2.22): {group_id}")
            
            ack()
                
        except Exception as e:
            logger.error(f"オーバーフローメニュー処理失敗: {e}", exc_info=True)
            ack()

    # ==========================================
    # 6. グループ編集モーダル「更新」押下
    # ==========================================
    @app.view("edit_group_modal")
    def on_edit_group_submitted(ack, body, view, client):
        """
        グループ編集モーダルの「更新」ボタン押下時の処理（v2.22）。
        
        グループ名とメンバーを更新し、親モーダル（一覧）を更新します。
        """
        from resources.services.group_service import GroupService
        
        workspace_id = body["team"]["id"]
        metadata = json.loads(view.get("private_metadata", "{}"))
        vals = view["state"]["values"]
        
        try:
            group_service = GroupService()
            
            # metadataからgroup_idを取得
            group_id = metadata.get("group_id")
            
            if not group_id:
                logger.error("group_idがmetadataにありません")
                ack()
                return
            
            # 入力値を取得
            group_name_raw = vals["name_block"]["name_input"].get("value", "")
            group_name = group_name_raw.strip() if group_name_raw else ""
            member_ids = vals["members_block"]["members_select"].get("selected_users", [])
            
            # バリデーション
            if not group_name:
                ack(response_action="errors", errors={
                    "name_block": "⚠️ グループ名称を入力してください。"
                })
                return
            
            # グループを更新
            group_service.update_group_members(
                workspace_id=workspace_id,
                group_id=group_id,
                member_ids=member_ids
            )
            logger.info(f"グループ更新(v2.22): {group_name} ({group_id}), Members={len(member_ids)}")
            
            ack()
            
            # 親モーダル（一覧）を更新
            _update_parent_admin_modal(client, body["view"]["previous_view_id"], workspace_id)
            
        except Exception as e:
            logger.error(f"グループ更新失敗(v2.22): {e}", exc_info=True)
            ack()

    # ==========================================
    # 7. 削除確認モーダル「削除する」押下
    # ==========================================
    @app.view("delete_confirm_modal")
    def on_delete_group_confirmed(ack, body, view, client):
        """
        削除確認モーダルの「削除する」ボタン押下時の処理（v2.22）。
        
        グループを削除し、親モーダル（一覧）を更新します。
        """
        workspace_id = body["team"]["id"]
        metadata = json.loads(view.get("private_metadata", "{}"))
        
        try:
            # metadataからgroup_idを取得
            group_id = metadata.get("group_id")
            group_name = metadata.get("group_name", "")
            
            if not group_id:
                logger.error("group_idがmetadataにありません")
                ack()
                return
            
            # グループを削除
            from google.cloud import firestore
            db = firestore.Client()
            group_ref = db.collection("groups").document(workspace_id)\
                          .collection("groups").document(group_id)
            group_ref.delete()
            logger.info(f"グループ削除(v2.22): {group_name} ({group_id})")
            
            ack()
            
            # 親モーダル（一覧）を更新
            _update_parent_admin_modal(client, body["view"]["previous_view_id"], workspace_id)
            
        except Exception as e:
            logger.error(f"グループ削除失敗(v2.22): {e}", exc_info=True)
            ack()


def _update_parent_admin_modal(client, view_id, workspace_id):
    """
    親モーダル（レポート設定一覧）を最新データで更新します（v2.22用ヘルパー）。
    
    Args:
        client: Slack client
        view_id: 更新対象のview_id
        workspace_id: ワークスペースID
    """
    try:
        from resources.services.group_service import GroupService
        from resources.services.workspace_service import WorkspaceService
        from resources.templates.modals import create_admin_settings_modal
        
        group_service = GroupService()
        workspace_service = WorkspaceService()
        
        # 最新データを取得
        admin_ids = workspace_service.get_admin_ids(workspace_id)
        groups = group_service.get_all_groups(workspace_id)
        
        # 表示名マップを生成
        all_user_ids = set(admin_ids)
        for g in groups:
            all_user_ids.update(g.get("member_ids", []))
            
        user_name_map = {}
        for uid in all_user_ids:
            try:
                # ユーザー情報を取得して表示名をマップに格納
                user_info = client.users_info(user=uid)
                if user_info["ok"]:
                    profile = user_info["user"]["profile"]
                    user_name_map[uid] = (
                        profile.get("display_name") or 
                        profile.get("real_name") or 
                        uid
                    )
            except Exception:
                user_name_map[uid] = uid

        # モーダルを再生成
        view = create_admin_settings_modal(
            admin_ids=admin_ids, 
            groups=groups, 
            user_name_map=user_name_map
        )
        
        # 更新
        client.views_update(view_id=view_id, view=view)
        logger.info(f"親モーダル更新成功(v2.22): Groups={len(groups)}")
    except Exception as e:
        logger.error(f"親モーダル更新失敗(v2.22): {e}", exc_info=True)
