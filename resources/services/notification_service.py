"""
Notification Service - Slackへのメッセージ送信・通知処理を担当
Firestore / Google Cloud 対応版 (マルチテナント・JST対応・上書き対応)
"""

import datetime
import logging
import time
import os
import json
import requests  # 上書き（response_url）送信に使用
from typing import Any, Dict, List, Optional

# クラウド対応済みの db 関数をインポート
from resources.shared.db import get_channel_members_with_section
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

    def notify_attendance_change(self, record: Any, message_text: str = "",
                                 options: Any = None, channel: Optional[str] = None,
                                 thread_ts: Optional[str] = None,
                                 is_update: bool = False,
                                 response_url: Optional[str] = None) -> None:
        """
        打刻・修正時の通知。
        response_url がある場合は既存メッセージを上書きし、ない場合は新規で Ephemeral 送信します。
        """
        # 循環参照を避けるためメソッド内でインポート
        from resources.views.modal_views import create_attendance_card_blocks
        
        # 修正後もボタンを出し続けるため、View側の blocks 作成ロジックにボタンを含める
        blocks = create_attendance_card_blocks(record, message_text, options, is_update=is_update)
        
        try:
            # record から情報を抽出
            if isinstance(record, dict):
                target_channel = channel or record.get('channel_id')
                user_id = record.get('user_id')
            else:
                target_channel = channel or getattr(record, 'channel_id', None)
                user_id = getattr(record, 'user_id', None)

            # 1. response_url がある場合は「上書き」処理
            if response_url:
                payload = {
                    "replace_original": "true",
                    "response_type": "ephemeral",
                    "blocks": blocks,
                    "text": "勤怠記録を更新しました"
                }
                res = requests.post(
                    response_url, 
                    data=json.dumps(payload), 
                    headers={'Content-Type': 'application/json'}
                )
                if res.status_code == 200:
                    logger.info("Message updated successfully via response_url")
                    return
                else:
                    logger.error(f"response_url による上書きに失敗: {res.status_code} {res.text}")
                    # 失敗した場合は通常の新規送信にフォールバックさせるため return しない

            # 2. 新規送信処理（Ephemeral: 本人にのみ表示）
            if not target_channel or not user_id:
                logger.warning(f"通知送信スキップ: ターゲット不明 (channel:{target_channel}, user:{user_id})")
                return

            self.client.chat_postEphemeral(
                channel=target_channel,
                user=user_id,
                blocks=blocks,
                text="勤怠を記録しました（あなたにのみ表示されています）"
            )
            logger.info(f"Individual ephemeral notification sent to user {user_id} in {target_channel}")
            
        except Exception as e:
            logger.error(f"通知処理失敗: {e}")

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