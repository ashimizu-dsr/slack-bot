"""
Slack Interaction Dispatcher (Producer)

このモジュールは、Slackからのインタラクション（ボタン押下、ショートカットなど）を受信し、
即座にack()を返した上で、処理を Pub/Sub キューに投げる Producer としての役割を担います。

重要:
- trigger_idは3秒間のみ有効なため、モーダル表示系の処理は同期処理のまま残します
- 重い処理（DB更新、通知送信など）のみをPub/Subに投げます
"""
import json
import os
import logging
from typing import Any, Dict
from google.cloud import pubsub_v1

logger = logging.getLogger(__name__)

class InteractionDispatcher:
    """
    Slackインタラクションをキューに投げるクラス
    """
    
    def __init__(self):
        """
        Pub/Sub Publisherの初期化
        """
        self.publisher = pubsub_v1.PublisherClient()
        self.project_id = os.environ.get("GCP_PROJECT_ID", os.environ.get("PROJECT_ID"))
        self.topic_name = os.environ.get("SLACK_INTERACTIONS_TOPIC", "slack-interactions-topic")
        self.topic_path = self.publisher.topic_path(self.project_id, self.topic_name)
        logger.info(f"InteractionDispatcher初期化: Topic={self.topic_path}")
    
    def dispatch(self, body: Dict[str, Any], action_type: str) -> None:
        """
        インタラクションをPub/Subキューに投げます。
        
        Args:
            body: Slackから受信したbodyデータ
            action_type: アクションタイプ（"open_update_attendance"など）
        """
        try:
            payload = {
                "type": action_type,
                "body": body
            }
            
            # Pub/Subにパブリッシュ
            future = self.publisher.publish(
                self.topic_path,
                json.dumps(payload).encode("utf-8")
            )
            
            message_id = future.result(timeout=5.0)
            logger.info(f"メッセージ送信成功: Type={action_type}, MessageID={message_id}")
        except Exception as e:
            logger.error(f"メッセージ送信失敗: {e}", exc_info=True)


def register_async_handlers(app, dispatcher: InteractionDispatcher) -> None:
    """
    非同期処理が必要なハンドラーを登録します（Pub/Sub経由）。
    
    Args:
        app: Slack Bolt Appインスタンス
        dispatcher: InteractionDispatcherインスタンス
        
    Note:
        モーダル表示系（trigger_id使用）は除外し、同期処理のままにしています。
        重い処理のみをPub/Sub経由で非同期化します。
    """
    
    # ==========================================
    # 非同期化対象: 削除確認モーダルの送信（実際の削除処理）
    # ==========================================
    @app.view("delete_attendance_confirm_callback")
    def handle_delete_confirm_submit_async(ack, body):
        """
        削除確認モーダルの「削除する」ボタン押下時の処理（非同期版）。
        
        即座にack()を返し、実際の削除処理はPub/Sub経由で実行します。
        """
        ack()
        dispatcher.dispatch(body, "delete_attendance_confirm")
        logger.info("削除リクエストをキューに投げました")
    
    # ==========================================
    # 非同期化対象: 履歴フィルタ更新（重い処理）
    # ==========================================
    # Note: views.updateは即座に実行する必要があるため、
    # このハンドラーは非同期化には適していません。
    # 代わりに、DB取得部分のみをキャッシュ化することを推奨します。
    
    logger.info("非同期ハンドラー登録完了")
