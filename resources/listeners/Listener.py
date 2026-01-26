# resources/listeners/listener.py
"""
Listener基底クラス

このモジュールは、全てのリスナーの基底クラスを提供します。
Pub/Subを使った非同期処理の枠組みを提供し、同期処理と非同期処理を
1つのクラスに集約することで保守性を向上させます。

使い方:
1. Listenerを継承したクラスを作成
2. handle_sync(): Slackイベントを受け取る処理（3秒以内に返す）
3. handle_async(): Pub/Subから戻ってきた後の重い処理
"""
import json
import os
import logging
from abc import ABC, abstractmethod
from typing import Optional
from google.cloud import pubsub_v1

logger = logging.getLogger(__name__)


class Listener(ABC):
    """
    Slack Listenerの基底クラス
    
    Pub/Subを使った非同期処理の枠組みを提供します。
    子クラスはhandle_sync()とhandle_async()を実装する必要があります。
    """
    
    def __init__(self):
        """
        Listenerを初期化します。
        
        - action_typeはクラス名を使用（例: AttendanceListener）
        - PUBSUB_TOPIC_IDが設定されている場合、Pub/Sub Publisherを初期化
        """
        self.action_type = self.__class__.__name__
        
        # GCP Pub/Sub設定
        project_id = os.getenv("GOOGLE_CLOUD_PROJECT")
        topic_id = os.getenv("PUBSUB_TOPIC_ID")
        
        if project_id and topic_id:
            try:
                self.publisher = pubsub_v1.PublisherClient()
                self.topic_path = self.publisher.topic_path(project_id, topic_id)
                logger.info(f"{self.action_type}: Pub/Sub Publisher initialized")
            except Exception as e:
                logger.error(f"{self.action_type}: Pub/Sub initialization failed: {e}")
                self.publisher = None
        else:
            self.publisher = None
            logger.info(f"{self.action_type}: Pub/Sub disabled (no PUBSUB_TOPIC_ID)")

    # ======================================================================
    # Pub/Sub送信処理（子クラスから呼び出される）
    # ======================================================================
    def publish_to_worker(self, team_id: str, event: dict) -> bool:
        """
        非同期処理のためにPub/Subへイベントを送信します。
        
        Args:
            team_id: ワークスペースID
            event: イベントデータ（dict）
            
        Returns:
            成功時True、失敗時False
        """
        if not self.publisher:
            logger.warning(f"{self.action_type}: Publisher not initialized, skipping async dispatch")
            return False

        payload = {
            "action_type": self.action_type,
            "team_id": team_id,
            "event": event
        }
        
        try:
            data = json.dumps(payload).encode("utf-8")
            future = self.publisher.publish(self.topic_path, data=data)
            message_id = future.result(timeout=2)  # 2秒でタイムアウト
            logger.info(f"{self.action_type}: Published to Pub/Sub (message_id={message_id})")
            return True
        except Exception as e:
            logger.error(f"{self.action_type}: Pub/Sub publish failed: {e}", exc_info=True)
            return False

    # ======================================================================
    # 抽象メソッド（子クラスで実装を強制）
    # ======================================================================
    @abstractmethod
    def handle_sync(self, app):
        """
        同期処理: Slackイベントを受け取り、3秒以内に応答を返す。
        
        - Slackイベントのack()処理
        - 軽量な処理（バリデーション、Pub/Subへの送信など）
        - app.event()、app.action()、app.shortcut()などを登録
        
        Args:
            app: Slack Bolt Appインスタンス
        """
        pass

    @abstractmethod
    def handle_async(self, team_id: str, event: dict):
        """
        非同期処理: Pub/Subから戻ってきた後の重い処理。
        
        - AI解析、DB操作、外部API呼び出しなど時間のかかる処理
        - Slackへの通知送信
        
        Args:
            team_id: ワークスペースID
            event: イベントデータ（dict）
        """
        pass