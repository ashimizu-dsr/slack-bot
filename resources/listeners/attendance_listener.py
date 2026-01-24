"""
勤怠操作リスナー

このモジュールは、勤怠記録に関するSlackイベントを受け取ります。
- メッセージからのAI解析による勤怠登録
- 修正ボタン押下
- 削除ボタン押下
- 削除確認の送信

命名規則:
- on_xxx: Slackイベントを受け取る
"""
import datetime
import json
import logging
from typing import Optional

from resources.services.nlp_service import extract_attendance_from_text 
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
    """
    if not text:
        return False
        
    text_stripped = text.strip()
    boring_messages = [
        "承知しました", "了解です", "ありがとうございます", 
        "お疲れ様です", "おはようございます", "よろしくお願いします"
    ]
    return text_stripped not in boring_messages


def should_process_message(event) -> bool:
    """
    メッセージを処理すべきかどうかを判定します。
    
    Args:
        event: Slackメッセージイベント
        
    Returns:
        処理対象の場合True
    """
    user_id = event.get("user")
    text = (event.get("text") or "").strip()
    channel = event.get("channel")
    
    if not user_id or not text:
        return False
    
    # Bot判定
    if event.get("bot_id") or event.get("bot_profile"):
        return False
    
    # サブタイプのチェック
    if event.get("subtype"):
        return False
    
    # NLP機能の有効/無効チェック
    from resources.constants import ENABLE_CHANNEL_NLP, ATTENDANCE_CHANNEL_ID
    if not ENABLE_CHANNEL_NLP:
        return False
        
    # チャンネル制限
    if ATTENDANCE_CHANNEL_ID and channel != ATTENDANCE_CHANNEL_ID:
        return False
    
    # 挨拶のみのメッセージを除外
    if not _is_likely_attendance_message(text):
        return False
    
    return True


def execute_attendance_from_message(
    event, 
    client, 
    attendance_service, 
    notification_service
):
    """
    メッセージからAI解析を実行し、勤怠記録を保存します。
    
    Args:
        event: Slackメッセージイベント
        client: Slack Web APIクライアント
        attendance_service: AttendanceServiceインスタンス
        notification_service: NotificationServiceインスタンス
    """
    user_id = event.get("user")
    team_id = event.get("team")  # マルチテナント対応: team_id を取得
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

            # A. 削除アクション
            if action == "delete":
                try:
                    attendance_service.delete_attendance(team_id, user_id, date)
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
                workspace_id=team_id,  # マルチテナント対応: workspace_id として team_id を渡す
                user_id=user_id, 
                email=email,
                date=date, 
                status=att.get("status"), 
                note=att.get("note", ""), 
                channel_id=channel, 
                ts=ts
            )
            
            # 通知カードの送信
            notification_service.notify_attendance_change(
                record=record, 
                channel=channel, 
                thread_ts=ts,
                is_update=False
            )
            
    except Exception as e:
        logger.error(f"解析・保存エラー: {e}", exc_info=True)


def register_attendance_listeners(app, attendance_service, notification_service, dispatcher=None):
    """
    勤怠操作関連のリスナーをSlack Appに登録します。
    
    Args:
        app: Slack Bolt Appインスタンス
        attendance_service: AttendanceServiceインスタンス
        notification_service: NotificationServiceインスタンス（マルチテナント対応では None でも可）
        dispatcher: InteractionDispatcherインスタンス（非同期処理用、オプション）
    """

    # ==========================================
    # 1. メッセージ受信（AI解析による勤怠登録）
    # ==========================================
    @app.event("message")
    def on_incoming_message(event, client, ack, body):
        """
        メッセージ受信ハンドラー。
        
        AI解析により勤怠情報を抽出し、自動的に記録します。
        """
        ack()

        if not should_process_message(event):
            return

        # マルチテナント対応: team_id を取得して、動的に WebClient を生成
        team_id = body.get("team_id") or event.get("team")
        
        from resources.clients.slack_client import get_slack_client
        from resources.services.notification_service import NotificationService
        
        try:
            # team_id に基づいて WebClient を取得
            dynamic_client = get_slack_client(team_id)
            
            # NotificationService を動的に生成
            dynamic_notification_service = NotificationService(dynamic_client, attendance_service)
            
            execute_attendance_from_message(
                event=event, 
                client=dynamic_client, 
                attendance_service=attendance_service, 
                notification_service=dynamic_notification_service
            )
        except Exception as e:
            logger.error(f"メッセージ処理エラー (team_id={team_id}): {e}", exc_info=True)

    # ==========================================
    # 2. 勤怠の「修正」ボタン押下
    # ==========================================
    @app.action("open_update_attendance")
    def on_update_button_clicked(ack, body, client):
        """
        勤怠カードの「修正」ボタン押下時の処理。
        
        本人確認を行い、編集モーダルを表示します。
        """
        ack()
        from resources.shared.db import get_single_attendance_record
        from resources.templates.modals import create_attendance_modal_view
        
        user_id = body["user"]["id"]
        team_id = body["team"]["id"]  # マルチテナント対応: team_id を取得
        trigger_id = body["trigger_id"]
        date = body["actions"][0].get("value")
        
        channel_id = body["container"]["channel_id"]
        message_ts = body["container"]["message_ts"]

        try:
            # マルチテナント対応: team_id に基づいて WebClient を取得
            from resources.clients.slack_client import get_slack_client
            dynamic_client = get_slack_client(team_id)
            
            record = get_single_attendance_record(team_id, user_id, date)
            
            # 本人チェック
            if record and record.get("user_id") != user_id:
                dynamic_client.chat_postEphemeral(
                    channel=channel_id,
                    user=user_id,
                    text="⚠️ この操作は打刻した本人しか行えません。"
                )
                logger.warning(
                    f"権限エラー: User {user_id} が User {record.get('user_id')} の記録を編集しようとしました"
                )
                return

            private_metadata = json.dumps({
                "date": date,
                "channel_id": channel_id,
                "message_ts": message_ts
            })
            
            initial_data = {
                "user_id": user_id,
                "date": date,
                "status": record.get("status") if record else None,
                "note": record.get("note", "") if record else ""
            }

            view = create_attendance_modal_view(
                initial_data=initial_data, 
                is_fixed_date=True
            )
            view["private_metadata"] = private_metadata 
            
            dynamic_client.views_open(trigger_id=trigger_id, view=view)
            
        except Exception as e:
            logger.error(f"修正モーダル表示失敗: {e}", exc_info=True)

    # ==========================================
    # 3. 勤怠の「取消」ボタン押下
    # ==========================================
    @app.action("delete_attendance_request")
    def on_delete_button_clicked(ack, body, client):
        """
        勤怠カードの「取消」ボタン押下時の処理。
        
        本人確認を行い、削除確認モーダルを表示します。
        """
        ack()
        from resources.shared.db import get_single_attendance_record
        from resources.templates.modals import create_attendance_delete_confirm_modal
        
        user_id = body["user"]["id"]
        team_id = body["team"]["id"]  # マルチテナント対応: team_id を取得
        trigger_id = body["trigger_id"]
        date = body["actions"][0]["value"]
        channel_id = body["container"]["channel_id"]

        try:
            # マルチテナント対応: team_id に基づいて WebClient を取得
            from resources.clients.slack_client import get_slack_client
            dynamic_client = get_slack_client(team_id)
            
            record = get_single_attendance_record(team_id, user_id, date)
            if record and record.get("user_id") != user_id:
                dynamic_client.chat_postEphemeral(
                    channel=channel_id, 
                    user=user_id, 
                    text="⚠️ 本人のみ取消可能です。"
                )
                logger.warning(
                    f"権限エラー: User {user_id} が User {record.get('user_id')} の記録を削除しようとしました"
                )
                return

            view = create_attendance_delete_confirm_modal(date) 
            view["callback_id"] = "delete_attendance_confirm_callback"
            view["private_metadata"] = json.dumps({
                "date": date,
                "message_ts": body["container"]["message_ts"],
                "channel_id": channel_id
            })
            dynamic_client.views_open(trigger_id=trigger_id, view=view)
        except Exception as e:
            logger.error(f"取消モーダル表示失敗: {e}", exc_info=True)

    # ==========================================
    # 4. 削除確認モーダルの「削除する」押下
    # ==========================================
    @app.view("delete_attendance_confirm_callback")
    def on_delete_confirmed(ack, body, client, view):
        """
        削除確認モーダルの「削除する」ボタン押下時の処理。
        """
        ack()
        
        # マルチテナント対応: team_id を取得
        team_id = body["team"]["id"]
        
        # 非同期処理が有効な場合
        if dispatcher:
            try:
                dispatcher.dispatch(body, "delete_attendance_confirm")
                logger.info("削除リクエストをキューに投げました（非同期）")
            except Exception as e:
                logger.error(f"非同期ディスパッチ失敗、同期処理にフォールバック: {e}", exc_info=True)
                # マルチテナント対応: team_id に基づいて WebClient を取得
                from resources.clients.slack_client import get_slack_client
                dynamic_client = get_slack_client(team_id)
                _execute_delete_sync(body, dynamic_client, view, attendance_service)
        else:
            # マルチテナント対応: team_id に基づいて WebClient を取得
            from resources.clients.slack_client import get_slack_client
            dynamic_client = get_slack_client(team_id)
            _execute_delete_sync(body, dynamic_client, view, attendance_service)
    
    def _execute_delete_sync(body, client, view, attendance_service):
        """勤怠削除の同期処理（内部関数）"""
        user_id = body["user"]["id"]
        team_id = body["team"]["id"]  # マルチテナント対応: team_id を取得
        metadata = json.loads(view.get("private_metadata", "{}"))

        try:
            attendance_service.delete_attendance(team_id, user_id, metadata["date"])
            client.chat_update(
                channel=metadata["channel_id"],
                ts=metadata["message_ts"],
                blocks=[{
                    "type": "context", 
                    "elements": [{
                        "type": "mrkdwn", 
                        "text": f"ⓘ <@{user_id}>さんの {metadata['date']} の勤怠連絡を取り消しました"
                    }]
                }],
                text="勤怠を取り消しました"
            )
            logger.info(f"削除成功（同期）: User={user_id}, Date={metadata['date']}")
        except Exception as e:
            logger.error(f"取消処理失敗: {e}", exc_info=True)

    # ==========================================
    # 5. 履歴表示（グローバルショートカット）
    # ==========================================
    @app.shortcut("open_kintai_history")
    def on_history_shortcut_triggered(ack, body, client):
        """
        グローバルショートカット「勤怠連絡の確認」の処理。
        
        ユーザー自身の勤怠履歴を月単位で表示します。
        """
        ack()
        from resources.templates.modals import create_history_modal_view
        
        user_id = body["user"]["id"]
        team_id = body["team"]["id"]  # マルチテナント対応: team_id を取得
        
        try:
            # マルチテナント対応: team_id に基づいて WebClient を取得
            from resources.clients.slack_client import get_slack_client
            dynamic_client = get_slack_client(team_id)
            
            today = datetime.date.today()
            month_str = today.strftime("%Y-%m")
            
            history = attendance_service.get_user_history(
                workspace_id=team_id,  # マルチテナント対応: workspace_id として team_id を渡す
                user_id=user_id, 
                month_filter=month_str
            )
            
            view = create_history_modal_view(
                history_records=history,
                selected_year=str(today.year),
                selected_month=f"{today.month:02d}",
                user_id=user_id
            )
            dynamic_client.views_open(trigger_id=body["trigger_id"], view=view)
            logger.info(f"履歴表示: User={user_id}, Month={month_str}, Count={len(history)}")
        except Exception as e:
            logger.error(f"履歴表示失敗: {e}", exc_info=True)

    # ==========================================
    # 6. 履歴モーダルの年月変更
    # ==========================================
    @app.action("history_year_change")
    @app.action("history_month_change")
    def on_history_filter_changed(ack, body, client):
        """
        履歴モーダル内の年月ドロップダウン変更時の処理。
        
        選択された年月で履歴を再取得し、モーダルをリアルタイム更新します。
        """
        ack()
        from resources.templates.modals import create_history_modal_view
        
        team_id = body["team"]["id"]  # マルチテナント対応: team_id を取得
        
        try:
            # マルチテナント対応: team_id に基づいて WebClient を取得
            from resources.clients.slack_client import get_slack_client
            dynamic_client = get_slack_client(team_id)
            
            metadata = json.loads(body["view"]["private_metadata"])
            target_user_id = metadata.get("target_user_id")
            
            state_values = body["view"]["state"]["values"]
            selected_year = state_values["history_filter"]["history_year_change"]["selected_option"]["value"]
            selected_month = state_values["history_filter"]["history_month_change"]["selected_option"]["value"]
            
            month_filter = f"{selected_year}-{selected_month}"
            
            history = attendance_service.get_user_history(
                workspace_id=team_id,  # マルチテナント対応: workspace_id として team_id を渡す
                user_id=target_user_id, 
                month_filter=month_filter
            )
            
            updated_view = create_history_modal_view(
                history_records=history,
                selected_year=selected_year,
                selected_month=selected_month,
                user_id=target_user_id
            )
            
            dynamic_client.views_update(
                view_id=body["view"]["id"], 
                hash=body["view"]["hash"], 
                view=updated_view
            )
            logger.info(f"履歴フィルタ更新: User={target_user_id}, Month={month_filter}, Count={len(history)}")
        except Exception as e:
            logger.error(f"履歴フィルタ更新失敗: {e}", exc_info=True)
