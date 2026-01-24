"""
システムイベントリスナー

このモジュールは、システム関連のSlackイベントを受け取ります。
- Bot参加
- ヘルスチェック

命名規則:
- on_xxx: Slackイベントを受け取る
"""
import logging

logger = logging.getLogger(__name__)


def register_system_listeners(app):
    """
    システムイベント関連のリスナーをSlack Appに登録します。
    
    Args:
        app: Slack Bolt Appインスタンス
    """

    # ==========================================
    # 1. Botがチャンネルに参加したとき
    # ==========================================
    @app.event("member_joined_channel")
    def on_bot_joined_channel(event, client, body):
        """
        Botがチャンネルに参加したときの処理。
        
        セットアップメッセージを投稿します。
        """
        from resources.templates.modals import create_setup_message_blocks
        
        try:
            # マルチテナント対応: team_id を取得
            team_id = body.get("team_id") or event.get("team")
            
            from resources.clients.slack_client import get_slack_client
            
            # team_id に基づいて WebClient を取得
            dynamic_client = get_slack_client(team_id)
            
            bot_user_id = dynamic_client.auth_test()["user_id"]
            if event.get("user") == bot_user_id:
                dynamic_client.chat_postMessage(
                    channel=event.get("channel"),
                    blocks=create_setup_message_blocks(),
                    text="勤怠管理ボットが参加しました。設定をお願いします。"
                )
                logger.info(f"Bot参加: Channel={event.get('channel')}, Workspace={team_id}")
        except Exception as e:
            logger.error(f"初期設定送信エラー: {e}", exc_info=True)
