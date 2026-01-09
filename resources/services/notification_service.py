"""
Notification Service - Slackへのメッセージ送信・通知処理を担当
マルチワークスペース（workspace_id）対応版
"""

import datetime
import os
import sys
import time
from typing import Any, Dict, List, Optional

# プロジェクトルートをパスに追加
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from constants import REPORT_CHANNEL_ID, STATUS_TRANSLATION
from shared.errors import SlackApiError
from shared.logging_config import get_logger
from shared.db import db_conn  # チャンネル一覧取得に使用

logger = get_logger(__name__)

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
        from views.modal_views import create_attendance_card_blocks
        
        # recordが持つ workspace_id を考慮してブロックを生成
        blocks = create_attendance_card_blocks(record, message_text, options, is_update=is_update)
        
        try:
            target = channel or getattr(record, 'channel_id', None)
            
            if not target:
                logger.warning("通知送信スキップ: チャンネルIDが指定されていません。")
                return

            self.client.chat_postMessage(
                channel=target,
                blocks=blocks,
                text="勤怠を記録しました", 
                thread_ts=thread_ts
            )
            logger.info(f"Notification sent to {target} (thread_ts: {thread_ts})")

        except Exception as e:
            logger.error(f"通知送信失敗: {e}", exc_info=True)

    def send_daily_report(self, date_str: str) -> None: 
        """
        【マルチワークスペース版】
        DBに登録されている全てのワークスペース・チャンネルに対してレポートを送信します。
        """
        if not self.attendance_service:
            logger.error("attendance_service が未設定のためレポートを送信できません。")
            return

        start_time = time.time()
        
        # 1. 配信対象となる (workspace_id, channel_id) のペアをDBから取得
        # channel_membersテーブルに登録がある場所＝レポートを送るべき場所と定義
        targets = []
        with db_conn() as conn:
            cur = conn.cursor()
            cur.execute("SELECT DISTINCT workspace_id, channel_id FROM channel_members")
            targets = [dict(row) for row in cur.fetchall()]

        if not targets:
            logger.info("レポート送信対象のチャンネルがありません。")
            return

        # 日付タイトルの準備
        dt = datetime.date.fromisoformat(date_str)
        weekday_list = ["月", "火", "水", "木", "金", "土", "日"]
        title_date = f"{dt.strftime('%m/%d')}({weekday_list[dt.weekday()]})"
        
        all_sections = [
            ("sec_1", "1課"), ("sec_2", "2課"), ("sec_3", "3課"), ("sec_4", "4課"),
            ("sec_5", "5課"), ("sec_6", "6課"), ("sec_7", "7課"), ("sec_finance", "金融開発課")
        ]

        # 2. 各ターゲット（チャンネル）ごとにレポートを作成して送信
        for target in targets:
            ws_id = target["workspace_id"]
            ch_id = target["channel_id"]

            try:
                # そのワークスペース・チャンネル用のデータを取得
                all_attendance_data = self.attendance_service.get_daily_report_data(ws_id, date_str, ch_id)

                # Block Kitの組み立て
                blocks = [
                    {
                        "type": "header",
                        "text": {"type": "plain_text", "text": f"{title_date}の勤怠一覧"}
                    }
                ]

                for sec_id, sec_name in all_sections:
                    records = all_attendance_data.get(sec_id, [])
                    blocks.append({
                        "type": "section",
                        "text": {"type": "mrkdwn", "text": f"*{sec_name}*"}
                    })

                    if not records:
                        content_text = "勤怠連絡はありません"
                    else:
                        records.sort(key=lambda x: x['status'])
                        lines = []
                        for r in records:
                            s_jp = STATUS_TRANSLATION.get(r['status'], r['status'])
                            note_text = f" ({r['note']})" if r.get('note') else ""
                            lines.append(f"• <@{r['user_id']}> - {s_jp}{note_text}")
                        content_text = "\n".join(lines)

                    blocks.append({
                        "type": "context",
                        "elements": [{"type": "mrkdwn", "text": content_text}]
                    })

                # Slack APIへの送信
                self.client.chat_postMessage(
                    channel=ch_id,
                    blocks=blocks,
                    text=f"{title_date}の勤怠一覧",
                    # 複数ワークスペース対応（トークンがワークスペースごとに異なる場合はここを拡張）
                )
                logger.info(f"Daily report sent to WS:{ws_id} CH:{ch_id}")

            except Exception as e:
                logger.error(f"レポート送信失敗 (WS:{ws_id}, CH:{ch_id}): {e}")

        total_end = time.time()
        logger.info(f"--- 全ワークスペースへのレポート送信完了 所要時間: {total_end - start_time:.4f}秒 ---")