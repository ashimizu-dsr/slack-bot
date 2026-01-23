"""
通知サービス

このモジュールは、Slackへのメッセージ送信・通知処理を担当します。
勤怠カード、日次レポート、エラーメッセージなどの送信を統括します。
"""

import datetime
import logging
import time
import os
from typing import Any, Dict, List, Optional

# ステータス翻訳をインポート
from resources.constants import STATUS_TRANSLATION

logger = logging.getLogger(__name__)


class NotificationService:
    """
    Slack通知を管理するサービスクラス。
    
    勤怠記録の通知、日次レポートの配信、エラーメッセージの送信などを提供します。
    """

    def __init__(self, slack_client, attendance_service=None):
        """
        通知サービスの初期化。
        
        Args:
            slack_client: Slack Web API クライアント（slack_sdk.web.client.WebClient）
            attendance_service: AttendanceServiceインスタンス（レポート生成に使用）
        """
        self.client = slack_client
        self.attendance_service = attendance_service

    def notify_attendance_change(
        self, 
        record: Any, 
        channel: str,
        thread_ts: Optional[str] = None,
        is_update: bool = False,
        is_delete: bool = False
    ) -> None:
        """
        勤怠記録の変更をSlackに通知します。
        
        Args:
            record: AttendanceRecordオブジェクトまたは辞書
            channel: 投稿先チャンネルID
            thread_ts: スレッドのタイムスタンプ（スレッド返信する場合）
            is_update: 更新通知かどうか
            is_delete: 削除通知かどうか
            
        Note:
            - 削除の場合は簡易メッセージを投稿
            - 記録・更新の場合は勤怠カード（ボタン付き）を投稿
        """
        from resources.views.modal_views import create_attendance_card_blocks
        
        try:
            # 1. 削除通知の場合
            if is_delete:
                user_id = record.user_id if hasattr(record, 'user_id') else record.get('user_id')
                date_val = record.date if hasattr(record, 'date') else record.get('date')
                self.client.chat_postMessage(
                    channel=channel,
                    thread_ts=thread_ts,
                    text=f"勤怠連絡を取り消しました: {date_val}",
                    blocks=[{
                        "type": "context",
                        "elements": [{"type": "mrkdwn", "text": f"ⓘ <@{user_id}> さんの {date_val} の勤怠連絡を取り消しました"}]
                    }]
                )
                logger.info(f"削除通知を送信しました: User={user_id}, Date={date_val}")
                return

            # 2. 記録・更新通知の場合
            blocks = create_attendance_card_blocks(
                record, 
                is_update=is_update,
                show_buttons=True
            )
            
            msg_text = "勤怠記録を更新しました" if is_update else "勤怠を記録しました"
            
            self.client.chat_postMessage(
                channel=channel,
                thread_ts=thread_ts,
                blocks=blocks,
                text=msg_text
            )
            
            user_id = record.user_id if hasattr(record, 'user_id') else record.get('user_id')
            date_val = record.date if hasattr(record, 'date') else record.get('date')
            logger.info(f"勤怠カードを送信しました: User={user_id}, Date={date_val}, Update={is_update}")
            
        except Exception as e:
            logger.error(f"通知送信失敗: {e}", exc_info=True)

    def _get_bot_joined_channels(self) -> List[str]:
        """
        Botが参加しているチャンネルIDの一覧を動的に取得します。
        
        Returns:
            チャンネルIDの配列
            
        Note:
            公開チャンネルのみを取得します。
            プライベートチャンネルを含める場合は、types="private_channel"も追加する必要があります。
        """
        try:
            # Botが参加している公開チャンネルを一覧取得
            response = self.client.conversations_list(
                types="public_channel, private_channel",  # privateも追加
                exclude_archived=True
            )
            joined_channels = [
                c["id"] for c in response["channels"] 
                if c.get("is_member", False)
            ]
            logger.info(f"Bot参加チャンネル数: {len(joined_channels)}")
            return joined_channels
        except Exception as e:
            logger.error(f"チャンネル一覧取得失敗: {e}", exc_info=True)
            return []

    def send_daily_report(self, date_str: str, workspace_id: str = None) -> None: 
        """
        日次レポートを管理者に送信します（v2.0版）。
        
        Args:
            date_str: 対象日（YYYY-MM-DD形式）
            workspace_id: Slackワークスペースの一意ID（省略時は環境変数から取得）
            
        Note:
            v2.0では、グループ構造と管理者設定を使用してレポートを生成・送信します。
            旧方式の固定セクション構造には対応していません。
        """
        if not self.attendance_service:
            logger.error("attendance_service が未設定のためレポート送信不可。")
            return

        start_time = time.time()
        
        # workspace_idの取得
        if not workspace_id:
            workspace_id = os.environ.get("SLACK_WORKSPACE_ID", "GLOBAL_WS")
        
        # 1. 管理者と送信先チャンネルの取得
        from resources.services.workspace_service import WorkspaceService
        workspace_service = WorkspaceService()
        admin_ids = workspace_service.get_admin_ids(workspace_id)

        mention_text = " ".join([f"<@{uid}>" for uid in admin_ids]) if admin_ids else ""
        
        if not admin_ids:
            logger.warning(f"管理者が設定されていません: Workspace={workspace_id}")
            # 旧方式へのフォールバック（互換性のため）
            self._send_daily_report_legacy(date_str, workspace_id)
            return
        
        # 2. 送信先チャンネルの自動決定
        # まずBotが参加しているチャンネル一覧を取得
        joined_channels = self._get_bot_joined_channels()
        
        if joined_channels:
            # 参加している最初のチャンネルを送信先とする
            target_channel = joined_channels[0]
            logger.info(f"レポート送信先（自動判別）: {target_channel}")
        else:
            # どこにも参加していない場合は管理者にDM（バックアップ策）
            target_channel = None
            logger.warning("Botがどのチャンネルにも参加していないため、管理者DMへ送信します。")
        
        # 3. 日付タイトルの準備
        try:
            dt = datetime.date.fromisoformat(date_str)
        except:
            dt = datetime.date.today()
            logger.warning(f"日付のパースに失敗したため今日の日付を使用: {dt}")
            
        weekday_list = ["月", "火", "水", "木", "金", "土", "日"]
        title_date = f"{dt.strftime('%m/%d')}({weekday_list[dt.weekday()]})"
        
        # 4. グループ情報を取得
        from resources.services.group_service import GroupService
        group_service = GroupService()
        all_groups = group_service.get_all_groups(workspace_id)
        
        if not all_groups:
            logger.warning(f"グループが設定されていません: Workspace={workspace_id}")
            return

        # --- 【追加】名前表示用のキャッシュを作成 ---
        # 全グループに所属する全メンバーのIDを抽出
        all_member_ids = set()
        for g in all_groups:
            all_member_ids.update(g.get("member_ids", []))
        
        # IDから名前(real_name)への変換マップを作成
        user_name_map = {}
        for uid in all_member_ids:
            try:
                u_info = self.client.users_info(user=uid)
                # real_nameがなければname、それもなければIDを表示
                user_name_map[uid] = u_info["user"].get("real_name") or u_info["user"].get("name") or uid
            except:
                user_name_map[uid] = uid
        # ----------------------------------------

        # 5. レポートブロックの構築
        blocks = []
        if mention_text:
            blocks.append({
                "type": "section",
                "text": {"type": "mrkdwn", "text": mention_text}
            })
        
        blocks.append({
            "type": "header", 
            "text": {"type": "plain_text", "text": f"{title_date}の勤怠一覧"}
        })

        for group in all_groups:
            group_name = group.get("name", "不明なグループ")
            member_ids = group.get("member_ids", [])
            
            blocks.append({"type": "divider"})
            blocks.append({
                "type": "section", 
                "text": {"type": "mrkdwn", "text": f"*{group_name}*"}
            })
            
            # --- 【修正】メンションを飛ばさないように書き換え ---
            if not member_ids:
                content_text = "_メンバーが登録されていません_"
            else:
                records = []
                for user_id in member_ids:
                    record = self.attendance_service.get_specific_date_record(workspace_id, user_id, date_str)
                    if record:
                        records.append(record)
                
                if not records:
                    content_text = "_勤怠連絡はありません_"
                else:
                    records.sort(key=lambda x: x.get('status', ''))
                    lines = []
                    for r in records:
                        # <@ID> ではなくキャッシュした user_name_map から名前を取得
                        display_name = user_name_map.get(r['user_id'], r['user_id'])
                        status_name = STATUS_TRANSLATION.get(r['status'], r['status'])
                        note = f" ({r['note']})" if r.get('note') else ""
                        lines.append(f"• {display_name} - *{status_name}*{note}")
                    
                    content_text = "\n".join(lines)
            
            blocks.append({
                "type": "context", 
                "elements": [{"type": "mrkdwn", "text": content_text}]
            })

        # 6. メッセージ送信（全参加チャンネル対応）
        try:
            # Botが参加しているチャンネルをすべて取得
            joined_channels = self._get_bot_joined_channels()
            
            if joined_channels:
                for channel_id in joined_channels:
                    self.client.chat_postMessage(
                        channel=channel_id, 
                        blocks=blocks, 
                        text=f"{title_date}の勤怠一覧"
                    )
                    logger.info(f"レポート送信成功: {channel_id}")
            else:
                # どこにも参加していない場合は管理者にDM
                for admin_id in admin_ids:
                    dm = self.client.conversations_open(users=admin_id)
                    self.client.chat_postMessage(channel=dm["channel"]["id"], blocks=blocks)

        except Exception as e:
            logger.error(f"送信エラー: {e}")
        
        total_end = time.time()
        logger.info(f"レポート送信処理完了 所要時間: {total_end - start_time:.4f}秒")

    def _send_daily_report_legacy(self, date_str: str, workspace_id: str) -> None:
        """
        日次レポートを旧方式で送信します（互換性のため）。
        
        Args:
            date_str: 対象日（YYYY-MM-DD形式）
            workspace_id: Slackワークスペースの一意ID
            
        Note:
            v1.1以前の固定セクション構造を使用します。
            v2.0のグループ設定が未完了の場合のフォールバック処理です。
        """
        logger.info("旧方式でレポートを送信します")
        
        # 送信先の決定
        target_channels = []
        env_channel = os.environ.get("REPORT_CHANNEL_ID")
        if env_channel:
            target_channels = [env_channel]
        else:
            target_channels = self._get_bot_joined_channels()

        if not target_channels:
            logger.error("レポート送信先が見つかりません。")
            return

        # 日付タイトルの準備
        try:
            dt = datetime.date.fromisoformat(date_str)
        except:
            dt = datetime.date.today()
            
        weekday_list = ["月", "火", "水", "木", "金", "土", "日"]
        title_date = f"{dt.strftime('%m/%d')}({weekday_list[dt.weekday()]})"
        
        # 固定セクション構造
        all_sections = [
            ("sec_1", "1課"), ("sec_2", "2課"), ("sec_3", "3課"), ("sec_4", "4課"),
            ("sec_5", "5課"), ("sec_6", "6課"), ("sec_7", "7課"), ("sec_finance", "金融開発課")
        ]

        for channel_id in target_channels:
            try:
                # データの取得
                all_attendance_data = self.attendance_service.get_daily_report_data(
                    workspace_id, date_str, channel_id
                )

                # Blocksの構築
                blocks = [{
                    "type": "header", 
                    "text": {"type": "plain_text", "text": f"{title_date}の勤怠一覧"}
                }]

                for sec_id, sec_name in all_sections:
                    records = all_attendance_data.get(sec_id, [])
                    blocks.append({"type": "divider"})
                    blocks.append({
                        "type": "section", 
                        "text": {"type": "mrkdwn", "text": f"*{sec_name}*"}
                    })

                    if not records:
                        content_text = "_勤怠連絡はありません_"
                    else:
                        records.sort(key=lambda x: x.get('status', ''))
                        lines = [
                            f"• <@{r['user_id']}> - *{STATUS_TRANSLATION.get(r['status'], r['status'])}*"
                            f"{f' ({r['note']})' if r.get('note') else ''}" 
                            for r in records
                        ]
                        content_text = "\n".join(lines)

                    blocks.append({
                        "type": "context", 
                        "elements": [{"type": "mrkdwn", "text": content_text}]
                    })

                # メッセージ送信
                self.client.chat_postMessage(
                    channel=channel_id, 
                    blocks=blocks, 
                    text=f"{title_date}の勤怠一覧"
                )
                logger.info(f"レポート送信成功（旧方式）: {channel_id}")

            except Exception as e:
                logger.error(f"チャンネル {channel_id} へのレポート送信失敗: {e}", exc_info=True)