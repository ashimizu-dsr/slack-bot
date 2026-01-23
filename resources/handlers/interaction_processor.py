"""
Slack Interaction Processor (Consumer)

このモジュールは、Pub/Subキューから届いたインタラクションを処理する Consumer としての役割を担います。
実際のビジネスロジック（DB取得、更新、通知送信など）を実行します。
"""
import json
import logging
from typing import Any, Dict
from slack_sdk import WebClient

logger = logging.getLogger(__name__)


class InteractionProcessor:
    """
    Pub/Subから届いたインタラクションを処理するクラス
    """
    
    def __init__(self, slack_client: WebClient, attendance_service, notification_service):
        """
        プロセッサの初期化
        
        Args:
            slack_client: Slack WebClientインスタンス
            attendance_service: AttendanceServiceインスタンス
            notification_service: NotificationServiceインスタンス
        """
        self.client = slack_client
        self.attendance_service = attendance_service
        self.notification_service = notification_service
        logger.info("InteractionProcessor初期化完了")
    
    def process(self, message: Dict[str, Any]) -> None:
        """
        Pub/Subから届いたメッセージを処理します。
        
        Args:
            message: Pub/Subメッセージ
                {
                    "type": "delete_attendance_confirm",
                    "body": {...}
                }
        """
        try:
            event_type = message.get("type")
            body = message.get("body")
            
            if not event_type or not body:
                logger.error(f"無効なメッセージ: {message}")
                return
            
            logger.info(f"処理開始: Type={event_type}")
            
            # イベントタイプに応じて処理を振り分け
            if event_type == "delete_attendance_confirm":
                self._handle_delete_attendance(body)
            else:
                logger.warning(f"未知のイベントタイプ: {event_type}")
        
        except Exception as e:
            logger.error(f"メッセージ処理失敗: {e}", exc_info=True)
            raise
    
    def _handle_delete_attendance(self, body: Dict[str, Any]) -> None:
        """
        勤怠削除処理を実行します。
        
        Args:
            body: Slackから受信したbodyデータ
        """
        try:
            user_id = body["user"]["id"]
            workspace_id = body["team"]["id"]
            view = body.get("view", {})
            metadata = json.loads(view.get("private_metadata", "{}"))
            
            # 勤怠を削除
            self.attendance_service.delete_attendance(
                workspace_id=workspace_id,
                user_id=user_id,
                date=metadata["date"]
            )
            
            # メッセージを更新
            self.client.chat_update(
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
            
            logger.info(f"削除成功: User={user_id}, Date={metadata['date']}")
        
        except Exception as e:
            logger.error(f"削除処理失敗: {e}", exc_info=True)
            raise


def create_pubsub_endpoint(app, processor: InteractionProcessor):
    """
    Pub/Sub Push エンドポイントを作成します。
    
    Args:
        app: Flask/FastAPIアプリケーションインスタンス
        processor: InteractionProcessorインスタンス
        
    Returns:
        エンドポイント関数
        
    Example (Flask):
        @app.route("/pubsub/interactions", methods=["POST"])
        def handle_pubsub_push():
            return create_pubsub_endpoint(app, processor)(request)
    """
    def handle_push(request):
        """
        Pub/Sub Pushリクエストを処理します。
        
        Args:
            request: HTTPリクエストオブジェクト
            
        Returns:
            HTTPレスポンス
        """
        try:
            # Pub/Subメッセージを取得
            envelope = request.get_json()
            
            if not envelope:
                logger.warning("空のメッセージを受信")
                return {"status": "error", "message": "No Pub/Sub message received"}, 400
            
            # Base64デコード
            pubsub_message = envelope.get("message", {})
            data = pubsub_message.get("data")
            
            if not data:
                logger.warning("dataフィールドが空")
                return {"status": "error", "message": "No data field"}, 400
            
            # デコードしてJSONパース
            import base64
            decoded = base64.b64decode(data).decode("utf-8")
            message = json.loads(decoded)
            
            # 処理を実行
            processor.process(message)
            
            return {"status": "ok"}, 200
        
        except Exception as e:
            logger.error(f"Pub/Subメッセージ処理失敗: {e}", exc_info=True)
            return {"status": "error", "message": str(e)}, 500
    
    return handle_push
