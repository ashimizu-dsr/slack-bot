"""
システムイベントリスナー (Pub/Sub対応版)

このモジュールは、システム関連のSlackイベントを受け取ります。
- Bot参加
- ヘルスチェック

Pub/Sub対応:
- handle_sync(): Slackイベントを受け取り、必要に応じてPub/Subに投げる（3秒以内）
- handle_async(): Pub/Subから戻ってきた後の重い処理
"""
# import logging

# from resources.listeners.Listener import Listener
# from resources.templates.modals import create_setup_message_blocks
# from resources.clients.slack_client import get_slack_client

# logger = logging.getLogger(__name__)


# class SystemListener(Listener):
#     """システムイベントリスナークラス"""
    
#     def __init__(self):
#         """SystemListenerを初期化します"""
#         super().__init__()

#     # ======================================================================
#     # 同期処理: Slackイベントの受付（3秒以内に返す）
#     # ======================================================================
#     def handle_sync(self, app):
#         """
#         Slackイベントを受け取る処理を登録します。
        
#         システムイベントは基本的に軽量な操作なので、
#         全て同期的に実行します。
        
#         Args:
#             app: Slack Bolt Appインスタンス
#         """
        
#         # ==========================================
#         # 1. Botがチャンネルに参加したとき
#         # ==========================================
#         @app.event("member_joined_channel")
#         def on_bot_joined_channel(event, body):
#             """
#             Botがチャンネルに参加したときの処理。
            
#             セットアップメッセージを投稿します。
#             """
#             try:
#                 # マルチテナント対応: team_id を取得
#                 team_id = body.get("team_id") or event.get("team")
                
#                 # team_id に基づいて WebClient を取得
#                 dynamic_client = get_slack_client(team_id)
                
#                 bot_user_id = dynamic_client.auth_test()["user_id"]
#                 if event.get("user") == bot_user_id:
#                     dynamic_client.chat_postMessage(
#                         channel=event.get("channel"),
#                         blocks=create_setup_message_blocks(),
#                         text="勤怠管理ボットが参加しました。設定をお願いします。"
#                     )
#                     logger.info(f"Bot参加: Channel={event.get('channel')}, Workspace={team_id}")
#             except Exception as e:
#                 logger.error(f"初期設定送信エラー: {e}", exc_info=True)

#     # ======================================================================
#     # 非同期処理: Pub/Subから戻ってきた後の重い処理
#     # ======================================================================
#     def handle_async(self, team_id: str, event: dict):
#         """
#         Pub/Subから戻ってきた後の重い処理を実行します。
        
#         システムイベントは基本的に軽量な処理なので、通常は非同期処理は不要です。
#         将来的に重い処理が必要になった場合にここに実装します。
        
#         Args:
#             team_id: ワークスペースID
#             event: イベントデータ
#         """
#         logger.info(f"SystemListener.handle_async called (no operation): team_id={team_id}")
#         pass
