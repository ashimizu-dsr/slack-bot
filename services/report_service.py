import os
from datetime import date
from typing import Optional
from slack_sdk import WebClient

from db.db import init_db, get_attendance_records_by_sections
from views.modal_views import build_daily_report_blocks # さっき作ったViewを使う
from utils.slack_utils import SECTION_TRANSLATION

# 環境変数の読み込み
SLACK_BOT_TOKEN = os.environ.get("SLACK_BOT_TOKEN")
REPORT_CHANNEL_ID = os.environ.get("REPORT_CHANNEL_ID")
client = WebClient(token=SLACK_BOT_TOKEN)

def send_daily_report(target_date: Optional[str] = None):
    """
    一日の集計レポートを配信するコアロジック
    """
    init_db()
    today = target_date or str(date.today())
    
    # 1. データの集約 (Model/DB層)
    # { "課名": [レコードリスト], ... } という形式に加工する
    report_data = {}
    all_section_ids = list(SECTION_TRANSLATION.keys())
    
    for sid in all_section_ids:
        section_name = SECTION_TRANSLATION.get(sid, sid)
        # DBからデータを取得
        rows = get_attendance_records_by_sections(today, [sid])
        report_data[section_name] = [dict(r) for r in rows] if rows else []

    # 2. Viewの生成 (View層)
    # 先ほど modal_views.py に定義した関数を呼び出す
    blocks = build_daily_report_blocks(
        header=f"📅 {today} 勤怠集計レポート",
        section_data=report_data
    )

    # 3. 送信 (Client層)
    try:
        if REPORT_CHANNEL_ID:
            client.chat_postMessage(
                channel=REPORT_CHANNEL_ID,
                blocks=blocks,
                text=f"{today}の勤怠レポート" # 通知用テキスト
            )
    except Exception as e:
        print(f"レポート送信エラー: {e}")

if __name__ == "__main__":
    send_daily_report()