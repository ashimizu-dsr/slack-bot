"""
Slack Client ラッパー

このモジュールは、Slack Web API呼び出しを集約し、
プロジェクト全体で統一したインターフェースを提供します。
"""
import logging
from typing import List, Dict, Any, Optional

logger = logging.getLogger(__name__)


class SlackClientWrapper:
    """
    Slack Web APIクライアントのラッパークラス。
    
    ユーザー情報の取得、メッセージ送信などの共通操作を提供します。
    """
    
    def __init__(self, client):
        """
        Args:
            client: slack_sdk.web.client.WebClient インスタンス
        """
        self.client = client
    
    def fetch_user_display_name(self, user_id: str) -> str:
        """
        ユーザーIDから表示名を取得します。
        
        Args:
            user_id: SlackユーザーID（<@U...>形式も自動クレンジング）
            
        Returns:
            表示名（優先順位: display_name > real_name > user_id）
            
        Note:
            メンション形式（<@U123|name>）が渡された場合も正しく処理されます。
        """
        try:
            # 1. メンション形式のクレンジング
            clean_user_id = user_id
            if user_id and isinstance(user_id, str):
                clean_user_id = user_id.replace("<@", "").replace(">", "").split("|")[0]
            
            # 2. Slack API呼び出し
            res = self.client.users_info(user=clean_user_id)
            if not res.get("ok"):
                logger.warning(f"Slack API response not OK for user {clean_user_id}")
                return clean_user_id

            user_data = res.get("user", {})
            profile = user_data.get("profile", {})

            # 優先順位: 1. display_name, 2. real_name, 3. user_id
            display_name = profile.get("display_name", "").strip()
            if display_name:
                return display_name
            
            real_name = profile.get("real_name", "").strip()
            if real_name:
                return real_name
            
            # どちらもない場合はuser_idをそのまま返す
            return clean_user_id
            
        except Exception as e:
            logger.error(f"ユーザー名取得失敗: {user_id}, {e}", exc_info=True)
            # エラー時も極力 @ 抜きを返す
            return user_id.replace("<@", "").replace(">", "").split("|")[0] if user_id else "Unknown"
    
    def fetch_user_name_map(self, user_ids: List[str]) -> Dict[str, str]:
        """
        複数のユーザーIDから表示名マップを作成します。
        
        Args:
            user_ids: SlackユーザーIDのリスト
            
        Returns:
            {user_id: display_name} の辞書
        """
        user_name_map = {}
        for uid in user_ids:
            try:
                name = self.fetch_user_display_name(uid)
                user_name_map[uid] = name
            except Exception as e:
                logger.warning(f"ユーザー名取得失敗: {uid}, Error: {e}")
                user_name_map[uid] = uid
        
        return user_name_map
    
    def fetch_bot_joined_channels(self) -> List[str]:
        """
        Botが参加しているチャンネルIDの一覧を取得します。
        
        Returns:
            チャンネルIDの配列
            
        Note:
            公開チャンネルおよびプライベートチャンネルを取得します。
            アーカイブされたチャンネルは除外されます。
        """
        try:
            response = self.client.conversations_list(
                types="public_channel,private_channel",
                exclude_archived=True
            )
            joined_channels = [
                c["id"] for c in response["channels"] 
                if c.get("is_member", False)
            ]
            logger.info(f"Bot参加チャンネル数: {len(joined_channels)}")
            return joined_channels
        except Exception as e:
            logger.error(f"チャンネル一覧取得失敗: {e}", exc_info=True)
            return []
    
    def send_message(
        self, 
        channel: str, 
        blocks: List[Dict[str, Any]], 
        text: str = None,
        thread_ts: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """
        メッセージを送信します。
        
        Args:
            channel: 送信先チャンネルID
            blocks: Block Kit ブロックの配列
            text: フォールバックテキスト（通知用）
            thread_ts: スレッドのタイムスタンプ（スレッド返信する場合）
            
        Returns:
            Slack APIのレスポンス（失敗時はNone）
        """
        try:
            response = self.client.chat_postMessage(
                channel=channel,
                blocks=blocks,
                text=text or "メッセージ",
                thread_ts=thread_ts
            )
            if response.get("ok"):
                return response
            else:
                logger.error(f"メッセージ送信失敗: {response.get('error')}")
                return None
        except Exception as e:
            logger.error(f"メッセージ送信エラー: {e}", exc_info=True)
            return None
    
    def update_message(
        self, 
        channel: str, 
        ts: str, 
        blocks: List[Dict[str, Any]], 
        text: str = None
    ) -> Optional[Dict[str, Any]]:
        """
        既存のメッセージを更新します。
        
        Args:
            channel: チャンネルID
            ts: メッセージのタイムスタンプ
            blocks: Block Kit ブロックの配列
            text: フォールバックテキスト
            
        Returns:
            Slack APIのレスポンス（失敗時はNone）
        """
        try:
            response = self.client.chat_update(
                channel=channel,
                ts=ts,
                blocks=blocks,
                text=text or "更新"
            )
            if response.get("ok"):
                return response
            else:
                logger.error(f"メッセージ更新失敗: {response.get('error')}")
                return None
        except Exception as e:
            logger.error(f"メッセージ更新エラー: {e}", exc_info=True)
            return None
    
    def send_ephemeral(
        self, 
        channel: str, 
        user: str, 
        text: str
    ) -> Optional[Dict[str, Any]]:
        """
        エフェメラルメッセージ（本人にのみ表示）を送信します。
        
        Args:
            channel: チャンネルID
            user: 対象ユーザーID
            text: メッセージテキスト
            
        Returns:
            Slack APIのレスポンス（失敗時はNone）
        """
        try:
            response = self.client.chat_postEphemeral(
                channel=channel,
                user=user,
                text=text
            )
            if response.get("ok"):
                return response
            else:
                logger.error(f"エフェメラルメッセージ送信失敗: {response.get('error')}")
                return None
        except Exception as e:
            logger.error(f"エフェメラルメッセージ送信エラー: {e}", exc_info=True)
            return None
