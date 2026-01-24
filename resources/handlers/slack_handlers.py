"""
モーダル開くでもフォーム送信するでもない処理

Slackイベントハンドラー

このモジュールは、Slackからのイベント（メッセージ受信、Bot参加など）を処理します。
メッセージからのAI解析による勤怠記録の自動登録を担当します。
"""
import logging
from typing import Optional
from resources.services.nlp_service import extract_attendance_from_text 
from resources.views.modal_views import create_setup_message_blocks
from resources.shared.utils import get_user_email

logger = logging.getLogger(__name__)

# 処理済みメッセージのタイムスタンプを記録（重複防止用）
_processed_message_ts = set()

def _is_likely_attendance_message(text: str) -> bool:
    """
    メッセージが勤怠連絡かどうかを簡易判定します。
    
    Args:
        text: メッセージ本文
        
    Returns:
        勤怠連絡の可能性がある場合True
        
    Note:
        定型の挨拶文のみのメッセージを除外します。
        最終的な判定はAIが行うため、ここでは緩い判定でOKです。
    """
    if not text:
        return False
        
    text_stripped = text.strip()
    boring_messages = [
        "承知しました", "了解です", "ありがとうございます", 
        "お疲れ様です", "おはようございます", "よろしくお願いします"
    ]
    # 挨拶そのものと完全一致する場合のみ除外
    return text_stripped not in boring_messages

def should_process_message(event) -> bool:
    """
    メッセージを処理すべきかどうかを判定します。
    
    Args:
        event: Slackメッセージイベント
        
    Returns:
        処理対象の場合True
        
    Note:
        以下の条件で除外されます:
        - Bot自身のメッセージ（bot_id, bot_profile が存在する場合）
        - サブタイプがあるメッセージ（編集、削除など）
        - NLPが無効化されている
        - 指定チャンネル以外（環境変数でチャンネルIDが設定されている場合）
        - Bot自身が投稿した通知メッセージ（"ⓘ"で始まるメッセージ）
    """
    # ==========================================
    # 1. 必須フィールドのチェック
    # ==========================================
    user_id = event.get("user")
    text = (event.get("text") or "").strip()
    channel = event.get("channel")
    
    if not user_id or not text:
        return False
    
    # ==========================================
    # 2. Bot判定（最重要）
    # ==========================================
    # Bot自身のメッセージを確実に除外
    if event.get("bot_id"):
        logger.debug(f"Bot判定: bot_id={event.get('bot_id')} が存在するため除外")
        return False
    
    if event.get("bot_profile"):
        logger.debug(f"Bot判定: bot_profile が存在するため除外")
        return False
    
    # ==========================================
    # 3. サブタイプのチェック
    # ==========================================
    # 編集、削除、ファイル共有など
    if event.get("subtype"):
        logger.debug(f"サブタイプ判定: subtype={event.get('subtype')} が存在するため除外")
        return False
    
    # # ==========================================
    # # 4. Bot通知メッセージの判定（追加の安全策）
    # # ==========================================
    # # Botが投稿した通知メッセージ（「ⓘ 〇〇 さんの勤怠連絡を記録しました」など）
    # # を確実に除外するため、テキストパターンでも判定
    # if "さんの勤怠連絡を" in text and "しました" in text:
    #     logger.debug(f"Bot通知パターン判定: 除外")
    #     return False
    
    # ==========================================
    # 5. NLP機能の有効/無効チェック
    # ==========================================
    from constants import ENABLE_CHANNEL_NLP, ATTENDANCE_CHANNEL_ID
    if not ENABLE_CHANNEL_NLP:
        return False
        
    # ==========================================
    # 6. チャンネル制限
    # ==========================================
    # 指定チャンネルのみに制限（環境変数で設定されている場合）
    if ATTENDANCE_CHANNEL_ID and channel != ATTENDANCE_CHANNEL_ID:
        return False
    
    # ==========================================
    # 7. 挨拶のみのメッセージを除外
    # ==========================================
    if not _is_likely_attendance_message(text):
        return False
    
    return True

