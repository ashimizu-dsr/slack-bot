"""
Report Service - 指定日の勤怠状況を集計してSlackへレポート送信する
Cloud Functions / Run 対応版

注意: このファイルは旧バージョンです。
マルチテナント対応後は notification_service.py の send_daily_report() を使用してください。
このファイルは後方互換性のために残されていますが、使用は推奨されません。
"""

import os
import logging
from datetime import date
from typing import Optional

# クラウド対応済みの db 関数をインポート
from resources.shared.db import init_db, get_attendance_records_by_sections
from resources.views.modal_views import build_daily_report_blocks
from resources.constants import SECTION_TRANSLATION

# loggerの設定
logger = logging.getLogger(__name__)

def send_daily_report(target_date: Optional[str] = None, workspace_id: str = None):
    """
    集計レポートを生成して送信するメイン関数
    
    注意: この関数は旧バージョンです。
    マルチテナント対応後は notification_service.py の send_daily_report() を使用してください。
    """
    logger.warning("この関数は旧バージョンです。notification_service.py を使用してください。")
    
    if not workspace_id:
        logger.error("workspace_id が指定されていません。マルチテナント環境では必須です。")
        return
    
    logger.info("--- レポート生成処理開始 (旧バージョン) ---")
    
    # Firestoreの初期化
    init_db()
    
    today = target_date or str(date.today())
    logger.info(f"ターゲット日付: {today}")
    
    report_data = {}
    all_section_ids = list(SECTION_TRANSLATION.keys())
    
    try:
        # マルチテナント対応: workspace_id を渡す
        from resources.clients.slack_client import get_slack_client
        client = get_slack_client(workspace_id)
        
        # 各セクション（課）ごとにデータを集計
        for sid in all_section_ids:
            section_name = SECTION_TRANSLATION.get(sid, sid)
            # Firestoreから該当セクションのメンバーの打刻を取得
            rows = get_attendance_records_by_sections(workspace_id, today, [sid])
            
            # Firestoreのデータはすでに辞書形式なので dict(r) は不要な場合が多いですが、
            # 安全のためにリストとして格納
            report_data[section_name] = rows if rows else []
        
        logger.info(f"集計完了: {len(report_data)} セクション")

        # Block Kitの組み立て
        blocks = build_daily_report_blocks(
            header=f"{today} 勤怠状況",
            section_data=report_data
        )

        # 送信先チャンネルの取得
        from resources.shared.db import get_workspace_config
        workspace_config = get_workspace_config(workspace_id)
        
        if not workspace_config or not workspace_config.get("report_channel_id"):
            logger.warning("report_channel_id が設定されていないため、送信をスキップします。")
            return

        response = client.chat_postMessage(
            channel=workspace_config["report_channel_id"],
            blocks=blocks,
            text=f"{today}の勤怠レポート"
        )
        logger.info(f"✅ レポート送信成功: {response['ts']}")

    except Exception as e:
        logger.error(f"❌ レポート送信プロセスでエラーが発生: {e}", exc_info=True)

def report_handler(request):
    """
    Google Cloud Scheduler (HTTP) から呼ばれる際の受付口
    
    注意: この関数は旧バージョンです。
    マルチテナント対応後は main.py の /job/report エンドポイントを使用してください。
    """
    logger.warning("この関数は旧バージョンです。main.py の /job/report エンドポイントを使用してください。")
    
    # URLパラメータに ?date=2024-01-01 と入れれば特定日の再送も可能
    request_json = request.get_json(silent=True)
    request_args = request.args

    target_date = None
    workspace_id = None
    
    if request_json:
        target_date = request_json.get('date')
        workspace_id = request_json.get('workspace_id')
    elif request_args:
        target_date = request_args.get('date')
        workspace_id = request_args.get('workspace_id')

    send_daily_report(target_date, workspace_id)
    return "OK", 200

if __name__ == "__main__":
    # ローカルデバッグ用
    logging.basicConfig(level=logging.INFO)
    logger.warning("このスクリプトは旧バージョンです。")
    # send_daily_report(workspace_id="YOUR_WORKSPACE_ID")