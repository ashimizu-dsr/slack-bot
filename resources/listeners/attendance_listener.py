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
from resources.services.nlp_service import (
    extract_attendance_from_text,
    reply_has_explicit_cancellation_keywords,
    reply_has_late_cancellation_phrases,
    resolve_target_user_for_cancellation,
    should_cancel_without_ai,
)
from resources.shared.utils import get_user_email
from resources.templates.modals import create_history_modal_view
from resources.clients.slack_client import get_slack_client, fetch_message_in_channel
from resources.services.notification_service import NotificationService
from resources.shared.db import get_single_attendance_record, get_global_user_list
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
                user_email = None
                if ul := get_global_user_list():
                    mu = next(
                        (u for u in ul if (u.get("user_id") or "") == user_id),
                        None,
                    )
                    if mu:
                        user_email = (mu.get("email") or "").strip() or None
                record = get_single_attendance_record(
                    team_id, user_id, date, email=user_email
                )
                if record:
                    rid = record.get("user_id") or ""
                    rem = (record.get("email") or "").strip().lower()
                    uem = (user_email or "").strip().lower()
                    if rid != user_id and (not uem or rem != uem):
                        dynamic_client.chat_postEphemeral(
                            channel=channel_id,
                            user=user_id,
                            text="⚠️ この操作は打刻した本人しか行えません。"
                        )
                        logger.warning(
                            f"権限エラー: User {user_id} が User {rid} の記録を編集しようとしました"
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
                user_email = None
                if ul := get_global_user_list():
                    mu = next((u for u in ul if (u.get("user_id") or "") == user_id), None)
                    if mu:
                        user_email = (mu.get("email") or "").strip() or None
                record = get_single_attendance_record(
                    team_id, user_id, date, email=user_email
                )
                
                if record:
                    rid = record.get("user_id") or ""
                    rem = (record.get("email") or "").strip().lower()
                    uem = (user_email or "").strip().lower()
                    if rid != user_id and (not uem or rem != uem):
                        dynamic_client.chat_postEphemeral(
                            channel=channel_id,
                            user=user_id,
                            text="⚠️ 本人のみ取消可能です。"
                        )
                        logger.warning(
                            f"権限エラー: User {user_id} が User {rid} の記録を削除しようとしました"
                        )
                        return

                view = create_attendance_delete_confirm_modal(date)
                view["callback_id"] = "delete_attendance_confirm_callback"
                view["private_metadata"] = json.dumps({
                    "date": date,
                    "message_ts": body["container"]["message_ts"],
                    "channel_id": channel_id,
                    "user_id": user_id,
                    "email": user_email or "",
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
            # 最優先: 3秒以内にSlackへ応答
            ack()
            
            user_id = body["user"]["id"]
            team_id = body["team"]["id"]
            
            try:
                dynamic_client = get_slack_client(team_id)
                
                today = datetime.date.today()
                month_str = today.strftime("%Y-%m")
                
                # 1. まず空のモーダルを即座に開く
                view = create_history_modal_view(
                    history_records=[],
                    selected_year=str(today.year),
                    selected_month=f"{today.month:02d}",
                    user_id=user_id
                )
                
                response = dynamic_client.views_open(trigger_id=body["trigger_id"], view=view)
                logger.info(f"履歴モーダル表示: User={user_id}, Month={month_str}")
                
                # 2. モーダルを開いた後、データを取得して更新
                if response["ok"]:
                    view_id = response["view"]["id"]
                    
                    # データ取得（これは3秒以降でもOK）
                    history = self.attendance_service.get_user_history(
                        workspace_id=team_id,
                        user_id=user_id, 
                        month_filter=month_str
                    )
                    
                    # モーダルを更新
                    updated_view = create_history_modal_view(
                        history_records=history,
                        selected_year=str(today.year),
                        selected_month=f"{today.month:02d}",
                        user_id=user_id
                    )
                    
                    dynamic_client.views_update(
                        view_id=view_id,
                        hash=response["view"]["hash"],
                        view=updated_view
                    )
                    
                    logger.info(f"履歴データ更新完了: User={user_id}, Count={len(history)}")
                
            except Exception as e:
                logger.error(f"履歴表示失敗: {e}", exc_info=True)

        # ==========================================
        # 6. 履歴モーダルの年月変更
        # ==========================================
        @app.action("history_year_change")
        @app.action("history_month_change")
        def on_history_filter_changed(ack, body):
            """履歴モーダル内の年月ドロップダウン変更時の処理"""
            # 最優先: 3秒以内にSlackへ応答
            ack()
            
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
            except Exception as e:
                logger.error(f"履歴フィルタ更新失敗: {e}", exc_info=True)

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
        
        # スレッド内のメッセージも処理する（電車遅延等でギリギリ間に合った場合の「間に合った」報告をAIで判定するため）
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
            sender_email: Optional[str] = get_user_email(client, user_id, logger)
            # AI抽出・照合用に get_global_user_list を使用（従来動作を維持。検索精度・表示順に影響）
            # target_user_id の厳格検証・users_info 確認は別途実施して誤割当を防止
            workspace_user_list = get_global_user_list()

            # スレッド返信かどうかを判定（親メッセージの取得はまだ行わない）
            thread_ts = event.get("thread_ts")
            is_thread_reply = bool(thread_ts and thread_ts != ts)

            # 取消フレーズの事前チェック（AIスキップ）
            # ・「間に合った/間に合いました」→ 時間・コンテキスト不問で常に取消
            # ・スレッド返信かつ「出社した/出社しました」→ 取消
            # ・スタンドアロンかつ「出社した/出社しました」かつ9時前 → 取消
            # ※取消パスでは thread_context を使わないため、ここでは fetch_message_in_channel を呼ばない
            if should_cancel_without_ai(text, ts, is_thread_reply=is_thread_reply):
                target_user_id = resolve_target_user_for_cancellation(
                    text, user_id, workspace_user_list or None
                )
                if target_user_id != user_id:
                    logger.info(
                        f"他者報告を検出（取消対象: {target_user_id}, 送信者: {user_id}）: text={text[:30]}..."
                    )
                else:
                    logger.info(f"早朝出社報告を検出（AIスキップ・今日の記録を削除）: text={text[:30]}..., ts={ts}")
                try:
                    client.reactions_add(channel=channel, name="outbox_tray", timestamp=ts)
                except Exception:
                    pass
                today_str = datetime.date.today().isoformat()
                target_email = None
                if workspace_user_list:
                    matched_u = next(
                        (u for u in workspace_user_list if (u.get("user_id") or "") == target_user_id),
                        None,
                    )
                    if matched_u:
                        target_email = (matched_u.get("email") or "").strip() or None
                notification_service = NotificationService(client, self.attendance_service)
                try:
                    self.attendance_service.delete_attendance(team_id, target_user_id, today_str)
                    notification_service.notify_attendance_change(
                        record={"user_id": target_user_id, "date": today_str, "email": target_email},
                        channel=channel,
                        thread_ts=ts,
                        is_delete=True
                    )
                except Exception as e:
                    logger.info(f"早朝出社取消削除失敗/スキップ: {today_str}, Error: {e}")
                    try:
                        client.chat_postMessage(
                            channel=channel,
                            thread_ts=ts,
                            text=f"⚠️ {today_str} の勤怠記録が見つかりませんでした。すでに取り消されているか、記録されていない可能性があります。"
                        )
                    except Exception:
                        pass
                return

            # スレッド返信の場合は親メッセージを取得し「親＋子」をセットでAIに渡す
            # （取消パスを抜けた後のみ呼び出す。T09R8SWTW49 の not_in_channel エラーを不要に発生させない）
            thread_context: Optional[str] = None
            if is_thread_reply:
                parent_msg = fetch_message_in_channel(client, channel, thread_ts)
                parent_text = (parent_msg.get("text") or "").strip() if parent_msg else ""
                thread_context = f"親メッセージ:\n{parent_text}\n\n返信:\n{text}"
                logger.info(f"スレッド返信を検出: 親+子をセットでAIに渡します thread_ts={thread_ts}")

            # 1. AI解析実行（誰の勤怠かは target_user_id で返る）
            extraction = extract_attendance_from_text(
                text,
                team_id=team_id,
                user_id=user_id,
                message_ts=ts if thread_context else None,
                thread_context=thread_context,
                workspace_user_list=workspace_user_list if workspace_user_list else None,
            )
            
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

            # 2. 誰の勤怠として記録するか（メッセージ内の人名 → target_email、email を主キーとして使用）
            target_email = (extraction.get("target_email") or "").strip().lower()
            if target_email:
                if not workspace_user_list:
                    logger.warning("target_email が指定されましたが workspace_user_list が空のため検証できません。記録を中断")
                    try:
                        client.chat_postEphemeral(
                            channel=channel,
                            user=user_id,
                            text="⚠️ メッセージ内の対象ユーザーを特定できませんでした。記録を中断しました。"
                        )
                    except Exception:
                        pass
                    return
                matched = next(
                    (u for u in workspace_user_list
                     if (u.get("email") or "").strip().lower() == target_email),
                    None,
                )
                if matched:
                    effective_email = (matched.get("email") or "").strip()
                    effective_user_id = matched.get("user_id") or ""
                    is_other_person = bool(effective_user_id)
                else:
                    logger.warning(
                        f"target_email がユーザーリストに存在しません: {target_email}, 記録を中断"
                    )
                    try:
                        client.chat_postEphemeral(
                            channel=channel,
                            user=user_id,
                            text="⚠️ メッセージ内の対象ユーザーを特定できませんでした。記録を中断しました。"
                        )
                    except Exception:
                        pass
                    return
            else:
                effective_user_id = user_id
                effective_email = sender_email or ""
                is_other_person = False

            # 3. 抽出結果をリスト化（複数日対応）
            attendances = [extraction]
            if "_additional_attendances" in extraction:
                attendances.extend(extraction["_additional_attendances"])

            # NotificationService を動的に生成
            notification_service = NotificationService(client, self.attendance_service)

            # 4. ループ処理
            for att in attendances:
                date = att.get("date")
                action = att.get("action", "save")

                # A. 削除アクション
                if action == "delete":
                    # スレッド返信時: 誤取消を防ぐガード（パターンA or パターンB のみ削除実行）
                    if thread_context:
                        pattern_a = reply_has_explicit_cancellation_keywords(text)
                        pattern_b = reply_has_late_cancellation_phrases(text)
                        if not pattern_a and not pattern_b:
                            logger.info(
                                f"スレッド返信の削除をスキップ（ガード）: text={text[:30]}..."
                            )
                            try:
                                client.chat_postEphemeral(
                                    channel=channel,
                                    user=user_id,
                                    text="取消する場合は、メッセージに「取消」「キャンセル」「取り消し」「削除」「間に合った」「出社した」のいずれかを含めて送信してください。"
                                )
                            except Exception:
                                pass
                            continue
                    try:
                        self.attendance_service.delete_attendance(team_id, effective_user_id, date)
                        notification_service.notify_attendance_change(
                            record={"user_id": effective_user_id, "date": date, "email": effective_email},
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
                    user_id=effective_user_id,
                    email=effective_email,
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
        date_val = metadata.get("date", "")
        user_email = (metadata.get("email") or "").strip() or None

        try:
            client = get_slack_client(team_id)

            # 削除実行（email で検索フォールバック、別ワークスペースのユーザー対応）
            self.attendance_service.delete_attendance(
                team_id, user_id, date_val, email=user_email
            )
            
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
                record={"user_id": user_id, "date": date_val, "email": user_email},
                channel=metadata["channel_id"],
                thread_ts=metadata["message_ts"],
                is_delete=True
            )

            logger.info(f"削除成功（非同期）: User={user_id}, Date={date_val}")
        except Exception as e:
            logger.error(f"取消処理失敗: {e}", exc_info=True)
