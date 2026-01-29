"""
システムイベントリスナー (Pub/Sub対応版)

このモジュールは、システム関連のSlackイベントを受け取ります。
- Bot参加（過去ログ遡り処理を含む）
- ヘルスチェック

Pub/Sub対応:
- handle_sync(): Slackイベントを受け取り、必要に応じてPub/Subに投げる（3秒以内）
- handle_async(): Pub/Subから戻ってきた後の重い処理
"""
import logging
import datetime
from typing import Optional

from resources.listeners.Listener import Listener
from resources.clients.slack_client import get_slack_client
from resources.shared.db import is_channel_history_processed, mark_channel_history_processed
from resources.shared.utils import get_user_email

logger = logging.getLogger(__name__)


class SystemListener(Listener):
    """システムイベントリスナークラス"""
   
    def __init__(self, attendance_service):
        """
        SystemListenerを初期化します
        
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
        Slackイベントを受け取る処理を登録します。
       
        Args:
            app: Slack Bolt Appインスタンス
        """
        
        logger.info("SystemListener.handle_sync: イベントハンドラーを登録中...")
       
        # ==========================================
        # 1. Botがチャンネルに参加したとき
        # ==========================================
        @app.event("member_joined_channel")
        @app.event({"type": "message", "subtype": "channel_join"})
        def on_bot_joined_channel(event, ack, body):
            """
            Botがチャンネルに参加したときの処理。
            Bot自身の参加のみを処理し、他のユーザーの参加は無視します。
           
            過去7日間のメッセージを取得して解析します。
            """
            ack()
            
            try:
                # マルチテナント対応: team_id を取得
                team_id = body.get("team_id") or event.get("team")
                channel_id = event.get("channel")
                joined_user_id = event.get("user")
               
                # team_id に基づいて WebClient を取得
                dynamic_client = get_slack_client(team_id)
               
                bot_user_id = dynamic_client.auth_test()["user_id"]
                
                # Bot自身の参加でない場合は即座にスキップ
                if joined_user_id != bot_user_id:
                    logger.debug(
                        f"[Bot参加イベント] スキップ（他ユーザーの参加）: "
                        f"User={joined_user_id}, Channel={channel_id}"
                    )
                    return
                
                # Bot自身の参加のみ処理
                logger.info(
                    f"[Bot参加イベント] Bot自身の参加を検知: "
                    f"Team={team_id}, Channel={channel_id}, Bot User={bot_user_id}"
                )
                
                # Pub/Subに投げる（非同期処理へ）
                self.publish_to_worker(
                    team_id=team_id,
                    event={
                        "type": "bot_joined_channel",
                        "event": event
                    }
                )
                
            except Exception as e:
                logger.error(f"[Bot参加イベント] エラー: {e}", exc_info=True)
        
        logger.info("SystemListener.handle_sync: member_joined_channel ハンドラー登録完了")

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
            if event_type == "bot_joined_channel":
                self._process_channel_history(team_id, event.get("event"))
            else:
                logger.warning(f"Unknown event type in SystemListener: {event_type}")
        except Exception as e:
            logger.error(f"SystemListener非同期処理エラー ({event_type}): {e}", exc_info=True)

    # ======================================================================
    # プライベートメソッド
    # ======================================================================
    def _process_channel_history(self, team_id: str, event: dict):
        """
        チャンネルの過去7日間のメッセージを取得し、勤怠情報を解析・保存します。
        
        Args:
            team_id: ワークスペースID
            event: member_joined_channelイベント
        """
        channel_id = event.get("channel")
        
        logger.info(f"[過去ログ処理] 開始: Team={team_id}, Channel={channel_id}")
        
        # 処理済みチェック
        if is_channel_history_processed(team_id, channel_id):
            logger.info(f"[過去ログ処理] スキップ（処理済み）: Channel={channel_id}")
            return
        
        try:
            client = get_slack_client(team_id)
            
            # 過去7日間のタイムスタンプを計算
            now = datetime.datetime.now()
            seven_days_ago = now - datetime.timedelta(days=7)
            oldest_ts = seven_days_ago.timestamp()
            
            logger.info(
                f"[過去ログ処理] 取得条件: Channel={channel_id}, "
                f"期間={seven_days_ago.strftime('%Y-%m-%d %H:%M:%S')} ～ {now.strftime('%Y-%m-%d %H:%M:%S')}, "
                f"oldest_ts={oldest_ts}"
            )
            
            # 過去メッセージを取得（ページネーション対応）
            all_messages = []
            cursor = None
            page_count = 0
            
            while True:
                try:
                    page_count += 1
                    logger.info(f"[過去ログ処理] API呼び出し: ページ={page_count}, cursor={cursor[:20] if cursor else 'None'}")
                    
                    response = client.conversations_history(
                        channel=channel_id,
                        oldest=str(oldest_ts),
                        limit=100,
                        cursor=cursor
                    )
                    
                    if response.get("ok"):
                        messages = response.get("messages", [])
                        all_messages.extend(messages)
                        
                        logger.info(
                            f"[過去ログ処理] ページ{page_count}取得成功: "
                            f"{len(messages)}件（累計: {len(all_messages)}件）"
                        )
                        
                        # 次のページがあるか確認
                        cursor = response.get("response_metadata", {}).get("next_cursor")
                        if not cursor:
                            logger.info(f"[過去ログ処理] 全ページ取得完了: {page_count}ページ")
                            break
                    else:
                        error_msg = response.get('error')
                        logger.error(f"[過去ログ処理] API エラー: {error_msg}")
                        break
                        
                except Exception as e:
                    logger.error(f"[過去ログ処理] 取得エラー: ページ={page_count}, {e}", exc_info=True)
                    break
            
            logger.info(f"[過去ログ処理] メッセージ取得完了: 総数={len(all_messages)}件")
            
            # メッセージを古い順にソート（tsの昇順）
            # Slack APIは新しい順で返すため、逆順にして古いメッセージから処理する
            all_messages.sort(key=lambda m: float(m.get("ts", 0)))
            logger.info(f"[過去ログ処理] メッセージを古い順にソート完了")
            
            # 各メッセージを解析
            processed_count = 0
            skipped_count = 0
            error_count = 0
            
            logger.info(f"[過去ログ解析開始] 対象: {len(all_messages)}件のメッセージ（古い順）")
            
            for idx, msg in enumerate(all_messages, 1):
                user_id = msg.get("user")
                text = (msg.get("text") or "").strip()
                ts = msg.get("ts")
                bot_id = msg.get("bot_id")
                subtype = msg.get("subtype")
                
                # スキップ条件のログ出力
                if bot_id:
                    logger.debug(f"[{idx}/{len(all_messages)}] スキップ（Bot投稿）: bot_id={bot_id}")
                    skipped_count += 1
                    continue
                
                if subtype:
                    logger.debug(f"[{idx}/{len(all_messages)}] スキップ（サブタイプ）: subtype={subtype}")
                    skipped_count += 1
                    continue
                
                if not user_id or not text:
                    logger.debug(f"[{idx}/{len(all_messages)}] スキップ（データ不足）: user_id={user_id}, text_len={len(text) if text else 0}")
                    skipped_count += 1
                    continue
                
                # 処理対象のメッセージをログ出力
                text_preview = text[:50] + "..." if len(text) > 50 else text
                logger.info(
                    f"[{idx}/{len(all_messages)}] 処理中: User={user_id}, "
                    f"Text='{text_preview}', TS={ts}"
                )
                
                try:
                    # ユーザー情報を取得
                    email: Optional[str] = get_user_email(client, user_id, logger)
                    
                    # 勤怠情報を解析・保存（通知なし）
                    result = self.attendance_service.process_historical_message(
                        workspace_id=team_id,
                        user_id=user_id,
                        email=email,
                        text=text,
                        channel_id=channel_id,
                        ts=ts
                    )
                    
                    if result:
                        processed_count += 1
                        logger.info(f"[{idx}/{len(all_messages)}] ✓ 保存成功: User={user_id}")
                    else:
                        logger.info(f"[{idx}/{len(all_messages)}] - AI解析失敗（勤怠情報なし）: User={user_id}")
                        skipped_count += 1
                        
                except Exception as e:
                    error_count += 1
                    logger.error(
                        f"[{idx}/{len(all_messages)}] ✗ 処理エラー: User={user_id}, "
                        f"Error={str(e)}", exc_info=True
                    )
            
            # 処理済みフラグを立てる
            mark_channel_history_processed(team_id, channel_id)
            
            logger.info(
                f"[過去ログ処理完了] Channel={channel_id}, "
                f"総メッセージ数={len(all_messages)}件, "
                f"保存成功={processed_count}件, "
                f"スキップ={skipped_count}件, "
                f"エラー={error_count}件"
            )
            
        except Exception as e:
            logger.error(f"チャンネル過去ログ処理エラー: {e}", exc_info=True)