def process_attendance_core(event, client, attendance_service, notification_service):
    """
    メッセージからAI解析を実行し、勤怠記録を保存します。
    
    Args:
        event: Slackメッセージイベント
        client: Slack Web APIクライアント
        attendance_service: AttendanceServiceインスタンス
        notification_service: NotificationServiceインスタンス
        
    Note:
        処理フロー:
        1. AI解析でメッセージから勤怠情報を抽出
        2. 抽出結果をAttendanceServiceで処理（保存/削除）
        3. NotificationServiceで通知カードを送信
    """
    user_id = event.get("user")
    workspace_id = event.get("team")
    ts = event.get("ts")
    channel = event.get("channel")
    text = (event.get("text") or "").strip()

    # 重複処理の防止
    msg_key = f"{channel}:{ts}"
    if msg_key in _processed_message_ts:
        return
    _processed_message_ts.add(msg_key)

    try:
        email: Optional[str] = get_user_email(client, user_id, logger)
        
        # 1. AI解析実行
        extraction = extract_attendance_from_text(text)
        
        if not extraction:
            logger.info(f"AI: No attendance data extracted from: {text[:20]}...")
            return

        # 処理開始のリアクション
        try:
            client.reactions_add(channel=channel, name="outbox_tray", timestamp=ts)
        except Exception:
            pass

        # 2. 抽出結果をリスト化（複数日対応）
        attendances = [extraction]
        if "_additional_attendances" in extraction:
            attendances.extend(extraction["_additional_attendances"])

        # 3. ループ処理
        for att in attendances:
            date = att.get("date")
            action = att.get("action", "save")

            # A. 削除アクション（打ち消し線のみの場合など）
            if action == "delete":
                try:
                    attendance_service.delete_attendance(workspace_id, user_id, date)
                    # 削除通知
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
            record = attendance_service.save_attendance(
                workspace_id=workspace_id, 
                user_id=user_id, 
                email=email,
                date=date, 
                status=att.get("status"), 
                note=att.get("note", ""), 
                channel_id=channel, 
                ts=ts
            )
            
            # 通知カードの送信

            print("DEBUG: CALLED FROM HANDLER_スラックハンドラー")
            notification_service.notify_attendance_change(
                record=record, 
                channel=channel, 
                thread_ts=ts,
                is_update=False
            )
            
    except Exception as e:
        logger.error(f"解析・保存エラー: {e}", exc_info=True)

def register_slack_handlers(app, attendance_service, notification_service) -> None:
    """
    Slackイベント関連のハンドラーをSlack Appに登録します。
    
    Args:
        app: Slack Bolt Appインスタンス
        attendance_service: AttendanceServiceインスタンス
        notification_service: NotificationServiceインスタンス
    """

    @app.event("member_joined_channel")
    def handle_bot_join(event, client):
        """
        Botがチャンネルに参加したときの処理。
        
        セットアップメッセージを投稿します。
        """
        try:
            bot_user_id = client.auth_test()["user_id"]
            if event.get("user") == bot_user_id:
                client.chat_postMessage(
                    channel=event.get("channel"),
                    blocks=create_setup_message_blocks(),
                    text="勤怠管理ボットが参加しました。設定をお願いします。"
                )
                logger.info(f"Bot参加: Channel={event.get('channel')}")
        except Exception as e:
            logger.error(f"初期設定送信エラー: {e}", exc_info=True)

    @app.event("message")
    def handle_incoming_message(event, client, ack):
        """
        メッセージ受信ハンドラー。
        
        Note:
            Cloud Runでは lazy リスナーを使わず、ack() の後に直接処理を書きます。
            --no-cpu-throttling 設定により、ack後も処理が継続されます。
        """
        # 1. まず Slack に 200 OK を返す（3秒ルール回避）
        ack()

        # 2. 処理対象かどうか判定
        if not should_process_message(event):
            return

        # 3. そのまま重い処理（AI解析）を実行
        process_attendance_core(
            event=event, 
            client=client, 
            attendance_service=attendance_service, 
            notification_service=notification_service
        )