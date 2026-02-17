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
from typing import List, Dict, Any
import os
from resources.listeners.Listener import Listener
from resources.services.group_service import GroupService
from resources.services.workspace_service import WorkspaceService
from resources.templates.modals import create_admin_settings_modal
from resources.clients.slack_client import get_slack_client
from resources.constants import get_collection_name, APP_ENV

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
            # 最優先: 3秒以内にSlackへ応答
            ack()
            
            team_id = body["team"]["id"]
            
            try:
                dynamic_client = get_slack_client(team_id)
                group_service = GroupService()
                
                # 1. まず空のモーダルを即座に開く
                view = create_admin_settings_modal(
                    groups=[], 
                    user_name_map={},
                    channels=[],
                    selected_channel_id=None
                )
                
                response = dynamic_client.views_open(trigger_id=body["trigger_id"], view=view)
                
                logger.info(f"レポート設定モーダル表示: Workspace={team_id}")
                
                # 2. モーダルを開いた後、データを取得して1回だけ更新
                if response["ok"]:
                    view_id = response["view"]["id"]
                    
                    # グループ取得
                    try:
                        groups = group_service.get_all_groups(team_id)
                    except Exception as e:
                        logger.error(f"グループ取得失敗: {e}", exc_info=True)
                        groups = []
                    
                    # チャンネル一覧取得
                    try:
                        channels_response = dynamic_client.users_conversations(
                            types="public_channel,private_channel",
                            exclude_archived=True,
                            limit=200
                        )
                        if channels_response["ok"]:
                            channels = [
                                {"id": ch["id"], "name": ch["name"]}
                                for ch in channels_response["channels"]
                            ]
                        else:
                            logger.error(f"チャンネル一覧取得失敗: {channels_response.get('error')}")
                            channels = []
                    except Exception as e:
                        logger.error(f"チャンネル一覧取得エラー: {e}", exc_info=True)
                        channels = []
                    
                    # 現在のレポート送信先チャンネルを取得
                    from resources.shared.db import get_workspace_config
                    workspace_config = get_workspace_config(team_id)
                    selected_channel_id = workspace_config.get("report_channel_id") if workspace_config else None
                    
                    # ユーザー名も一緒に取得
                    user_name_map = {}
                    if groups:
                        user_name_map = self._fetch_user_names(dynamic_client, groups)
                    
                    # 完全なデータで1回だけ更新
                    updated_view = create_admin_settings_modal(
                        groups=groups, 
                        user_name_map=user_name_map,
                        channels=channels,
                        selected_channel_id=selected_channel_id
                    )
                    
                    try:
                        dynamic_client.views_update(
                            view_id=view_id,
                            hash=response["view"]["hash"],
                            view=updated_view
                        )
                        logger.info(
                            f"モーダル更新完了: Groups={len(groups)}, Users={len(user_name_map)}, Channels={len(channels)}"
                        )
                    except Exception as e:
                        logger.error(f"モーダル更新失敗: {e}", exc_info=True)
                    
            except Exception as e:
                logger.error(f"レポート設定モーダル表示失敗: {e}", exc_info=True)

        # ==========================================
        # 2. レポート設定モーダル「保存」押下（v2.4でチャンネル設定保存を追加）
        # ==========================================
        @app.view("admin_settings_modal")
        def on_admin_settings_submitted(ack, body, view):
            """
            レポート設定モーダル（一覧）の「保存」ボタン押下時の処理。
            
            v2.4では、レポート送信先チャンネルを保存します。
            """
            workspace_id = body["team"]["id"]
            
            try:
                # チャンネル選択を取得
                vals = view["state"]["values"]
                
                report_channel_id = None
                if "report_channel_block" in vals:
                    selected_option = vals["report_channel_block"]["report_channel_select"].get("selected_option")
                    if selected_option:
                        report_channel_id = selected_option["value"]
                
                # Firestoreの workspaces コレクションに保存
                from resources.shared.db import get_workspace_config
                from google.cloud import firestore
                
                # 空文字列チェック
                db_name = APP_ENV.strip() if APP_ENV and APP_ENV.strip() else "develop"
                db = firestore.Client(database=db_name)
                workspace_ref = db.collection(get_collection_name("workspaces")).document(workspace_id)
                
                # 既存の設定を取得して更新
                workspace_ref.set({
                    "report_channel_id": report_channel_id or ""
                }, merge=True)
                
                logger.info(f"レポート送信先チャンネル保存: Workspace={workspace_id}, Channel={report_channel_id}")
                ack()
                
            except Exception as e:
                logger.error(f"レポート送信先チャンネル保存失敗: {e}", exc_info=True)
                ack()

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
                # 空文字列チェック
                db_name = APP_ENV.strip() if APP_ENV and APP_ENV.strip() else "develop"
                db = firestore.Client(database=db_name)
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
        
        チャンネルレポートと同じフォーマットで表示しますが、以下の違いがあります：
        - 通知先（admin_ids）は表示しない
        - 該当者のない区分も「なし」として表示する
        
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
            all_today_records = get_today_records(team_id, target_date)
            attendance_lookup = {r["user_id"]: r for r in all_today_records}
            
            # 日付フォーマットの準備
            try:
                dt = datetime.datetime.strptime(target_date, "%Y-%m-%d").date()
            except:
                dt = datetime.date.today()
                logger.warning(f"日付のパースに失敗したため今日の日付を使用: {dt}")
            
            weekday_list = ["月", "火", "水", "木", "金", "土", "日"]
            month_day = dt.strftime('%m/%d')
            weekday = weekday_list[dt.weekday()]
            
            # 全メンバーのIDを抽出（名前解決用）
            all_member_ids = set()
            for g in groups:
                all_member_ids.update(g.get("member_ids", []))
            
            # IDから名前への変換マップを作成
            user_name_map = {}
            try:
                response = client.users_list()
                if response["ok"]:
                    for user in response["members"]:
                        if user["id"] in all_member_ids:
                            profile = user.get("profile", {})
                            name = (
                                profile.get("display_name") or 
                                user.get("real_name") or 
                                user.get("name", "")
                            )
                            # ＠マークを除去
                            if name and name.startswith("@"):
                                name = name[1:]
                            user_name_map[user["id"]] = name
                
                # users_listで取得できなかったユーザーを個別に取得
                missing_user_ids = all_member_ids - set(user_name_map.keys())
                if missing_user_ids:
                    logger.info(f"レポート生成: users_listで取得できなかったユーザーを個別取得: {len(missing_user_ids)}名")
                    for user_id in missing_user_ids:
                        try:
                            user_info_response = client.users_info(user=user_id)
                            if user_info_response["ok"]:
                                user = user_info_response["user"]
                                profile = user.get("profile", {})
                                name = (
                                    profile.get("display_name") or 
                                    user.get("real_name") or 
                                    user.get("name", "")
                                )
                                # ＠マークを除去
                                if name and name.startswith("@"):
                                    name = name[1:]
                                user_name_map[user_id] = name
                            else:
                                # 取得失敗の場合はユーザーIDをそのまま使用
                                user_name_map[user_id] = user_id
                        except Exception as e:
                            # エラーの場合もユーザーIDをそのまま使用
                            user_name_map[user_id] = user_id
                            logger.error(f"レポート生成: ユーザー情報取得例外: {user_id}, エラー: {e}")
            except Exception as e:
                logger.error(f"ユーザー名取得失敗: {e}", exc_info=True)
            
            # グループごとにレポートを生成
            for group in groups:
                group_name = group.get("name", "無名グループ")
                member_ids = group.get("member_ids", [])
                
                # レポートブロックの構築
                blocks = []
                
                # タイトル（グループ名を含む）
                blocks.append({
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": f"*{month_day}({weekday})の勤怠（{group_name}）*"}
                })
                blocks.append({"type": "divider"})
                
                # ステータスごとにグルーピング
                status_map = {}
                for uid in member_ids:
                    if uid in attendance_lookup:
                        record = attendance_lookup[uid]
                        st = record.get('status', 'other')
                        display_name = user_name_map.get(uid, uid)
                        note = record.get('note', '')
                        
                        if st not in status_map:
                            status_map[st] = []
                        
                        # 備考がある場合はカッコ内に追加
                        if note:
                            status_map[st].append(f"{display_name}（{note}）")
                        else:
                            status_map[st].append(display_name)
                
                # 区分の定義順（該当者がいない場合も「なし」で表示）
                status_order = [
                    ("vacation", "全休"),
                    ("vacation_am", "AM休"),
                    ("vacation_pm", "PM休"),
                    ("vacation_hourly", "時間休"),
                    ("late_delay", "電車遅延"),
                    ("late", "遅刻"),
                    ("remote", "在宅"),
                    ("out", "外出"),
                    ("shift", "シフト勤務"),
                    ("early_leave", "早退"),
                    ("other", "その他")
                ]
                
                # 区分ごとの区切り位置（この区分の後にdividerを入れる）
                divider_after = {"vacation_hourly", "late", "remote", "out", "shift", "early_leave", "other"}
                
                for status_key, status_label in status_order:
                    if status_key in status_map:
                        users_text = " \n\t".join(status_map[status_key])
                        blocks.append({
                            "type": "section",
                            "text": {"type": "mrkdwn", "text": f"*{status_label}：* \n\t{users_text}"}
                        })
                    else:
                        # 該当者なしの場合も表示
                        blocks.append({
                            "type": "section",
                            "text": {"type": "mrkdwn", "text": f"*{status_label}：* \n\tなし"}
                        })
                    
                    # 指定された区分の後にdividerを追加
                    if status_key in divider_after:
                        blocks.append({"type": "divider"})
                
                # レポートを送信
                try:
                    client.chat_postMessage(
                        channel=channel_id,
                        blocks=blocks,
                        text=f"{group_name}の{month_day}({weekday})の勤怠"
                    )
                    logger.info(f"デバッグレポート送信成功: Group={group_name}, Date={target_date}")
                except Exception as e:
                    logger.error(f"グループレポート送信エラー: Group={group_name}, {e}")
            
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
            
            # チャンネル一覧取得
            try:
                channels_response = client.users_conversations(
                    types="public_channel,private_channel",
                    exclude_archived=True,
                    limit=200
                )
                if channels_response["ok"]:
                    channels = [
                        {"id": ch["id"], "name": ch["name"]}
                        for ch in channels_response["channels"]
                    ]
                else:
                    logger.error(f"チャンネル一覧取得失敗: {channels_response.get('error')}")
                    channels = []
            except Exception as e:
                logger.error(f"チャンネル一覧取得エラー: {e}", exc_info=True)
                channels = []
            
            # 現在のレポート送信先チャンネルを取得
            from resources.shared.db import get_workspace_config
            workspace_config = get_workspace_config(workspace_id)
            selected_channel_id = workspace_config.get("report_channel_id") if workspace_config else None
            
            # キャンセルで戻る時は、時間的余裕があるのでユーザー名を取得
            user_name_map = self._fetch_user_names(client, groups)

            # モーダルを再生成（データが空でもOK）
            view = create_admin_settings_modal(
                groups=groups or [], 
                user_name_map=user_name_map,
                channels=channels,
                selected_channel_id=selected_channel_id
            )
            
            # 更新
            client.views_update(view_id=view_id, view=view)
            logger.info(f"親モーダル更新成功: Groups={len(groups or [])}, Channels={len(channels)}")
        except Exception as e:
            logger.error(f"親モーダル更新失敗: {e}", exc_info=True)
    
    def _fetch_user_names(self, client, groups: List[Dict]) -> Dict[str, str]:
        """
        グループ内のユーザー名を取得します（＠なしのプレーンテキスト）。
        
        Args:
            client: Slack client
            groups: グループ情報のリスト
            
        Returns:
            user_id -> 表示名 のマッピング辞書
        """
        user_name_map = {}
        
        try:
            # 必要なユーザーIDを収集
            all_user_ids = set()
            for g in (groups or []):
                all_user_ids.update(g.get("member_ids", []))
                all_user_ids.update(g.get("admin_ids", []))
            
            if not all_user_ids:
                return user_name_map
            
            # users_listで全ユーザー取得（ページネーション対応）
            cursor = None
            while True:
                response = client.users_list(cursor=cursor, limit=200)
                
                if response["ok"]:
                    for user in response["members"]:
                        if user["id"] in all_user_ids:
                            profile = user.get("profile", {})
                            name = (
                                profile.get("display_name") or 
                                user.get("real_name") or 
                                user.get("name", "")
                            )
                            # ＠マークを除去
                            if name and name.startswith("@"):
                                name = name[1:]
                            user_name_map[user["id"]] = name
                    
                    # 次のページがあるか確認
                    cursor = response.get("response_metadata", {}).get("next_cursor")
                    if not cursor:
                        break
                else:
                    logger.error(f"users_list APIエラー: {response.get('error')}")
                    break
            
            # users_listで取得できなかったユーザーを個別に取得
            # （ゲストユーザー、無効化されたユーザーなどが該当）
            missing_user_ids = all_user_ids - set(user_name_map.keys())
            if missing_user_ids:
                logger.info(f"users_listで取得できなかったユーザーを個別取得: {len(missing_user_ids)}名")
                for user_id in missing_user_ids:
                    try:
                        user_info_response = client.users_info(user=user_id)
                        if user_info_response["ok"]:
                            user = user_info_response["user"]
                            profile = user.get("profile", {})
                            name = (
                                profile.get("display_name") or 
                                user.get("real_name") or 
                                user.get("name", "")
                            )
                            # ＠マークを除去
                            if name and name.startswith("@"):
                                name = name[1:]
                            user_name_map[user_id] = name
                            logger.debug(f"個別取得成功: {user_id} -> {name}")
                        else:
                            # 取得失敗の場合はユーザーIDをそのまま使用
                            user_name_map[user_id] = user_id
                            logger.warning(f"ユーザー情報取得失敗: {user_id}, エラー: {user_info_response.get('error')}")
                    except Exception as e:
                        # エラーの場合もユーザーIDをそのまま使用
                        user_name_map[user_id] = user_id
                        logger.error(f"ユーザー情報取得例外: {user_id}, エラー: {e}")
            
            logger.info(f"ユーザー名取得完了: {len(user_name_map)}名")
            
        except Exception as e:
            logger.error(f"ユーザー名取得失敗: {e}", exc_info=True)
        
        return user_name_map