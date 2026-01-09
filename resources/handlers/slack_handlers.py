"""
Slack Event Handlers - チャンネル内の発言監視とAI解析の実行
"""
import threading
from typing import Optional
from shared.logging_config import get_logger
from constants import ENABLE_CHANNEL_NLP, ATTENDANCE_CHANNEL_ID
from services.nlp_service import extract_attendance_from_text
from views.modal_views import create_setup_message_blocks, create_attendance_card_blocks
from shared.utils import get_user_email

logger = get_logger(__name__)

# 重複処理防止用
_processed_message_ts = set()

# ==========================================
# 1. 補助関数（フィルターロジック）
# ==========================================

def _is_likely_attendance_message(text: str) -> bool:
    """挨拶や承諾のみのメッセージを除外する"""
    if not text: return False
    text_stripped = text.strip()
    boring_messages = [
        "承知しました", "了解です", "ありがとうございます", 
        "お疲れ様です", "おはようございます", "よろしくお願いします"
    ]
    return text_stripped not in boring_messages

# ==========================================
# 2. 非同期処理コアロジック（AI解析 & DB保存）
# ==========================================

def process_attendance_async(event, user_id, attendance_service, notification_service, client):
    """バックグラウンドでAI解析を実行し、結果をスレッドに返信する"""

    workspace_id = event.get("team")
    ts = event.get("ts")
    channel = event.get("channel")
    text = (event.get("text") or "").strip()

    try:
        # 名寄せ用Email取得
        email: Optional[str] = get_user_email(client, user_id, logger)
        
        # AI解析の実行
        logger.debug(f"AI解析開始: user={user_id}")
        extraction = extract_attendance_from_text(text)
        
        if not extraction:
            logger.info(f"解析スキップ: 勤怠情報なし")
            return

        # 受付完了リアクションを追加
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
            
            # スレッド内操作ボタン（修正・取消）
            actions = [
                {
                    "type": "button", 
                    "text": {"type": "plain_text", "text": "修正"}, 
                    "action_id": "open_update_attendance", 
                    "value": date
                },
                {
                    "type": "button", 
                    "text": {"type": "plain_text", "text": "取消"}, 
                    "action_id": "delete_attendance_request",
                    "value": date,
                    "confirm": {
                        "title": {"type": "plain_text", "text": "取消の確認"},
                        "text": {"type": "mrkdwn", "text": f"{date} の連絡を削除しますか？"},
                        "confirm": {"type": "plain_text", "text": "削除"},
                        "deny": {"type": "plain_text", "text": "キャンセル"},
                        "style": "danger"
                    }
                }
            ]

            # スレッドへ完了通知
            # notification_service側で引数エラーが出ないよう、必要最小限の引数で呼び出す
            notification_service.notify_attendance_change(
                record=record, 
                options=actions, 
                channel=channel, 
                thread_ts=ts
            )
            
    except Exception as e:
        logger.error(f"非同期処理エラー: {e}", exc_info=True)

# ==========================================
# 3. Slackイベント登録
# ==========================================

def register_slack_handlers(app, attendance_service, notification_service) -> None:
    
    # ------------------------------------------
    # A. ボット参加時の初期案内（イベント検知用）
    # ------------------------------------------
    @app.event("member_joined_channel")
    def handle_bot_join(event, client, logger):
        """ボット自身がチャンネルに入った際、初期設定ボタンを送る"""
        try:
            bot_user_id = client.auth_test()["user_id"]
            if event.get("user") == bot_user_id:
                client.chat_postMessage(
                    channel=event.get("channel"),
                    blocks=create_setup_message_blocks(),
                    text="ⓘ 勤怠連絡の管理を開始します。下のボタンより各課のメンバー設定をお願いします。"
                )
        except Exception as e:
            logger.error(f"初期設定送信エラー: {e}")

    # ------------------------------------------
    # B. 自由入力メッセージの監視 (AI解析)
    # ------------------------------------------
    @app.event("message")
    def handle_incoming_message(event, client, logger):
        """流れてくる発言をフィルターし、必要に応じて非同期解析を開始する"""
        user_id = event.get("user")
        text = (event.get("text") or "").strip()
        channel = event.get("channel")
        ts = event.get("ts")
        
        # 基本フィルター
        if event.get("subtype") or event.get("bot_id") or not user_id or not text:
            return

        try:
            if not ENABLE_CHANNEL_NLP: return
            if ATTENDANCE_CHANNEL_ID and channel != ATTENDANCE_CHANNEL_ID: return
            
            # ボット自身の投稿をスルー
            if text.startswith(("勤怠連絡:", "✅", "ⓘ")): return 
            
            if not _is_likely_attendance_message(text): return

            # 二重処理防止
            msg_key = f"{channel}:{ts}"
            if msg_key in _processed_message_ts: return
            _processed_message_ts.add(msg_key)

            # 解析を別スレッドで開始
            threading.Thread(
                target=process_attendance_async,
                args=(event, user_id, attendance_service, notification_service, client),
                daemon=True
            ).start()

        except Exception as e:
            logger.error(f"メッセージ監視エラー: {e}")