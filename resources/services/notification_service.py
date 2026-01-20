"""
Notification Service - Slackへのメッセージ送信・通知処理を担当
打刻内容（全体公開）＋操作ボタン（本人限定）のハイブリッド構成
"""

import datetime
import logging
import time
import os
import json
import requests
from typing import Any, Dict, List, Optional

from resources.shared.db import get_channel_members_with_section
from resources.constants import STATUS_TRANSLATION

logger = logging.getLogger(__name__)

class NotificationService:
    def __init__(self, slack_client, attendance_service=None):
        self.client = slack_client
        self.attendance_service = attendance_service

    def notify_attendance_change(self, record: Any, message_text: str = "",
                                 options: Any = None, channel: Optional[str] = None,
                                 user_id: Optional[str] = None,
                                 response_url: Optional[str] = None,
                                 is_update: bool = False) -> None:
        """
        打刻・修正時の通知。
        - チャンネル全体には「打刻内容」を通知（ボタンなし）。
        - 本人には「修正・取消ボタン」をエフェメラルで通知。
        """
        # 循環参照を避けるためメソッド内でインポート
        from resources.views.modal_views import create_attendance_card_blocks
        
        try:
            # 1. データの整理
            if isinstance(record, dict):
                target_channel = channel or record.get('channel_id')
                target_user = user_id or record.get('user_id')
            else:
                target_channel = channel or getattr(record, 'channel_id', None)
                target_user = user_id or getattr(record, 'user_id', None)

            if not target_channel or not target_user:
                logger.warning("通知送信スキップ: ターゲットが不明です。")
                return

            # --- A. チャンネル全体への通知（ボタンなし） ---
            # create_attendance_card_blocks 内で buttons=False になるよう調整が必要
            # ※もし views 側で制御してなければ、ここで blocks を加工するか、引数を追加してください
            public_blocks = create_attendance_card_blocks(record, message_text, options, is_update=is_update, show_buttons=False)
            
            # 修正(is_update=True)の時は、二重投稿を避けるため全体通知はスキップしても良いかもしれません
            if not is_update:
                self.client.chat_postMessage(
                    channel=target_channel,
                    blocks=public_blocks,
                    text="勤怠が記録されました"
                )

            # --- B. 操作ボタンの通知（本人にのみ表示・上書き対応） ---
            # 本人用にはボタンありのブロックを作成
            private_blocks = create_attendance_card_blocks(record, message_text, options, is_update=is_update, show_buttons=True)

            if response_url:
                # 修正ボタン等が押された後の「上書き」
                payload = {
                    "replace_original": "true",
                    "response_type": "ephemeral",
                    "blocks": private_blocks,
                    "text": "勤怠記録を更新しました（あなたにのみ表示されています）"
                }
                requests.post(response_url, json=payload)
            else:
                # 新規打刻時の「本人専用ボタン」送信
                self.client.chat_postEphemeral(
                    channel=target_channel,
                    user=target_user,
                    blocks=private_blocks,
                    text="この投稿の修正・取消はこちらから行えます（あなたにのみ表示されています）"
                )

            logger.info(f"Hybrid notification sent: Public to {target_channel}, Private to {target_user}")

        except Exception as e:
            logger.error(f"通知処理失敗: {e}")

    # (send_daily_report は変更なし)

    def send_daily_report(self, date_str: str) -> None: 
        """
        【クラウド版】JSTを考慮し、動的なチャンネル指定でレポートを送信。
        """
        if not self.attendance_service:
            logger.error("attendance_service が未設定のためレポート送信不可。")
            return

        start_time = time.time()
        section_user_map, _ = get_channel_members_with_section()

        if not section_user_map:
            logger.info("レポート送信対象の設定が見つかりません。")
            return

        try:
            dt = datetime.date.fromisoformat(date_str)
        except Exception:
            dt = datetime.date.today()

        weekday_list = ["月", "火", "水", "木", "金", "土", "日"]
        title_date = f"{dt.strftime('%m/%d')}({weekday_list[dt.weekday()]})"
        
        all_sections = [
            ("sec_1", "1課"), ("sec_2", "2課"), ("sec_3", "3課"), ("sec_4", "4課"),
            ("sec_5", "5課"), ("sec_6", "6課"), ("sec_7", "7課"), ("sec_finance", "金融開発課")
        ]

        try:
            workspace_id = os.environ.get("SLACK_WORKSPACE_ID", "GLOBAL_WS")
            target_report_channel = os.environ.get("CHANNEL_ID")

            if not target_report_channel:
                logger.error("CHANNEL_ID 未設定。")
                return

            all_attendance_data = self.attendance_service.get_daily_report_data(
                workspace_id, date_str, target_report_channel
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

            self.client.chat_postMessage(channel=target_report_channel, blocks=blocks, text=f"{title_date}の勤怠一覧")
            logger.info(f"Daily report sent to {target_report_channel}")

        except Exception as e:
            logger.error(f"レポート送信エラー: {e}", exc_info=True)

        total_end = time.time()
        logger.info(f"レポート送信完了: {total_end - start_time:.4f}秒")