"""
勤怠操作リスナー (Pub/Sub対応版)

このモジュールは、勤怠記録に関するSlackイベントを受け取ります。
- メッセージからのAI解析による勤怠登録
- 修正ボタン押下
- 削除ボタン押下
- 削除確認の送信
- 履歴表示

Pub/Sub対応:
- handle_sync(): Slackイベントを受け取り、Pub/Subに投げる（3秒以内）
- handle_async(): Pub/Subから戻ってきた後の重い処理
"""
import datetime
import json
import logging
from typing import Optional

from resources.listeners.Listener import Listener
from resources.services.nlp_service import extract_attendance_from_text
from resources.shared.utils import get_user_email
from resources.templates.modals import create_history_modal_view
from resources.clients.slack_client import get_slack_client
from resources.services.notification_service import NotificationService
from resources.shared.db import get_single_attendance_record
from resources.templates.modals import (
    create_attendance_modal_view,
    create_attendance_delete_confirm_modal
)
from resources.shared.setup_logger import log_ai_parse_failure

logger = logging.getLogger(__name__)

# 処理済みメッセージのタイムスタンプを記録（重複防止用）
_processed_message_ts = set()


class AttendanceListener(Listener):
    """勤怠操作リスナークラス"""
    
    def __init__(self, attendance_service):
        """
        Args:
            attendance_service: AttendanceServiceインスタンス
        """
        super().__init__()
        self.attendance_service = attendance_service

    # ======================================================================
    # 同期処理: Slackイベントの受付（3秒以内に返す）
    # ======================================================================
    def handle_sync(self, app):
        """
        Slackイベントを受け取り、Pub/Subへ投げる処理を登録します。
        
        Args:
            app: Slack Bolt Appインスタンス
        """
        
        # ==========================================
        # 1. メッセージ受信（AI解析による勤怠登録）
        # ==========================================
        @app.event("message")
        def on_incoming_message(event, ack, body):
            """メッセージ受信ハンドラー（受付のみ）"""
            if not self._should_process_message(event):
                return
            
            ack()
            
            team_id = body.get("team_id") or event.get("team")
            
            # Pub/Subに投げる（非同期処理へ）
            self.publish_to_worker(
                team_id=team_id,
                event={
                    "type": "message",
                    "event": event
                }
            )

        # ==========================================
        # 2. 勤怠の「修正」ボタン押下
        # ==========================================
        @app.action("open_update_attendance")
        def on_update_button_clicked(ack, body, client):
            """勤怠カードの「修正」ボタン押下時の処理"""
            ack()
            
            user_id = body["user"]["id"]
            team_id = body["team"]["id"]
            trigger_id = body["trigger_id"]
            date = body["actions"][0].get("value")
            
            channel_id = body["container"]["channel_id"]
            message_ts = body["container"]["message_ts"]

            try:
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
            """勤怠カードの「取消」ボタン押下時の処理"""
            ack()
            
            user_id = body["user"]["id"]
            team_id = body["team"]["id"]
            trigger_id = body["trigger_id"]
            date = body["actions"][0]["value"]
            channel_id = body["container"]["channel_id"]

            try:
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
        def on_delete_confirmed(ack, body, view):
            """削除確認モーダルの「削除する」ボタン押下時の処理"""
            ack()
            
            team_id = body["team"]["id"]
            
            # Pub/Subに投げる（非同期処理へ）
            self.publish_to_worker(
                team_id=team_id,
                event={
                    "type": "delete_attendance_confirm",
                    "body": body,
                    "view": view
                }
            )
        
        # ==========================================
        # 5. 履歴表示（グローバルショートカット）
        # ==========================================
        @app.shortcut("open_kintai_history")
        def on_history_shortcut_triggered(ack, body):
            """グローバルショートカット「勤怠連絡の確認」の処理"""
            user_id = body["user"]["id"]
            team_id = body["team"]["id"]
            
            try:
                dynamic_client = get_slack_client(team_id)
                
                today = datetime.date.today()
                month_str = today.strftime("%Y-%m")
                
                # 重い処理（1か月分の勤怠データを抽出する）
                history = self.attendance_service.get_user_history(
                    workspace_id=team_id,
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
                ack()
                
                logger.info(f"履歴表示: User={user_id}, Month={month_str}, Count={len(history)}")
            except Exception as e:
                ack()
                logger.error(f"履歴表示失敗: {e}", exc_info=True)

        # ==========================================
        # 6. 履歴モーダルの年月変更
        # ==========================================
        @app.action("history_year_change")
        @app.action("history_month_change")
        def on_history_filter_changed(ack, body):
            """履歴モーダル内の年月ドロップダウン変更時の処理"""
            team_id = body["team"]["id"]
            
            try:
                dynamic_client = get_slack_client(team_id)
                
                metadata = json.loads(body["view"]["private_metadata"])
                target_user_id = metadata.get("target_user_id")
                
                state_values = body["view"]["state"]["values"]
                selected_year = state_values["history_filter"]["history_year_change"]["selected_option"]["value"]
                selected_month = state_values["history_filter"]["history_month_change"]["selected_option"]["value"]
                
                month_filter = f"{selected_year}-{selected_month}"
                
                # 重い処理（1か月分の勤怠データを抽出する）
                history = self.attendance_service.get_user_history(
                    workspace_id=team_id,
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
                ack()
            except Exception as e:
                logger.error(f"履歴フィルタ更新失敗: {e}", exc_info=True)
                ack()

    # ======================================================================
    # 非同期処理: Pub/Subから戻ってきた後の重い処理
    # ======================================================================
    def handle_async(self, team_id: str, event: dict):
        """
        Pub/Subから戻ってきた後の重い処理を実行します。
        
        Args:
            team_id: ワークスペースID
            event: イベントデータ
        """
        event_type = event.get("type")
        
        try:
            if event_type == "message":
                # メッセージからAI解析を実行し、勤怠記録を保存
                self._handle_message_async(team_id, event.get("event"))
                
            elif event_type == "delete_attendance_confirm":
                # 勤怠削除処理
                self._handle_delete_async(team_id, event.get("body"), event.get("view"))
                
            else:
                logger.warning(f"Unknown event type: {event_type}")
                
        except Exception as e:
            logger.error(f"非同期処理エラー ({event_type}): {e}", exc_info=True)

    # ======================================================================
    # プライベートメソッド
    # ======================================================================
    def _should_process_message(self, event) -> bool:
        """メッセージを処理すべきかどうかを判定します"""
        user_id = event.get("user")
        text = (event.get("text") or "").strip()
        
        if not user_id or not text:
            return False
        
        # Bot判定
        if event.get("bot_id") or event.get("bot_profile"):
            return False
        
        # サブタイプのチェック（編集・削除・システムメッセージなどは除外）
        if event.get("subtype"):
            return False
        
        # スレッド内のメッセージは除外（thread_tsがあり、それが自分のtsと異なる場合）
        thread_ts = event.get("thread_ts")
        if thread_ts and thread_ts != event.get("ts"):
            logger.info(f"スレッド内メッセージのため処理をスキップ: channel={event.get('channel')}, thread_ts={thread_ts}")
            return False
        
        return True

    def _handle_message_async(self, team_id: str, event: dict):
        """メッセージからAI解析を実行し、勤怠記録を保存します"""
        user_id = event.get("user")
        ts = event.get("ts")
        channel = event.get("channel")
        text = (event.get("text") or "").strip()

        # 重複処理の防止
        msg_key = f"{channel}:{ts}"
        if msg_key in _processed_message_ts:
            return
        _processed_message_ts.add(msg_key)

        try:
            client = get_slack_client(team_id)
            email: Optional[str] = get_user_email(client, user_id, logger)
            
            # 1. AI解析実行（team_id, user_idを渡してコストログを出力）
            extraction = extract_attendance_from_text(text, team_id=team_id, user_id=user_id)
            
            if not extraction:
                # AI解析失敗時のログを構造化ログとして出力
                log_ai_parse_failure(
                    logger=logger,
                    team_id=team_id,
                    channel_id=channel,
                    user_id=user_id,
                    text=text,
                    reason="勤怠データが抽出できませんでした"
                )
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

            # NotificationService を動的に生成
            notification_service = NotificationService(client, self.attendance_service)

            # 3. ループ処理
            for att in attendances:
                date = att.get("date")
                action = att.get("action", "save")

                # A. 削除アクション
                if action == "delete":
                    try:
                        self.attendance_service.delete_attendance(team_id, user_id, date)
                        notification_service.notify_attendance_change(
                            record={"user_id": user_id, "date": date},
                            channel=channel,
                            thread_ts=ts,
                            is_delete=True
                        )
                    except Exception as e:
                        logger.info(f"Delete failed/skipped: {date}, Error: {e}")
                        # 削除対象が見つからない場合もユーザーに通知
                        try:
                            client.chat_postMessage(
                                channel=channel,
                                thread_ts=ts,
                                text=f"⚠️ {date} の勤怠記録が見つかりませんでした。すでに取り消されているか、記録されていない可能性があります。"
                            )
                        except Exception:
                            pass
                    continue

                # B. 保存・更新アクション
                record = self.attendance_service.save_attendance(
                    workspace_id=team_id,
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

    def _handle_delete_async(self, team_id: str, body: dict, view: dict):
        """勤怠削除の非同期処理"""
        user_id = body["user"]["id"]
        metadata = json.loads(view.get("private_metadata", "{}"))

        try:
            client = get_slack_client(team_id)
            
            # 削除実行
            self.attendance_service.delete_attendance(team_id, user_id, metadata["date"])
            
            # 元のメッセージを更新
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
            
            # 削除通知を送信（スレッド返信として）
            notification_service = NotificationService(client, self.attendance_service)
            notification_service.notify_attendance_change(
                record={"user_id": user_id, "date": metadata["date"]},
                channel=metadata["channel_id"],
                thread_ts=metadata["message_ts"],
                is_delete=True
            )
            
            logger.info(f"削除成功（非同期）: User={user_id}, Date={metadata['date']}")
        except Exception as e:
            logger.error(f"取消処理失敗: {e}", exc_info=True)
