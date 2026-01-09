import os
import sys
from datetime import date
from typing import Optional
from slack_sdk import WebClient

# プロジェクトルートをパスに追加
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from shared.db import init_db, get_attendance_records_by_sections
from views.modal_views import build_daily_report_blocks # さっき作ったViewを使う
from constants import SECTION_TRANSLATION, REPORT_CHANNEL_ID as CONST_REPORT_ID

# 環境変数の読み込み
SLACK_BOT_TOKEN = os.environ.get("SLACK_BOT_TOKEN")
REPORT_CHANNEL_ID = os.environ.get("REPORT_CHANNEL_ID") or CONST_REPORT_ID
client = WebClient(token=SLACK_BOT_TOKEN)

def send_daily_report(target_date: Optional[str] = None):
    # print(f"--- レポート生成処理開始 ---") # ログ
    init_db()
    today = target_date or str(date.today())
    # print(f"ターゲット日付: {today}")
    
    report_data = {}
    all_section_ids = list(SECTION_TRANSLATION.keys())
    
    for sid in all_section_ids:
        section_name = SECTION_TRANSLATION.get(sid, sid)
        rows = get_attendance_records_by_sections(today, [sid])
        report_data[section_name] = [dict(r) for r in rows] if rows else []
    
    # print(f"集計完了: {len(report_data)} セクション")

    blocks = build_daily_report_blocks(
        header=f"{today} 勤怠",
        section_data=report_data
    )

    # 送信
    try:
        if not REPORT_CHANNEL_ID:
            print("⚠️ 警告: REPORT_CHANNEL_ID が設定されていないため、送信をスキップします。")
            return

        print(f"Slackへ送信中... (Channel: {REPORT_CHANNEL_ID})")
        response = client.chat_postMessage(
            channel=REPORT_CHANNEL_ID,
            blocks=blocks,
            text=f"{today}の勤怠レポート"
        )
        print(f"✅ 送信成功: {response['ts']}")
    except Exception as e:
        print(f"❌ レポート送信エラー詳細: {e}")

if __name__ == "__main__":
    send_daily_report()