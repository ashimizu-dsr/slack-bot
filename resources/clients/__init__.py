"""
Clients package - External service clients

このパッケージは、外部サービス（Slack API、データベースなど）への
接続を管理するクライアントを提供します。
"""

from .slack_client import SlackClientWrapper

__all__ = [
    'SlackClientWrapper',
]
