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
                types="public_channel", 
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

    def send_daily_report(self, date_str: str) -> None: 
        """
        日次レポートをBotが参加している全チャンネルに送信します。
        
        Args:
            date_str: 対象日（YYYY-MM-DD形式）
            
        Note:
            - Botが参加しているチャンネルを動的に取得して送信
            - 環境変数 REPORT_CHANNEL_ID があればそれを優先使用
            - 各セクション（課）ごとに勤怠記録を集約して表示
        """
        if not self.attendance_service:
            logger.error("attendance_service が未設定のためレポート送信不可。")
            return

        start_time = time.time()
        
        # 1. 送信先の決定（環境変数 > 動的取得）
        target_channels = []
        env_channel = os.environ.get("REPORT_CHANNEL_ID")
        if env_channel:
            target_channels = [env_channel]
            logger.info(f"レポート送信先（環境変数指定）: {env_channel}")
        else:
            target_channels = self._get_bot_joined_channels()
            logger.info(f"レポート送信先（動的取得）: {len(target_channels)}チャンネル")

        if not target_channels:
            logger.error("レポート送信先が見つかりません。Botをチャンネルに招待してください。")
            return

        # 2. 日付タイトルの準備
        try:
            dt = datetime.date.fromisoformat(date_str)
        except:
            dt = datetime.date.today()
            logger.warning(f"日付のパースに失敗したため今日の日付を使用: {dt}")
            
        weekday_list = ["月", "火", "水", "木", "金", "土", "日"]
        title_date = f"{dt.strftime('%m/%d')}({weekday_list[dt.weekday()]})"
        
        # 3. Firestoreからメンバー設定を取得
        from resources.shared.db import get_channel_members_with_section
        section_user_map, _ = get_channel_members_with_section()

        # 4. 各チャンネルに対してレポート生成と送信
        all_sections = [
            ("sec_1", "1課"), ("sec_2", "2課"), ("sec_3", "3課"), ("sec_4", "4課"),
            ("sec_5", "5課"), ("sec_6", "6課"), ("sec_7", "7課"), ("sec_finance", "金融開発課")
        ]

        # workspace_idの取得（環境変数または固定値）
        workspace_id = os.environ.get("SLACK_WORKSPACE_ID", "GLOBAL_WS")

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
                        # ステータスでソート
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
                logger.info(f"Daily report sent successfully to {channel_id}")

            except Exception as e:
                logger.error(f"チャンネル {channel_id} へのレポート送信中にエラー: {e}", exc_info=True)

        total_end = time.time()
        logger.info(f"レポート送信処理完了 所要時間: {total_end - start_time:.4f}秒")