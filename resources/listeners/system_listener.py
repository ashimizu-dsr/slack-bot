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
       
        # ==========================================
        # 1. Botがチャンネルに参加したとき
        # ==========================================
        @app.event("member_joined_channel")
        def on_bot_joined_channel(event, ack, body):
            """
            Botがチャンネルに参加したときの処理。
           
            過去7日間のメッセージを取得して解析します。
            """
            ack()
            
            try:
                # マルチテナント対応: team_id を取得
                team_id = body.get("team_id") or event.get("team")
               
                # team_id に基づいて WebClient を取得
                dynamic_client = get_slack_client(team_id)
               
                bot_user_id = dynamic_client.auth_test()["user_id"]
                
                # Bot自身の参加のみ処理
                if event.get("user") == bot_user_id:
                    logger.info(f"Bot参加検知: Channel={event.get('channel')}, Workspace={team_id}")
                    
                    # Pub/Subに投げる（非同期処理へ）
                    self.publish_to_worker(
                        team_id=team_id,
                        event={
                            "type": "bot_joined_channel",
                            "event": event
                        }
                    )
            except Exception as e:
                logger.error(f"Bot参加処理エラー: {e}", exc_info=True)

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
        
        # 処理済みチェック
        if is_channel_history_processed(team_id, channel_id):
            logger.info(f"チャンネル過去ログ処理済みのためスキップ: {channel_id}")
            return
        
        try:
            client = get_slack_client(team_id)
            
            # 過去7日間のタイムスタンプを計算
            now = datetime.datetime.now()
            seven_days_ago = now - datetime.timedelta(days=7)
            oldest_ts = seven_days_ago.timestamp()
            
            logger.info(f"過去ログ取得開始: Channel={channel_id}, 期間=7日間")
            
            # 過去メッセージを取得（ページネーション対応）
            all_messages = []
            cursor = None
            
            while True:
                try:
                    response = client.conversations_history(
                        channel=channel_id,
                        oldest=str(oldest_ts),
                        limit=100,
                        cursor=cursor
                    )
                    
                    if response.get("ok"):
                        messages = response.get("messages", [])
                        all_messages.extend(messages)
                        
                        # 次のページがあるか確認
                        cursor = response.get("response_metadata", {}).get("next_cursor")
                        if not cursor:
                            break
                    else:
                        logger.error(f"過去ログ取得失敗: {response.get('error')}")
                        break
                        
                except Exception as e:
                    logger.error(f"過去ログ取得エラー: {e}", exc_info=True)
                    break
            
            logger.info(f"過去ログ取得完了: {len(all_messages)}件のメッセージ")
            
            # 各メッセージを解析
            processed_count = 0
            for msg in all_messages:
                # Bot自身のメッセージや、サブタイプがあるメッセージはスキップ
                if msg.get("bot_id") or msg.get("subtype"):
                    continue
                
                user_id = msg.get("user")
                text = (msg.get("text") or "").strip()
                ts = msg.get("ts")
                
                if not user_id or not text:
                    continue
                
                # ユーザー情報を取得
                email: Optional[str] = get_user_email(client, user_id, logger)
                
                # 勤怠情報を解析・保存（通知なし）
                if self.attendance_service.process_historical_message(
                    workspace_id=team_id,
                    user_id=user_id,
                    email=email,
                    text=text,
                    channel_id=channel_id,
                    ts=ts
                ):
                    processed_count += 1
            
            # 処理済みフラグを立てる
            mark_channel_history_processed(team_id, channel_id)
            
            logger.info(
                f"過去ログ処理完了: Channel={channel_id}, "
                f"解析={len(all_messages)}件, 保存={processed_count}件"
            )
            
        except Exception as e:
            logger.error(f"チャンネル過去ログ処理エラー: {e}", exc_info=True)
