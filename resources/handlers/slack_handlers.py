"""
Slack Event Handlers - チャンネル内の発言監視とAI解析の実行
Cloud Run / Lazy リスナー対応版
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
    """メッセージを処理すべきかどうかの判定ロジック（ack用）"""
    user_id = event.get("user")
    text = (event.get("text") or "").strip()
    channel = event.get("channel")

    # サブタイプがある場合（編集など）やBot自身の投稿、空文字は無視
    if event.get("subtype") or event.get("bot_id") or not user_id or not text:
        return False
    
    # NLP設定が無効、または指定チャンネル以外なら無視
    if not ENABLE_CHANNEL_NLP: return False
    if ATTENDANCE_CHANNEL_ID and channel != ATTENDANCE_CHANNEL_ID: return False
    
    # 既にボットが反応した後のメッセージや、挨拶のみのメッセージは無視
    if text.startswith(("勤怠連絡:", "✅", "ⓘ")): return False
    if not _is_likely_attendance_message(text): return False
    
    return True

def process_attendance_core(event, client, attendance_service, notification_service):
    """
    AI解析を実行し、結果を保存・通知するコアロジック（lazy用）
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
            # DB保存
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

    # --- 修正の目玉：Lazyリスナーの実装 ---
    
    def handle_incoming_message_ack(event, ack):
        """すぐにレスポンスを返す"""
        if should_process_message(event):
            ack()
        else:
            # 処理不要な場合もackしないとSlackからリトライが来るためackする
            ack()

    def handle_incoming_message_lazy(event, client):
        """後から重いAI処理を行う"""
        # should_process_message を再度チェック（念のため）
        if not should_process_message(event):
            return
            
        process_attendance_core(
            event=event, 
            client=client, 
            attendance_service=attendance_service, 
            notification_service=notification_service
        )

    # 登録
    app.event("message")(
        ack=handle_incoming_message_ack,
        lazy=[handle_incoming_message_lazy]
    )