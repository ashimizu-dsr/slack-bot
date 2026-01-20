"""
Slack Event Handlers - チャンネル内の発言監視とAI解析の実行
Cloud Run 最適化版（Lazy非使用・直列処理モデル）
"""
import logging
from typing import Optional
from constants import ENABLE_CHANNEL_NLP, ATTENDANCE_CHANNEL_ID
from resources.services.nlp_service import extract_attendance_from_text 
from resources.views.modal_views import create_setup_message_blocks
from resources.shared.utils import get_user_email

# loggerの統一
logger = logging.getLogger(__name__)

# メッセージ重複防止用
_processed_message_ts = set()

def _is_likely_attendance_message(text: str) -> bool:
    if not text: return False
    text_stripped = text.strip()
    boring_messages = [
        "承知しました", "了解です", "ありがとうございます", 
        "お疲れ様です", "おはようございます", "よろしくお願いします"
    ]
    return text_stripped not in boring_messages

def should_process_message(event) -> bool:
    """メッセージを処理すべきかどうかの判定ロジック"""
    user_id = event.get("user")
    text = (event.get("text") or "").strip()
    channel = event.get("channel")

    # サブタイプがある場合やBot自身の投稿、空文字は無視
    if event.get("subtype") or event.get("bot_id") or not user_id or not text:
        return False
    
    # NLP設定が無効、または指定チャンネル以外なら無視
    if not ENABLE_CHANNEL_NLP: return False
    if ATTENDANCE_CHANNEL_ID and channel != ATTENDANCE_CHANNEL_ID: return False
    
    # すでにボットが反応した後のメッセージや、挨拶のみのメッセージは無視
    if text.startswith(("勤怠連絡:", "✅", "ⓘ")): return False
    if not _is_likely_attendance_message(text): return False
    
    return True

def process_attendance_core(event, client, attendance_service, notification_service):
    """
    AI解析を実行し、結果に基づいて保存または削除を実行する。
    削除時はスレッドに誰でも見える形で通知する。
    """
    user_id = event.get("user")
    workspace_id = event.get("team")
    ts = event.get("ts")
    channel = event.get("channel")
    text = (event.get("text") or "").strip()

    # 二重処理防止
    msg_key = f"{channel}:{ts}"
    if msg_key in _processed_message_ts:
        return
    _processed_message_ts.add(msg_key)

    try:
        email: Optional[str] = get_user_email(client, user_id, logger)
        
        # AI解析の実行
        extraction = extract_attendance_from_text(text)
        
        if not extraction:
            return

        # 受付中リアクション（処理が始まった目印）
        try:
            client.reactions_add(channel=channel, name="outbox_tray", timestamp=ts)
        except Exception: pass

        # 抽出データのリスト化
        attendances = [extraction]
        if "_additional_attendances" in extraction:
            attendances.extend(extraction["_additional_attendances"])

        for att in attendances:
            date = att.get("date")
            action = att.get("action", "save")

            if action == "delete":
                try:
                    # DBから削除実行
                    attendance_service.delete_attendance(workspace_id, user_id, date)
                    
                    # --- 【修正点】全員に見える形でスレッドに通知 ---
                    client.chat_postMessage(
                        channel=channel,
                        thread_ts=ts,
                        text=f"~{date} の勤怠連絡を取り消しました~"
                    )
                    logger.info(f"Auto-deleted by AI strikethrough: {user_id} on {date}")
                except Exception as e:
                    logger.info(f"Delete skipped (not found or error): {date}")
                continue

            # --- 通常の保存処理 ---
            record = attendance_service.save_attendance(
                workspace_id=workspace_id, user_id=user_id, email=email,
                date=date, status=att.get("status"), note=att.get("note", ""), 
                channel_id=channel, ts=ts
            )
            
            actions = [
                {"type": "button", "text": {"type": "plain_text", "text": "修正"}, "action_id": "open_update_attendance", "value": date},
                {"type": "button", "text": {"type": "plain_text", "text": "取消"}, "action_id": "delete_attendance_request", "value": date}
            ]

            notification_service.notify_attendance_change(
                record=record, 
                options=actions, 
                channel=channel, 
                thread_ts=ts
            )
            
    except Exception as e:
        logger.error(f"解析・保存エラー: {e}", exc_info=True)

def register_slack_handlers(app, attendance_service, notification_service) -> None:
    """ハンドラーの登録"""

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
    def handle_incoming_message(event, client, ack):
        """
        メッセージ受信ハンドラー
        Cloud Runでは lazy を使わず、ack() の後に直接処理を書く
        """
        # 1. まず Slack に 200 OK を返す（3秒ルール回避）
        ack()

        # 2. 処理対象かどうか判定
        if not should_process_message(event):
            return

        # 3. そのまま重い処理（AI解析）を実行
        # Cloud Runの --no-cpu-throttling 設定により、ack後も処理が継続されます
        process_attendance_core(
            event=event, 
            client=client, 
            attendance_service=attendance_service, 
            notification_service=notification_service
        )