"""
Slack Event Handlers - 修正版
"""
import logging
from typing import Optional
# nlp_service から新しく extract_attendance_from_text を読み込む
from resources.services.nlp_service import extract_attendance_from_text 
from resources.views.modal_views import create_setup_message_blocks
from resources.shared.utils import get_user_email

logger = logging.getLogger(__name__)

_processed_message_ts = set()

def _is_likely_attendance_message(text: str) -> bool:
    if not text: return False
    # 【不具合①対策】判定を緩和。
    # 「1/21(水) ~8:00出勤~」のようなメッセージは
    # 挨拶リストに含まれないため、以下のロジックで通過するようにします。
    text_stripped = text.strip()
    boring_messages = [
        "承知しました", "了解です", "ありがとうございます", 
        "お疲れ様です", "おはようございます", "よろしくお願いします"
    ]
    # 挨拶そのものと完全一致する場合のみ除外
    return text_stripped not in boring_messages

def should_process_message(event) -> bool:
    user_id = event.get("user")
    text = (event.get("text") or "").strip()
    channel = event.get("channel")

    if event.get("subtype") or event.get("bot_id") or not user_id or not text:
        return False
    
    # NLPが無効、または指定チャンネル以外なら無視
    from constants import ENABLE_CHANNEL_NLP, ATTENDANCE_CHANNEL_ID
    if not ENABLE_CHANNEL_NLP: return False
    if ATTENDANCE_CHANNEL_ID and channel != ATTENDANCE_CHANNEL_ID: return False
    
    # 自分が送った「記録済み」メッセージに反応しないようにする
    if text.startswith(("勤怠連絡:", "✅", "ⓘ")): return False
    if not _is_likely_attendance_message(text): return False
    
    return True

def process_attendance_core(event, client, attendance_service, notification_service):
    user_id = event.get("user")
    workspace_id = event.get("team")
    ts = event.get("ts")
    channel = event.get("channel")
    text = (event.get("text") or "").strip()

    msg_key = f"{channel}:{ts}"
    if msg_key in _processed_message_ts: return
    _processed_message_ts.add(msg_key)

    try:
        email: Optional[str] = get_user_email(client, user_id, logger)
        
        # 1. AI解析実行 (nlp_serviceの修正版が反映されている前提)
        extraction = extract_attendance_from_text(text)
        
        if not extraction:
            logger.info(f"AI: No attendance data extracted from: {text[:20]}...")
            return

        # 処理開始のリアクション
        try:
            client.reactions_add(channel=channel, name="outbox_tray", timestamp=ts)
        except Exception: pass

        # 2. リスト化（複数日対応）
        attendances = [extraction]
        if "_additional_attendances" in extraction:
            attendances.extend(extraction["_additional_attendances"])

        # 3. ループ処理
        for att in attendances:
            date = att.get("date")
            action = att.get("action", "save")

            # A. 削除アクション (打ち消し線のみの場合など)
            if action == "delete":
                try:
                    attendance_service.delete_attendance(workspace_id, user_id, date)
                    # 通知
                    notification_service.notify_attendance_change(
                        record={"user_id": user_id, "date": date},
                        channel=channel,
                        thread_ts=ts,
                        is_delete=True
                    )
                except Exception:
                    logger.info(f"Delete failed/skipped: {date}")
                continue

            # B. 保存・更新アクション
            # record は保存された結果のオブジェクト（または辞書）
            record = attendance_service.save_attendance(
                workspace_id=workspace_id, user_id=user_id, email=email,
                date=date, status=att.get("status"), note=att.get("note", ""), 
                channel_id=channel, ts=ts
            )
            
            # 【重要】通知サービスの呼び出し
            # 引数を NotificationService の notify_attendance_change の定義に合わせます
            notification_service.notify_attendance_change(
                record=record, 
                channel=channel, 
                thread_ts=ts,
                is_update=False # 保存したてなのでFalse
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