"""
フォーム送信する処理

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

    # # ==========================================
    # # 1. 勤怠入力モーダル：「保存」押下（修正・更新）
    # # ==========================================
    # @app.view("attendance_submit")
    # def handle_attendance_save(ack, body, client, view):
    #     """
    #     勤怠入力モーダルの「保存」ボタン押下時の処理。
        
    #     既存レコードを更新する場合、元のメッセージを上書きします。
    #     """
    #     # モーダルを閉じる
    #     ack()
        
    #     user_id = body["user"]["id"]
    #     workspace_id = body["team"]["id"]
        
    #     # モーダル生成時に仕込んだメタデータを取得
    #     metadata = json.loads(view.get("private_metadata", "{}"))
    #     vals = view["state"]["values"]
        
    #     date = metadata.get("date")
    #     # どのメッセージを上書きするか（message_ts）をメタデータから取得
    #     target_message_ts = metadata.get("message_ts") 

    #     status = vals["status_block"]["status_select"]["selected_option"]["value"]
    #     note = vals["note_block"]["note_input"]["value"] or ""

    #     try:
    #         # 1. 既存レコードを取得（修正: get_specific_date_recordメソッドを使用）
    #         existing = attendance_service.get_specific_date_record(workspace_id, user_id, date) or {}
            
    #         # 2. 保存実行
    #         record = attendance_service.save_attendance(
    #             workspace_id=workspace_id,
    #             user_id=user_id,
    #             email=existing.get("email"),
    #             date=date,
    #             status=status,
    #             note=note,
    #             channel_id=existing.get("channel_id") or metadata.get("channel_id"),
    #             ts=existing.get("ts")
    #         )
            
    #         # 3. 通知（上書き実行）

    #         print("DEBUG: CALLED FROM HANDLER_モーダルハンドラー")

    #         notification_service.notify_attendance_change(
    #             record=record,
    #             # display_name=display_name,
    #             is_update=True,
    #             channel=existing.get("channel_id") or metadata.get("channel_id"),
    #             thread_ts=existing.get("ts"),
    #             message_ts=target_message_ts
    #         )
    #         logger.info(f"Modal update success: {user_id} on {date}, target_ts: {target_message_ts}")
            
    #     except Exception as e:
    #         logger.error(f"勤怠保存失敗 (Modal): {e}", exc_info=True)



# ==========================================
# v2.22用ハンドラー
# ==========================================

def register_modal_handlers_v22(app):
    """
    v2.22のモーダルハンドラーを登録します。
    
    Args:
        app: Slack Bolt Appインスタンス
    """

    # ==========================================
    # 1. v2.22: レポート設定モーダル「保存」押下
    # ==========================================
    @app.view("admin_settings_modal")
    def handle_admin_settings_submission(ack, body, view):
        """
        レポート設定モーダル（一覧）の「保存」ボタン押下時の処理（v2.22）。
        
        通知先（admin_ids）のみを保存します。
        グループの編集は個別のモーダルで行います。
        """
        ack()
        
        workspace_id = body["team"]["id"]
        vals = view["state"]["values"]
        
        try:
            from resources.services.workspace_service import WorkspaceService
            
            workspace_service = WorkspaceService()
            
            # 通知先（管理者）IDを取得
            admin_ids = vals["admin_block"]["admin_select"].get("selected_users", [])
            
            # 通知先を保存
            workspace_service.save_admin_ids(workspace_id, admin_ids)
            logger.info(f"通知先保存(v2.22): Workspace={workspace_id}, Admins={len(admin_ids)}")
            
        except Exception as e:
            logger.error(f"通知先保存失敗(v2.22): {e}", exc_info=True)

    # ==========================================
    # 2. v2.22: グループ追加モーダル「保存」押下
    # ==========================================
    @app.view("add_group_modal")
    def handle_add_group_submission(ack, body, view, client):
        """
        グループ追加モーダルの「保存」ボタン押下時の処理（v2.22）。
        
        新しいグループを作成し、親モーダル（一覧）を更新します。
        """
        workspace_id = body["team"]["id"]
        vals = view["state"]["values"]
        
        try:
            from resources.services.group_service import GroupService
            
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
            
            # 成功
            ack()
            
            # 親モーダル（一覧）を更新
            _update_parent_admin_modal(client, body["view"]["previous_view_id"], workspace_id)
            
        except Exception as e:
            logger.error(f"グループ作成失敗(v2.22): {e}", exc_info=True)
            ack()

    # ==========================================
    # 3. v2.22: グループ編集モーダル「更新」押下
    # ==========================================
    @app.view("edit_group_modal")
    def handle_edit_group_submission(ack, body, view, client):
        """
        グループ編集モーダルの「更新」ボタン押下時の処理（v2.22）。
        
        グループ名とメンバーを更新し、親モーダル（一覧）を更新します。
        """
        workspace_id = body["team"]["id"]
        metadata = json.loads(view.get("private_metadata", "{}"))
        vals = view["state"]["values"]
        
        try:
            from resources.services.group_service import GroupService
            
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
            
            # 成功
            ack()
            
            # 親モーダル（一覧）を更新
            _update_parent_admin_modal(client, body["view"]["previous_view_id"], workspace_id)
            
        except Exception as e:
            logger.error(f"グループ更新失敗(v2.22): {e}", exc_info=True)
            ack()

    # ==========================================
    # 4. v2.22: 削除確認モーダル「削除する」押下
    # ==========================================
    @app.view("delete_confirm_modal")
    def handle_delete_confirm_submission(ack, body, view, client):
        """
        削除確認モーダルの「削除する」ボタン押下時の処理（v2.22）。
        
        グループを削除し、親モーダル（一覧）を更新します。
        """
        workspace_id = body["team"]["id"]
        metadata = json.loads(view.get("private_metadata", "{}"))
        
        try:
            from resources.services.group_service import GroupService
            
            group_service = GroupService()
            
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
            
            # 成功
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
        from resources.views.modal_views import create_admin_settings_modal
        
        group_service = GroupService()
        workspace_service = WorkspaceService()
        
        # 最新データを取得
        admin_ids = workspace_service.get_admin_ids(workspace_id)
        groups = group_service.get_all_groups(workspace_id)
        
        # 2. 【ここが漏れていました】表示名マップを生成
        # すべてのグループメンバーと管理者のIDを抽出
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
                    # 表示名(display_name)を優先、なければ本名(real_name)
                    user_name_map[uid] = profile.get("display_name") or profile.get("real_name") or uid
            except Exception:
                user_name_map[uid] = uid # 失敗時はID

        # 3. マップを渡してモーダルを再生成
        view = create_admin_settings_modal(
            admin_ids=admin_ids, 
            groups=groups, 
            user_name_map=user_name_map  # ← これを渡す
        )
        
        # 更新
        client.views_update(view_id=view_id, view=view)
        logger.info(f"親モーダル更新成功(v2.22): Groups={len(groups)}")
    except Exception as e:
        logger.error(f"親モーダル更新失敗(v2.22): {e}", exc_info=True)