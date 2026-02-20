"""
Slack Client ラッパー

このモジュールは、Slack Web API呼び出しを集約し、
プロジェクト全体で統一したインターフェースを提供します。
"""
import logging
from typing import List, Dict, Any, Optional
from slack_sdk import WebClient

logger = logging.getLogger(__name__)


def get_slack_client(team_id: str) -> WebClient:
    """
    team_id に基づいて Slack WebClient を生成します。
    
    Args:
        team_id: Slackワークスペースの一意ID
        
    Returns:
        そのワークスペース用の WebClient インスタンス
        
    Raises:
        ValueError: ワークスペース設定が見つからない、またはトークンが無効な場合
        
    Note:
        内部で Firestore の workspaces コレクションから bot_token を取得します。
        マルチテナント対応の中核となる関数です。
    """
    from resources.shared.db import get_workspace_config
    
    workspace_config = get_workspace_config(team_id)
    
    if not workspace_config:
        logger.error(f"ワークスペース設定が見つかりません: team_id={team_id}")
        raise ValueError(f"Workspace configuration not found for team_id: {team_id}")
    
    bot_token = workspace_config.get("bot_token")
    
    if not bot_token:
        logger.error(f"bot_token が設定されていません: team_id={team_id}")
        raise ValueError(f"bot_token not configured for team_id: {team_id}")
    
    logger.info(f"Slack WebClient を生成しました: team_id={team_id}")
    return WebClient(token=bot_token)


def fetch_message_in_channel(
    client: WebClient, channel: str, ts: str
) -> Optional[Dict[str, Any]]:
    """
    チャンネル内の指定 ts のメッセージを1件取得します。
    conversations.history を使用（スレッドの親メッセージ取得に利用）。

    Args:
        client: Slack WebClient
        channel: チャンネルID
        ts: メッセージのタイムスタンプ（例: thread_ts）

    Returns:
        メッセージ辞書（text 等）。取得失敗時は None
    """
    try:
        res = client.conversations_history(
            channel=channel, latest=ts, limit=1, inclusive=True
        )
        if not res.get("ok") or not res.get("messages"):
            return None
        return res["messages"][0]
    except Exception as e:
        logger.error(
            f"メッセージ取得失敗: channel={channel}, ts={ts}, {e}", exc_info=True
        )
        return None


def fetch_workspace_user_list(client: WebClient) -> List[Dict[str, Any]]:
    """
    ワークスペースの全ユーザーを users.list で取得し、
    user_id, email, real_name, display_name のリストを返します。
    Bot・削除済みユーザーは除外します。

    Args:
        client: Slack WebClient（bot_token で生成されたもの）

    Returns:
        [{ user_id, email, real_name, display_name }, ...]
    """
    result: List[Dict[str, Any]] = []
    cursor: Optional[str] = None
    try:
        while True:
            resp = client.users_list(limit=200, cursor=cursor)
            if not resp.get("ok"):
                logger.error(f"users.list error: {resp.get('error')}")
                break
            for member in resp.get("members", []):
                if member.get("is_bot") or member.get("deleted"):
                    continue
                uid = member.get("id")
                if not uid:
                    continue
                profile = member.get("profile") or {}
                result.append({
                    "user_id": uid,
                    "email": (profile.get("email") or "").strip(),
                    "real_name": (member.get("real_name") or "").strip(),
                    "display_name": (profile.get("display_name") or "").strip(),
                })
            cursor = (resp.get("response_metadata") or {}).get("next_cursor")
            if not cursor:
                break
        logger.info(f"fetch_workspace_user_list: {len(result)} users")
        return result
    except Exception as e:
        logger.error(f"fetch_workspace_user_list failed: {e}", exc_info=True)
        return result


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
    
    def fetch_user_display_name(self, user_id: str) -> Optional[str]:
        """
        ユーザーIDから表示名を取得します。
        
        Args:
            user_id: SlackユーザーID（<@U...>形式も自動クレンジング）
            
        Returns:
            表示名（優先順位: display_name > real_name > user_id）。
            user_not_found 等で取得失敗時は None を返す（他ユーザーを返さない）。
            
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
                err = res.get("error", "")
                logger.warning(f"Slack API response not OK for user {clean_user_id}, error={err}")
                if err == "user_not_found":
                    return None
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
            return None
    
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
                user_name_map[uid] = name if name is not None else uid
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
            users.conversations APIを使用してBotが参加しているチャンネルのみを取得します。
            アーカイブされたチャンネルは除外されます。
        """
        try:
            # users.conversations は Bot が実際に参加しているチャンネルのみを返す
            channels = []
            cursor = None
            
            while True:
                response = self.client.users_conversations(
                    types="public_channel", # private_channelは除外
                    exclude_archived=True,
                    limit=200,
                    cursor=cursor
                )
                
                if not response.get("ok"):
                    logger.error(f"チャンネル一覧取得エラー: {response.get('error')}")
                    break
                
                channels.extend([c["id"] for c in response.get("channels", [])])
                
                # ページネーション処理
                cursor = response.get("response_metadata", {}).get("next_cursor")
                if not cursor:
                    break
            
            logger.info(f"Bot参加チャンネル数: {len(channels)}")
            return channels
            
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
