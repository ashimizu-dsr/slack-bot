"""
Notification Service - Slackへのメッセージ送信・通知処理を担当
Firestore / Google Cloud 対応版 (スレッド返信・チャンネル自動取得対応)
"""

import datetime
import logging
import time
import os
from typing import Any, Dict, List, Optional

# ステータス翻訳をインポート
from resources.constants import STATUS_TRANSLATION

# loggerの設定
logger = logging.getLogger(__name__)

class NotificationService:
    def __init__(self, slack_client, attendance_service=None):
        """
        初期化
        """
        self.client = slack_client
        self.attendance_service = attendance_service

    def notify_attendance_change(self, record: Any, 
                                 channel: str,
                                 thread_ts: Optional[str] = None,
                                 is_update: bool = False,
                                 is_delete: bool = False) -> None:
        """
        勤怠カード（記録/更新）または削除メッセージを送信する
        """
        from resources.views.modal_views import create_attendance_card_blocks
        
        try:
            # 1. 削除のみの場合（打ち消し線のみで、後の情報がない場合など）
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
                return

            # 2. 記録・更新の場合（打ち消し線があっても最終的な情報がある場合）
            # create_attendance_card_blocks を呼び出してボタン付きカードを作成
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
        except Exception as e:
            logger.error(f"通知送信失敗: {e}", exc_info=True)

    def _get_bot_joined_channels(self) -> List[str]:
        """
        Botが参加しているチャンネルIDの一覧を取得（動的な取得）
        """
        try:
            # Botが参加している公開チャンネルを一覧取得
            response = self.client.conversations_list(types="public_channel", filter_basic_slots=True)
            joined_channels = [
                c["id"] for c in response["channels"] 
                if c.get("is_member", False)
            ]
            return joined_channels
        except Exception as e:
            logger.error(f"チャンネル一覧取得失敗: {e}")
            return []

    def send_daily_report(self, date_str: str) -> None: 
        """
        【クラウド版】
        Botが参加している全チャンネルに対してレポートを送信します（環境変数不要）
        """
        if not self.attendance_service:
            logger.error("attendance_service が未設定のためレポート送信不可。")
            return

        start_time = time.time()
        
        # 1. 送信先の動的取得（Botが参加しているチャンネル）
        target_channels = self._get_bot_joined_channels()
        
        # フォールバック：もし動的取得が空なら環境変数を試す（移行期間用）
        if not target_channels:
            env_channel = os.environ.get("CHANNEL_ID")
            if env_channel:
                target_channels = [env_channel]

        if not target_channels:
            logger.error("レポート送信先が見つかりません。Botをチャンネルに招待してください。")
            return

        # 2. 日付タイトルの準備
        try:
            dt = datetime.date.fromisoformat(date_str)
        except:
            dt = datetime.date.today()
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

        workspace_id = os.environ.get("SLACK_WORKSPACE_ID", "GLOBAL_WS")

        for channel_id in target_channels:
            try:
                # データの取得
                all_attendance_data = self.attendance_service.get_daily_report_data(
                    workspace_id, date_str, channel_id
                )

                blocks = [{"type": "header", "text": {"type": "plain_text", "text": f"{title_date}の勤怠一覧"}}]

                for sec_id, sec_name in all_sections:
                    records = all_attendance_data.get(sec_id, [])
                    blocks.append({"type": "divider"})
                    blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": f"*{sec_name}*"}})

                    if not records:
                        content_text = "_勤怠連絡はありません_"
                    else:
                        records.sort(key=lambda x: x.get('status', ''))
                        lines = [f"• <@{r['user_id']}> - *{STATUS_TRANSLATION.get(r['status'], r['status'])}*{f' ({r['note']})' if r.get('note') else ''}" for r in records]
                        content_text = "\n".join(lines)

                    blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": content_text}]})

                self.client.chat_postMessage(channel=channel_id, blocks=blocks, text=f"{title_date}の勤怠一覧")
                logger.info(f"Daily report sent successfully to {channel_id}")

            except Exception as e:
                logger.error(f"チャンネル {channel_id} へのレポート送信中にエラー: {e}")

        total_end = time.time()
        logger.info(f"レポート送信処理完了 所要時間: {total_end - start_time:.4f}秒")