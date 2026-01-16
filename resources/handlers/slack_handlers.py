"""
Slack Event Handlers - チャンネル内の発言監視とAI解析の実行
Cloud Run / Functions 対応版（スレッド排除版）
"""
import logging
from typing import Optional
from constants import ENABLE_CHANNEL_NLP, ATTENDANCE_CHANNEL_ID
# 修正：nlp_service ではなく ai_service から直接呼ぶ形に変更（ファイル名に合わせて調整してください）
from resources.services.nlp_service import extract_attendance_from_text 
from resources.views.modal_views import create_setup_message_blocks
from shared.utils import get_user_email

# loggerの統一
logger = logging.getLogger(__name__)

# メッセージ重複防止用（クラウド環境ではメモリがリセットされるため気休め程度ですが維持します）
_processed_message_ts = set()

def _is_likely_attendance_message(text: str) -> bool:
    if not text: return False
    text_stripped = text.strip()
    boring_messages = [
        "承知しました", "了解です", "ありがとうございます", 
        "お疲れ様です", "おはようございます", "よろしくお願いします"
    ]
    return text_stripped not in boring_messages

def process_attendance_core(event, user_id, attendance_service, notification_service, client):
    """
    AI解析を実行し、結果を保存・通知するコアロジック。
    クラウド環境ではスレッドを使わず、このまま直列で実行します。
    """
    workspace_id = event.get("team")
    ts = event.get("ts")
    channel = event.get("channel")
    text = (event.get("text") or "").strip()

    try:
        # 名寄せ用Email取得
        email: Optional[str] = get_user_email(client, user_id, logger)
        
        # AI解析の実行
        logger.info(f"AI解析開始: user={user_id}")
        extraction = extract_attendance_from_text(text)
        
        if not extraction:
            logger.info(f"解析スキップ: 勤怠情報なし")
            return

        # リアクションを追加（受付中）
        try:
            client.reactions_add(channel=channel, name="outbox_tray", timestamp=ts)
        except Exception: pass

        # 抽出データの正規化（単一/複数日対応）
        attendances = [extraction]
        if "_additional_attendances" in extraction:
            attendances.extend(extraction["_additional_attendances"])

        for att in attendances:
            date = att.get("date")
            # DB保存 (shared/db.py 経由)
            record = attendance_service.save_attendance(
                workspace_id=workspace_id, user_id=user_id, email=email,
                date=date, status=att.get("status"), note=att.get("note", ""), 
                channel_id=channel, ts=ts
            )
            
            # スレッド内操作ボタン
            actions = [
                {"type": "button", "text": {"type": "plain_text", "text": "修正"}, "action_id": "open_update_attendance", "value": date},
                {"type": "button", "text": {"type": "plain_text", "text": "取消"}, "action_id": "delete_attendance_request", "value": date}
            ]

            # スレッドへ完了通知
            notification_service.notify_attendance_change(
                record=record, 
                options=actions, 
                channel=channel, 
                thread_ts=ts
            )
            
    except Exception as e:
        logger.error(f"解析・保存エラー: {e}", exc_info=True)

def register_slack_handlers(app, attendance_service, notification_service) -> None:
    
    @app.event("member_joined_channel")
    def handle_bot_join(event, client):
        try:
            bot_user_id = client.auth_test()["user_id"]
            if event.get("user") == bot_user_id:
                client.chat_postMessage(
                    channel=event.get("channel"),
                    blocks=create_setup_message_blocks(),
                    text="勤怠管理ボットが参加しました。設定をお願いします。"
                )
        except Exception as e:
            logger.error(f"初期設定送信エラー: {e}")

    @app.event("message")
    def handle_incoming_message(event, client):
        user_id = event.get("user")
        text = (event.get("text") or "").strip()
        channel = event.get("channel")
        ts = event.get("ts")
        
        if event.get("subtype") or event.get("bot_id") or not user_id or not text:
            return

        try:
            if not ENABLE_CHANNEL_NLP: return
            if ATTENDANCE_CHANNEL_ID and channel != ATTENDANCE_CHANNEL_ID: return
            if text.startswith(("勤怠連絡:", "✅", "ⓘ")): return 
            if not _is_likely_attendance_message(text): return

            # 二重処理防止
            msg_key = f"{channel}:{ts}"
            if msg_key in _processed_message_ts: return
            _processed_message_ts.add(msg_key)

            # --- 修正ポイント：threading は使わずに直接実行する ---
            # Bolt の process_before_response=True 設定と組み合わせることで、
            # クラウド環境でも解析が終わるまでインスタンスが起きていてくれます。
            process_attendance_core(event, user_id, attendance_service, notification_service, client)

        except Exception as e:
            logger.error(f"メッセージ監視エラー: {e}")