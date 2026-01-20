"""
Notification Service - Slackへのメッセージ送信・通知処理を担当
Firestore / Google Cloud 対応版 (マルチテナント・JST対応)
"""

import datetime
import logging
import time
import os
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
        :param slack_client: Slack WebClient
        :param attendance_service: 勤怠データ取得用のサービスインスタンス
        """
        self.client = slack_client
        self.attendance_service = attendance_service

    def notify_attendance_change(self, record: Any, message_text: str = "",
                                 options: Any = None, channel: Optional[str] = None,
                                 thread_ts: Optional[str] = None,
                                 is_update: bool = False) -> None:
        """
        打刻時の個別通知（本人にのみ「修正・削除」ボタンが見えるようにエフェメラル送信）
        """
        # 循環参照を避けるためメソッド内でインポート
        from resources.views.modal_views import create_attendance_card_blocks
        
        blocks = create_attendance_card_blocks(record, message_text, options, is_update=is_update)
        
        try:
            # record が辞書かオブジェクトかに応じて取得（動的にチャンネルを特定）
            if isinstance(record, dict):
                target_channel = channel or record.get('channel_id')
                user_id = record.get('user_id')
            else:
                target_channel = channel or getattr(record, 'channel_id', None)
                user_id = getattr(record, 'user_id', None)

            if not target_channel or not user_id:
                logger.warning(f"通知送信スキップ: ターゲット不明 (channel:{target_channel}, user:{user_id})")
                return

            # chat.postEphemeral を使用して「操作した本人」にのみボタンを表示する
            self.client.chat_postEphemeral(
                channel=target_channel,
                user=user_id,
                blocks=blocks,
                text="勤怠を記録しました（あなたにのみ表示されています）"
                # thread_ts は Ephemeral メッセージでは現在サポートされていないため除外
            )
            logger.info(f"Individual ephemeral notification sent to user {user_id} in {target_channel}")
        except Exception as e:
            logger.error(f"通知送信失敗: {e}")

    def send_daily_report(self, date_str: str) -> None: 
        """
        【クラウド版】
        JST（日本時間）を考慮し、動的なチャンネル指定でレポートを送信します。
        """
        if not self.attendance_service:
            logger.error("attendance_service が未設定のためレポート送信不可。")
            return

        start_time = time.time()
        
        # 1. Firestoreから配信対象を取得
        section_user_map, _ = get_channel_members_with_section()

        if not section_user_map:
            logger.info("レポート送信対象の設定が見つかりません。")
            return

        # 日付タイトルの準備 (JSTは環境変数 TZ=Asia/Tokyo で担保)
        try:
            dt = datetime.date.fromisoformat(date_str)
        except Exception:
            # date_strが不正な場合、現在の日付を使用
            dt = datetime.date.today()

        weekday_list = ["月", "火", "水", "木", "金", "土", "日"]
        title_date = f"{dt.strftime('%m/%d')}({weekday_list[dt.weekday()]})"
        
        all_sections = [
            ("sec_1", "1課"), ("sec_2", "2課"), ("sec_3", "3課"), ("sec_4", "4課"),
            ("sec_5", "5課"), ("sec_6", "6課"), ("sec_7", "7課"), ("sec_finance", "金融開発課")
        ]

        # 2. レポート送信
        try:
            # ワークスペースIDの特定
            workspace_id = os.environ.get("SLACK_WORKSPACE_ID", "GLOBAL_WS")
            
            # 定数からではなく、環境変数または設定から動的に取得するように変更
            # ※将来的に複数チャンネルに対応させる場合はDBから取得するロジックをここに書く
            target_report_channel = os.environ.get("CHANNEL_ID")

            if not target_report_channel:
                logger.error("CHANNEL_ID が設定されていないためレポートを送信できません。")
                return

            # データの取得
            all_attendance_data = self.attendance_service.get_daily_report_data(
                workspace_id, 
                date_str, 
                target_report_channel
            )

            blocks = [
                {
                    "type": "header",
                    "text": {"type": "plain_text", "text": f"{title_date}の勤怠一覧"}
                }
            ]

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
                    lines = []
                    for r in records:
                        s_jp = STATUS_TRANSLATION.get(r['status'], r['status'])
                        note_text = f" ({r['note']})" if r.get('note') else ""
                        lines.append(f"• <@{r['user_id']}> - *{s_jp}*{note_text}")
                    content_text = "\n".join(lines)

                blocks.append({
                    "type": "context",
                    "elements": [{"type": "mrkdwn", "text": content_text}]
                })

            # Slack送信
            self.client.chat_postMessage(
                channel=target_report_channel,
                blocks=blocks,
                text=f"{title_date}の勤怠一覧"
            )
            logger.info(f"Daily report sent successfully to {target_report_channel}")

        except Exception as e:
            logger.error(f"レポート生成・送信中にエラー発生: {e}", exc_info=True)

        total_end = time.time()
        logger.info(f"レポート送信処理完了 所要時間: {total_end - start_time:.4f}秒")