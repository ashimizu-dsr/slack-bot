"""
管理機能リスナー (Pub/Sub対応版)

このモジュールは、管理者向けのSlackイベントを受け取ります。
- レポート設定ショートカット
- グループ追加・編集・削除
- デバッグ用レポートコマンド (/report)

Pub/Sub対応:
- handle_sync(): Slackイベントを受け取り、必要に応じてPub/Subに投げる（3秒以内）
- handle_async(): Pub/Subから戻ってきた後の重い処理
"""
import json
import logging
import re
import datetime

from resources.listeners.Listener import Listener
from resources.services.group_service import GroupService
from resources.services.workspace_service import WorkspaceService
from resources.templates.modals import create_admin_settings_modal
from resources.clients.slack_client import get_slack_client
from resources.constants import get_collection_name

logger = logging.getLogger(__name__)


class AdminListener(Listener):
    """管理機能リスナークラス"""
    
    def __init__(self):
        """AdminListenerを初期化します"""
        super().__init__()

    # ======================================================================
    # 同期処理: Slackイベントの受付（3秒以内に返す）
    # ======================================================================
    def handle_sync(self, app):
        """
        Slackイベントを受け取る処理を登録します。
        
        管理機能は基本的に軽量な操作が多いため、
        ほとんどの処理を同期的に実行します。
        
        Args:
            app: Slack Bolt Appインスタンス
        """
        
        # ==========================================
        # 1. グローバルショートカット「レポート設定」
        # ==========================================
        @app.shortcut("open_member_setup_modal")
        def on_admin_settings_shortcut(ack, body):
            """グローバルショートカット「レポート設定」のハンドラー"""
            team_id = body["team"]["id"]
            
            try:
                dynamic_client = get_slack_client(team_id)
                group_service = GroupService()
                
                # グループ取得（エラー時は初期値）
                try:
                    groups = group_service.get_all_groups(team_id)
                except Exception as e:
                    logger.error(f"グループ取得失敗: {e}", exc_info=True)
                    groups = []

                # ユーザー名マップの生成（＠付き問題を解決）
                all_uids = set()
                for g in (groups or []):
                    all_uids.update(g.get("member_ids", []))
                    all_uids.update(g.get("admin_ids", []))
                
                user_name_map = {}
                try:
                    users_data = dynamic_client.users_list()
                    if users_data["ok"]:
                        for u in users_data["members"]:
                            if u["id"] in all_uids:
                                profile = u.get("profile", {})
                                # ＠マークを除去して表示名を取得
                                name = (
                                    profile.get("display_name") or 
                                    u.get("real_name") or 
                                    u.get("name", "")
                                )
                                # 先頭の＠マークを除去
                                if name and name.startswith("@"):
                                    name = name[1:]
                                user_name_map[u["id"]] = name
                except Exception as e:
                    logger.error(f"ユーザーリスト取得失敗: {e}", exc_info=True)

                # モーダルを生成（データが空でもOK）
                view = create_admin_settings_modal(
                    groups=groups or [], 
                    user_name_map=user_name_map or {}
                )
                
                dynamic_client.views_open(trigger_id=body["trigger_id"], view=view)
                ack()
                
                logger.info(
                    f"レポート設定モーダル表示: Workspace={team_id}, Groups={len(groups or [])}"
                )
            except Exception as e:
                ack()
                logger.error(f"レポート設定モーダル表示失敗: {e}", exc_info=True)

        # ==========================================
        # 2. レポート設定モーダル「保存」押下（v2.3では何もしない）
        # ==========================================
        @app.view("admin_settings_modal")
        def on_admin_settings_submitted(ack, body, view):
            """
            レポート設定モーダル（一覧）の「保存」ボタン押下時の処理。
            
            v2.3では、admin_idsはグループごとに保存されるため、
            このモーダルでは何も保存しません。
            """
            ack()
            logger.info("レポート設定モーダル閉じる（v2.3では何も保存しない）")

        # ==========================================
        # 3. 「追加」ボタン押下
        # ==========================================
        @app.action("add_new_group")
        def on_add_group_button_clicked(ack, body, client):
            """「追加」ボタンのハンドラー"""
            from resources.templates.modals import create_add_group_modal
            
            try:
                view = create_add_group_modal()
                client.views_push(trigger_id=body["trigger_id"], view=view)
                logger.info("グループ追加モーダル表示")
                ack()
            except Exception as e:
                logger.error(f"グループ追加モーダル表示失敗: {e}", exc_info=True)
                ack()

        # ==========================================
        # 4. グループ追加モーダル「保存」押下
        # ==========================================
        @app.view("add_group_modal")
        def on_add_group_submitted(ack, body, view, client):
            """グループ追加モーダルの「保存」ボタン押下時の処理"""
            workspace_id = body["team"]["id"]
            vals = view["state"]["values"]
            
            try:
                group_service = GroupService()
                
                # 入力値を取得
                admin_ids = vals["admin_block"]["admin_select"].get("selected_users", [])
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
                    admin_ids=admin_ids,
                    created_by=body["user"]["id"]
                )
                logger.info(f"グループ作成: {group_name}, Members={len(member_ids)}, Admins={len(admin_ids)}")
                
                ack()
                
                # 親モーダル（一覧）を更新
                self._update_parent_admin_modal(client, body["view"]["previous_view_id"], workspace_id)
                
            except Exception as e:
                logger.error(f"グループ作成失敗: {e}", exc_info=True)
                ack()

        # ==========================================
        # 5. オーバーフローメニュー（...）押下
        # ==========================================
        @app.action("group_overflow_action")
        def on_group_overflow_menu_selected(ack, body, client):
            """オーバーフローメニュー（...）のハンドラー"""
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
                    try:
                        group = group_service.get_group_by_id(workspace_id, group_id)
                        logger.info(f"編集用グループ取得: {group_id}, データ: {group}")
                    except Exception as e:
                        logger.error(f"グループ取得失敗: {e}", exc_info=True)
                        group = None
                    
                    if not group:
                        logger.error(f"グループが見つかりません: {group_id}")
                        ack()
                        return
                    
                    admin_ids_for_modal = group.get("admin_ids", [])
                    logger.info(f"モーダルに渡すadmin_ids: {admin_ids_for_modal}")
                    
                    view = create_edit_group_modal(
                        group_id=group.get("group_id", group_id),
                        group_name=group.get("name", ""),
                        member_ids=group.get("member_ids", []),
                        admin_ids=admin_ids_for_modal
                    )
                    
                    client.views_push(trigger_id=body["trigger_id"], view=view)
                    logger.info(f"編集モーダル表示: {group_id}")
                    
                elif action_type == "delete":
                    # 削除確認モーダルを表示
                    try:
                        group = group_service.get_group_by_id(workspace_id, group_id)
                    except Exception as e:
                        logger.error(f"グループ取得失敗: {e}", exc_info=True)
                        group = None
                    
                    if not group:
                        logger.error(f"グループが見つかりません: {group_id}")
                        ack()
                        return
                    
                    view = create_member_delete_confirm_modal(
                        group_id=group.get("group_id", group_id),
                        group_name=group.get("name", "")
                    )
                    
                    client.views_push(trigger_id=body["trigger_id"], view=view)
                    logger.info(f"削除確認モーダル表示: {group_id}")
                
                ack()
                    
            except Exception as e:
                logger.error(f"オーバーフローメニュー処理失敗: {e}", exc_info=True)
                ack()

        # ==========================================
        # 6. グループ編集モーダル「保存」押下
        # ==========================================
        @app.view("edit_group_modal")
        def on_edit_group_submitted(ack, body, view, client):
            """グループ編集モーダルの「保存」ボタン押下時の処理"""
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
                admin_ids = vals["admin_block"]["admin_select"].get("selected_users", [])
                group_name_raw = vals["name_block"]["name_input"].get("value", "")
                group_name = group_name_raw.strip() if group_name_raw else ""
                member_ids = vals["members_block"]["members_select"].get("selected_users", [])
                
                # デバッグログ
                logger.info(f"グループ編集：取得した値 - admin_ids={admin_ids}, name={group_name}, members={member_ids}")
                logger.info(f"vals構造: {json.dumps(vals, indent=2, ensure_ascii=False)}")
                
                # バリデーション
                if not group_name:
                    ack(response_action="errors", errors={
                        "name_block": "⚠️ グループ名称を入力してください。"
                    })
                    return
                
                # グループを更新
                group_service.update_group(
                    workspace_id=workspace_id,
                    group_id=group_id,
                    name=group_name,
                    member_ids=member_ids,
                    admin_ids=admin_ids
                )
                logger.info(f"グループ更新: {group_name} ({group_id}), Members={len(member_ids)}, Admins={len(admin_ids)}")
                
                ack()
                
                # 親モーダル（一覧）を更新
                self._update_parent_admin_modal(client, body["view"]["previous_view_id"], workspace_id)
                
            except Exception as e:
                logger.error(f"グループ更新失敗: {e}", exc_info=True)
                ack()

        # ==========================================
        # 7. 削除確認モーダル「削除する」押下
        # ==========================================
        @app.view("delete_confirm_modal")
        def on_delete_group_confirmed(ack, body, view, client):
            """削除確認モーダルの「削除する」ボタン押下時の処理"""
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
                group_ref = db.collection(get_collection_name("groups")).document(workspace_id)\
                              .collection(get_collection_name("groups")).document(group_id)
                group_ref.delete()
                logger.info(f"グループ削除: {group_name} ({group_id})")
                
                ack()
                
                # 親モーダル（一覧）を更新
                self._update_parent_admin_modal(client, body["view"]["previous_view_id"], workspace_id)
                
            except Exception as e:
                logger.error(f"グループ削除失敗: {e}", exc_info=True)
                ack()

        # ==========================================
        # 8. /report スラッシュコマンド（デバッグ用）
        # ==========================================
        @app.command("/report")
        def on_report_command(ack, command, client):
            """
            /report スラッシュコマンドのハンドラー。
            
            DM限定で、指定された日付の全グループの勤怠状況をレポートします。
            """
            ack()
            
            team_id = command.get("team_id")
            user_id = command.get("user_id")
            channel_id = command.get("channel_id")
            text = (command.get("text") or "").strip()
            
            try:
                dynamic_client = get_slack_client(team_id)
                
                # DM判定（channel_idがDで始まるか確認）
                if not channel_id.startswith("D"):
                    dynamic_client.chat_postEphemeral(
                        channel=channel_id,
                        user=user_id,
                        text="⚠️ このコマンドはDM（ダイレクトメッセージ）でのみ使用可能です。"
                    )
                    logger.warning(f"/report コマンドがDM以外で実行されました: User={user_id}, Channel={channel_id}")
                    return
                
                # 日付のバリデーション（YYYYMMDD形式）
                if not re.match(r'^\d{8}$', text):
                    dynamic_client.chat_postMessage(
                        channel=channel_id,
                        text=(
                            "⚠️ 日付の形式が不正です。\n"
                            "正しい形式: `YYYYMMDD`（例: `/report 20260127`）"
                        )
                    )
                    logger.warning(f"/report コマンドの日付形式エラー: {text}")
                    return
                
                # 日付をYYYY-MM-DD形式に変換
                target_date = f"{text[0:4]}-{text[4:6]}-{text[6:8]}"
                
                # 日付の妥当性チェック
                try:
                    datetime.datetime.strptime(target_date, "%Y-%m-%d")
                except ValueError:
                    dynamic_client.chat_postMessage(
                        channel=channel_id,
                        text=f"⚠️ 無効な日付です: {text}"
                    )
                    logger.warning(f"/report コマンドの日付が無効: {text}")
                    return
                
                logger.info(f"/report コマンド実行: User={user_id}, Date={target_date}")
                
                # レポート生成（非同期処理へ）
                self.publish_to_worker(
                    team_id=team_id,
                    event={
                        "type": "report_command",
                        "user_id": user_id,
                        "channel_id": channel_id,
                        "target_date": target_date
                    }
                )
                
                # 即座にフィードバック
                dynamic_client.chat_postMessage(
                    channel=channel_id,
                    text=f"📊 {target_date} のレポートを生成中です..."
                )
                
            except Exception as e:
                logger.error(f"/report コマンド処理失敗: {e}", exc_info=True)

    # ======================================================================
    # 非同期処理: Pub/Subから戻ってきた後の重い処理
    # ======================================================================
    def handle_async(self, team_id: str, event: dict):
        """
        Pub/Subから戻ってきた後の重い処理を実行します。
        
        Args:
            team_id: ワークスペースID
            event: イベントデータ
        """
        event_type = event.get("type")
        
        try:
            if event_type == "report_command":
                self._generate_debug_report(team_id, event)
            else:
                logger.info(f"AdminListener.handle_async: 未処理のイベントタイプ ({event_type})")
        except Exception as e:
            logger.error(f"AdminListener非同期処理エラー ({event_type}): {e}", exc_info=True)

    # ======================================================================
    # プライベートメソッド
    # ======================================================================
    def _generate_debug_report(self, team_id: str, event: dict):
        """
        デバッグ用レポートを生成してDMで送信します。
        
        Args:
            team_id: ワークスペースID
            event: イベントデータ（user_id, channel_id, target_dateを含む）
        """
        user_id = event.get("user_id")
        channel_id = event.get("channel_id")
        target_date = event.get("target_date")
        
        try:
            client = get_slack_client(team_id)
            group_service = GroupService()
            
            # 全グループを取得
            groups = group_service.get_all_groups(team_id)
            
            if not groups:
                client.chat_postMessage(
                    channel=channel_id,
                    text="⚠️ グループが登録されていません。"
                )
                return
            
            # 指定日の全勤怠データを取得
            from resources.shared.db import get_today_records
            all_records = get_today_records(team_id, target_date)
            
            # user_id -> record のマップを作成
            record_map = {r["user_id"]: r for r in all_records}
            
            # レポートを生成
            report_blocks = [
                {
                    "type": "header",
                    "text": {
                        "type": "plain_text",
                        "text": f"{target_date}の勤怠"
                    }
                },
                {
                    "type": "divider"
                }
            ]
            
            # グループごとに集計
            for group in groups:
                group_name = group.get("name", "無名グループ")
                member_ids = group.get("member_ids", [])
                
                # グループ名
                report_blocks.append({
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"*{group_name}*"
                    }
                })
                
                # メンバーの勤怠状況
                if not member_ids:
                    report_blocks.append({
                        "type": "context",
                        "elements": [{
                            "type": "mrkdwn",
                            "text": "_メンバーが登録されていません_"
                        }]
                    })
                else:
                    member_lines = []
                    for member_id in member_ids:
                        if member_id in record_map:
                            record = record_map[member_id]
                            status = record.get("status", "未登録")
                            note = record.get("note", "")
                            
                            # ステータスの日本語化
                            status_jp = self._translate_status(status)
                            
                            if note:
                                member_lines.append(f"• <@{member_id}>: {status_jp} ({note})")
                            else:
                                member_lines.append(f"• <@{member_id}>: {status_jp}")
                        else:
                            member_lines.append(f"• <@{member_id}>: _未登録_")
                    
                    report_blocks.append({
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": "\n".join(member_lines)
                        }
                    })
                
                report_blocks.append({"type": "divider"})
            
            # レポートを送信
            client.chat_postMessage(
                channel=channel_id,
                blocks=report_blocks,
                text=f"{target_date}の勤怠"
            )
            
            logger.info(f"デバッグレポート送信完了: User={user_id}, Date={target_date}, Groups={len(groups)}")
            
        except Exception as e:
            logger.error(f"デバッグレポート生成失敗: {e}", exc_info=True)
            try:
                client = get_slack_client(team_id)
                client.chat_postMessage(
                    channel=channel_id,
                    text=f"⚠️ レポートの生成に失敗しました: {str(e)}"
                )
            except:
                pass

    def _translate_status(self, status: str) -> str:
        """
        ステータスを日本語に変換します。
        
        Args:
            status: ステータスコード（late, vacation等）
            
        Returns:
            日本語のステータス名
        """
        status_map = {
            "vacation": "休暇（全日）",
            "vacation_am": "午前休",
            "vacation_pm": "午後休",
            "vacation_hourly": "時間休",
            "late": "遅刻",
            "late_delay": "遅刻（遅延）",
            "early_leave": "早退",
            "out": "外出",
            "remote": "在宅",
            "shift": "シフト",
            "other": "その他"
        }
        return status_map.get(status, status)

    def _update_parent_admin_modal(self, client, view_id, workspace_id):
        """
        親モーダル（レポート設定一覧）を最新データで更新します。
        
        Args:
            client: Slack client（マルチテナント対応済み）
            view_id: 更新対象のview_id
            workspace_id: ワークスペースID
        """
        try:
            group_service = GroupService()
            
            # グループ取得（エラー時は初期値）
            try:
                groups = group_service.get_all_groups(workspace_id)
            except Exception as e:
                logger.error(f"グループ取得失敗（更新時）: {e}", exc_info=True)
                groups = []
            
            # 表示名マップを生成（＠付き問題を解決）
            all_user_ids = set()
            for g in (groups or []):
                all_user_ids.update(g.get("member_ids", []))
                all_user_ids.update(g.get("admin_ids", []))
                
            user_name_map = {}
            for uid in all_user_ids:
                try:
                    # ユーザー情報を取得して表示名をマップに格納
                    user_info = client.users_info(user=uid)
                    if user_info["ok"]:
                        user = user_info["user"]
                        profile = user.get("profile", {})
                        name = (
                            profile.get("display_name") or 
                            user.get("real_name") or 
                            user.get("name", "")
                        )
                        # 先頭の＠マークを除去
                        if name and name.startswith("@"):
                            name = name[1:]
                        user_name_map[uid] = name
                except Exception:
                    user_name_map[uid] = uid

            # モーダルを再生成（データが空でもOK）
            view = create_admin_settings_modal(
                groups=groups or [], 
                user_name_map=user_name_map or {}
            )
            
            # 更新
            client.views_update(view_id=view_id, view=view)
            logger.info(f"親モーダル更新成功: Groups={len(groups or [])}")
        except Exception as e:
            logger.error(f"親モーダル更新失敗: {e}", exc_info=True)
