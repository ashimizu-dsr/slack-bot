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

    # ==========================================
    # 4. v2.1: 設定モーダル「保存」押下（テキスト入力版・UPSERT方式）
    # ==========================================
    @app.view("member_settings_v2_1")
    def handle_member_settings_v2_1_save(ack, body, view):
        """
        v2.1設定モーダルの「保存」ボタン押下時の処理。
        
        v2.0との違い:
        - グループ名をテキスト入力から取得
        - find_group_by_name() で既存グループを検索
        - UPSERT処理（既存なら更新、新規なら作成）
        """
        workspace_id = body["team"]["id"]
        vals = view["state"]["values"]
        
        try:
            from resources.services.group_service import GroupService
            from resources.services.workspace_service import WorkspaceService
            from resources.shared.errors import ValidationError
            
            group_service = GroupService()
            workspace_service = WorkspaceService()
            
            # 1. 入力値の取得
            admin_ids = vals["admin_users_block"]["admin_users_select"].get("selected_users", [])
            group_name_raw = vals["group_name_input_block"]["group_name_input"].get("value", "")
            group_name = group_name_raw.strip() if group_name_raw else ""
            member_ids = vals["target_members_block"]["target_members_select"].get("selected_users", [])
            
            # 2. バリデーション
            if not admin_ids:
                ack(response_action="errors", errors={
                    "admin_users_block": "⚠️ 少なくとも1人の管理者を選択してください。"
                })
                return
            
            if not group_name and member_ids:
                ack(response_action="errors", errors={
                    "group_name_input_block": "⚠️ グループ名を入力してください。"
                })
                return
            
            # 3. グループのUPSERT
            if group_name:
                # 既存グループの検索
                existing_group = group_service.find_group_by_name(workspace_id, group_name)
                
                if existing_group:
                    # 更新
                    group_service.update_group_members(
                        workspace_id=workspace_id,
                        group_id=existing_group["group_id"],
                        member_ids=member_ids
                    )
                    logger.info(f"グループ更新(v2.1): {group_name}, Members={len(member_ids)}")
                else:
                    # 作成
                    group_service.create_group(
                        workspace_id=workspace_id,
                        name=group_name,
                        member_ids=member_ids,
                        created_by=body["user"]["id"]
                    )
                    logger.info(f"グループ作成(v2.1): {group_name}, Members={len(member_ids)}")
            
            # 4. 管理者の保存
            workspace_service.save_admin_ids(workspace_id, admin_ids)
            logger.info(f"管理者保存(v2.1): Workspace={workspace_id}, Admins={len(admin_ids)}")
            
            # 成功
            ack()
            logger.info(f"設定保存成功(v2.1): Workspace={workspace_id}")
            
        except ValidationError as ve:
            logger.warning(f"バリデーションエラー(v2.1): {ve}")
            ack(response_action="errors", errors={
                "admin_users_block": ve.user_message
            })
        except Exception as e:
            logger.error(f"設定保存失敗(v2.1): {e}", exc_info=True)
            ack()

    # ==========================================
    # 5. v2.2: メンバー設定モーダル「保存」押下（複数グループ一括管理版）
    # ==========================================
    @app.view("member_settings_v2")
    def handle_member_settings_v2_save(ack, body, view):
        """
        v2.2設定モーダルの「保存」ボタン押下時の処理。
        
        v2.1との違い:
        - 複数グループを一度に処理
        - 完全同期（作成・更新・削除）
        - name-as-id方式を使用
        
        処理フロー:
        1. モーダルから全グループを取得
        2. Firestoreから既存グループを取得
        3. 差分を計算（to_create, to_update, to_delete）
        4. 新規作成
        5. 更新
        6. 削除
        7. 管理者の保存
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
                ack(response_action="errors", errors={
                    "admin_users_block": "⚠️ 少なくとも1人の通知先を選択してください。"
                })
                return
            
            # 2. モーダルから全グループを取得
            group_count = metadata.get("group_count", 1)
            modal_groups = _extract_groups_from_modal(vals, group_count)
            
            # 3. バリデーション
            validation_errors = _validate_modal_input(admin_ids, modal_groups, group_count)
            if validation_errors:
                ack(response_action="errors", errors=validation_errors)
                return
            
            # 4. Firestoreから既存グループを取得
            existing_groups = group_service.get_all_groups(workspace_id)
            
            # 5. 差分を計算
            diff = _calculate_diff(modal_groups, existing_groups)
            
            # 6. 完全同期
            _sync_all_groups(
                group_service=group_service,
                workspace_id=workspace_id,
                modal_groups=modal_groups,
                diff=diff,
                user_id=body["user"]["id"]
            )
            
            # 7. 管理者の保存
            workspace_service.save_admin_ids(workspace_id, admin_ids)
            logger.info(f"管理者保存(v2.2): Workspace={workspace_id}, Admins={len(admin_ids)}")
            
            # 成功
            ack()
            logger.info(f"設定保存成功(v2.2): Workspace={workspace_id}, Groups={len(modal_groups)}, "
                       f"Created={len(diff['to_create'])}, Updated={len(diff['to_update'])}, Deleted={len(diff['to_delete'])}")
            
        except ValidationError as ve:
            logger.warning(f"バリデーションエラー(v2.2): {ve}")
            ack(response_action="errors", errors={
                "admin_users_block": ve.user_message
            })
        except Exception as e:
            logger.error(f"設定保存失敗(v2.2): {e}", exc_info=True)
            ack()


# ==========================================
# v2.2用ヘルパー関数
# ==========================================

def _extract_groups_from_modal(state_values, group_count):
    """
    モーダルの入力値から全グループを抽出します（v2.2）。
    
    Args:
        state_values: モーダルのstate.values
        group_count: グループ数
        
    Returns:
        グループ配列（空のグループは除外）
        [{"name": "営業1課", "member_ids": ["U001"]}, ...]
        
    Note:
        - グループ名が空の場合はスキップ
        - グループ名はsanitize_group_name()でサニタイズ
    """
    from resources.shared.utils import sanitize_group_name
    
    groups = []
    
    for i in range(1, group_count + 1):
        name_block = f"group_name_{i}"
        members_block = f"group_members_{i}"
        
        name_raw = state_values.get(name_block, {}).get("group_name_input", {}).get("value", "")
        name = name_raw.strip() if name_raw else ""
        
        member_ids = state_values.get(members_block, {}).get("target_members_select", {}).get("selected_users", [])
        
        # グループ名が空の場合はスキップ
        if not name:
            continue
        
        # グループ名のサニタイズ
        sanitized_name = sanitize_group_name(name)
        
        if not sanitized_name:
            continue
        
        groups.append({
            "name": sanitized_name,
            "member_ids": member_ids
        })
    
    return groups


def _validate_modal_input(admin_ids, modal_groups, group_count):
    """
    モーダル入力のバリデーション（v2.2）。
    
    Args:
        admin_ids: 管理者ID配列
        modal_groups: モーダルから抽出したグループ配列
        group_count: グループ数
        
    Returns:
        エラー辞書（エラーがない場合は空辞書）
        
    チェック項目:
    1. 通知先が必須: 少なくとも1人の管理者を選択
    2. グループ名の重複: モーダル内で同じ名前が複数ある場合はエラー
    """
    errors = {}
    
    # 2. グループ名の重複チェック
    names = [g["name"] for g in modal_groups]
    seen = set()
    duplicates = set()
    
    for name in names:
        if name in seen:
            duplicates.add(name)
        seen.add(name)
    
    if duplicates:
        # 重複しているグループのblock_idを特定してエラー表示
        # 最初の重複をエラーとして返す（複数エラーの表示は複雑になるため）
        duplicate_name = list(duplicates)[0]
        
        # 重複している最初のブロックIDを見つける
        for i in range(1, group_count + 1):
            for group in modal_groups:
                if group["name"] == duplicate_name:
                    errors[f"group_name_{i}"] = f"⚠️ グループ名「{duplicate_name}」が重複しています。"
                    break
            if errors:
                break
    
    return errors


def _calculate_diff(modal_groups, existing_groups):
    """
    モーダルとFirestoreの差分を計算します（v2.2）。
    
    Args:
        modal_groups: モーダルから取得したグループ配列
        existing_groups: Firestoreから取得した既存グループ配列
        
    Returns:
        差分辞書
        {
            "to_create": {"営業2課"},  # 新規作成
            "to_update": {"営業1課"},  # 更新
            "to_delete": {"開発課"}    # 削除
        }
    """
    modal_names = {g["name"] for g in modal_groups}
    
    # 既存グループのgroup_idを取得（v2.1のUUID形式とv2.2のname形式が混在する可能性）
    existing_names = set()
    for g in existing_groups:
        # group_idを優先、なければnameを使用
        group_id = g.get("group_id", g.get("name"))
        if group_id:
            existing_names.add(group_id)
    
    return {
        "to_create": modal_names - existing_names,
        "to_update": modal_names & existing_names,
        "to_delete": existing_names - modal_names
    }


def _sync_all_groups(group_service, workspace_id, modal_groups, diff, user_id):
    """
    モーダルの内容とFirestoreを完全同期します（v2.2）。
    
    Args:
        group_service: GroupServiceインスタンス
        workspace_id: ワークスペースID
        modal_groups: モーダルから取得したグループ配列
        diff: 差分辞書（_calculate_diffの戻り値）
        user_id: 実行ユーザーID
        
    処理:
    1. 新規作成: to_createに含まれるグループを作成
    2. 更新: to_updateに含まれるグループのメンバーを更新
    3. 削除: to_deleteに含まれるグループを削除
    """
    # 新規作成
    for name in diff["to_create"]:
        group = next((g for g in modal_groups if g["name"] == name), None)
        if not group:
            continue
        
        try:
            group_service.create_group_with_name_as_id(
                workspace_id=workspace_id,
                name=name,
                member_ids=group["member_ids"],
                created_by=user_id
            )
            logger.info(f"グループ作成(v2.2): {name}, Members={len(group['member_ids'])}")
        except Exception as e:
            logger.error(f"グループ作成失敗(v2.2): {name}, Error={e}", exc_info=True)
    
    # 更新
    for name in diff["to_update"]:
        group = next((g for g in modal_groups if g["name"] == name), None)
        if not group:
            continue
        
        try:
            group_service.update_group_with_name_as_id(
                workspace_id=workspace_id,
                group_id=name,
                member_ids=group["member_ids"]
            )
            logger.info(f"グループ更新(v2.2): {name}, Members={len(group['member_ids'])}")
        except Exception as e:
            logger.error(f"グループ更新失敗(v2.2): {name}, Error={e}", exc_info=True)
    
    # 削除
    for name in diff["to_delete"]:
        try:
            group_service.delete_group_with_name_as_id(
                workspace_id=workspace_id,
                group_id=name
            )
            logger.info(f"グループ削除(v2.2): {name}")
        except Exception as e:
            logger.error(f"グループ削除失敗(v2.2): {name}, Error={e}", exc_info=True)