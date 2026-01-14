"""
Report Service - 指定日の勤怠状況を集計してSlackへレポート送信する
Cloud Functions / Run 対応版
"""

import os
import logging
from datetime import date
from typing import Optional
from slack_sdk import WebClient

# クラウド対応済みの db 関数をインポート
from shared.db import init_db, get_attendance_records_by_sections
from views.modal_views import build_daily_report_blocks
from constants import SECTION_TRANSLATION, REPORT_CHANNEL_ID as CONST_REPORT_ID

# loggerの設定
logger = logging.getLogger(__name__)

# クライアントの初期化（グローバルに置くことでメモリを節約）
SLACK_BOT_TOKEN = os.environ.get("SLACK_BOT_TOKEN")
# Cloud Run 等の環境変数を優先し、なければ constants の値を使う
REPORT_CHANNEL_ID = os.environ.get("REPORT_CHANNEL_ID") or CONST_REPORT_ID
client = WebClient(token=SLACK_BOT_TOKEN)

def send_daily_report(target_date: Optional[str] = None):
    """
    集計レポートを生成して送信するメイン関数
    """
    logger.info("--- レポート生成処理開始 ---")
    
    # Firestoreの初期化
    init_db()
    
    today = target_date or str(date.today())
    logger.info(f"ターゲット日付: {today}")
    
    report_data = {}
    all_section_ids = list(SECTION_TRANSLATION.keys())
    
    try:
        # 各セクション（課）ごとにデータを集計
        for sid in all_section_ids:
            section_name = SECTION_TRANSLATION.get(sid, sid)
            # Firestoreから該当セクションのメンバーの打刻を取得
            rows = get_attendance_records_by_sections(today, [sid])
            
            # Firestoreのデータはすでに辞書形式なので dict(r) は不要な場合が多いですが、
            # 安全のためにリストとして格納
            report_data[section_name] = rows if rows else []
        
        logger.info(f"集計完了: {len(report_data)} セクション")

        # Block Kitの組み立て
        blocks = build_daily_report_blocks(
            header=f"{today} 勤怠状況",
            section_data=report_data
        )

        # Slackへ送信
        if not REPORT_CHANNEL_ID:
            logger.warning("REPORT_CHANNEL_ID が設定されていないため、送信をスキップします。")
            return

        response = client.chat_postMessage(
            channel=REPORT_CHANNEL_ID,
            blocks=blocks,
            text=f"{today}の勤怠レポート"
        )
        logger.info(f"✅ レポート送信成功: {response['ts']}")

    except Exception as e:
        logger.error(f"❌ レポート送信プロセスでエラーが発生: {e}", exc_info=True)

def report_handler(request):
    """
    Google Cloud Scheduler (HTTP) から呼ばれる際の受付口
    """
    # URLパラメータに ?date=2024-01-01 と入れれば特定日の再送も可能
    request_json = request.get_json(silent=True)
    request_args = request.args

    target_date = None
    if request_json and 'date' in request_json:
        target_date = request_json['date']
    elif request_args and 'date' in request_args:
        target_date = request_args['date']

    send_daily_report(target_date)
    return "OK", 200

if __name__ == "__main__":
    # ローカルデバッグ用
    logging.basicConfig(level=logging.INFO)
    send_daily_report()