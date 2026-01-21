"""
カスタム例外クラスとエラーハンドリングユーティリティ

このモジュールは、アプリケーション全体で一貫したエラー処理を提供します。
全てのビジネスロジック層のエラーは、ここで定義された例外クラスを使用してください。
"""

from typing import Optional
import logging

logger = logging.getLogger(__name__)


class AttendanceBotError(Exception):
    """
    勤怠Botの基底例外クラス。
    
    全てのカスタム例外はこのクラスを継承します。
    user_messageを設定することで、エンドユーザーにわかりやすいメッセージを表示できます。
    """

    def __init__(self, message: str, user_message: Optional[str] = None):
        """
        Args:
            message: 開発者向けのエラーメッセージ（ログに記録）
            user_message: ユーザー向けのエラーメッセージ（UI表示用、省略時はmessageを使用）
        """
        super().__init__(message)
        self.user_message = user_message or message


class ValidationError(AttendanceBotError):
    """
    入力値の検証に失敗した場合に発生します。
    
    例: 必須フィールドの欠落、不正なステータス値、日付形式の誤りなど
    """
    pass


class DatabaseError(AttendanceBotError):
    """
    データベース操作に失敗した場合に発生します。
    
    例: Firestore書き込みエラー、ネットワーク障害など
    """
    pass


class SlackApiError(AttendanceBotError):
    """
    Slack API呼び出しに失敗した場合に発生します。
    
    例: 認証エラー、レート制限、無効なチャンネルIDなど
    """
    pass


class NotificationError(AttendanceBotError):
    """
    通知送信に失敗した場合に発生します。
    
    例: メッセージ投稿失敗、モーダル表示失敗など
    """
    pass


class ConcurrencyError(AttendanceBotError):
    """
    楽観的ロックの競合が発生した場合に発生します。
    
    例: メンバー設定を複数人が同時に更新しようとした場合
    """
    pass


class AuthorizationError(AttendanceBotError):
    """
    権限不足で操作が拒否された場合に発生します。
    
    例: 他人の勤怠記録を編集しようとした場合
    """
    pass


def handle_error(error: Exception, user_id: Optional[str] = None,
                logger_instance: Optional[logging.Logger] = None) -> str:
    """
    例外をハンドリングし、ユーザー向けのエラーメッセージを返します。
    
    Args:
        error: 発生した例外
        user_id: エラーが発生したユーザーのID（ログ記録用）
        logger_instance: ロガーインスタンス（省略時はモジュールのロガーを使用）
        
    Returns:
        ユーザーに表示すべきエラーメッセージ
        
    Note:
        AttendanceBotErrorの派生クラスの場合はuser_messageを返し、
        それ以外の予期しないエラーの場合は汎用メッセージを返します。
    """
    log = logger_instance or logger
    
    if isinstance(error, AttendanceBotError):
        log.warning(f"Business error: {error} (user: {user_id})")
        return error.user_message
    else:
        log.error(f"Unexpected error: {error} (user: {user_id})", exc_info=True)
        return "⚠️ 内部エラーが発生しました。管理者にお知らせください。"


def get_error_response(error: Exception) -> dict:
    """
    例外をSlackモーダルのエラーレスポンスに変換します。
    
    Args:
        error: 変換する例外
        
    Returns:
        Slackモーダルのエラーレスポンス辞書
        
    Example:
        >>> try:
        ...     raise ValidationError("Invalid date", "日付形式が正しくありません")
        ... except Exception as e:
        ...     return ack(**get_error_response(e))
    """
    user_message = handle_error(error)
    return {
        "response_action": "errors",
        "errors": {
            "general": user_message
        }
    }


def get_ephemeral_error_message(error: Exception) -> str:
    """
    例外を一時メッセージ用のテキストに変換します。
    
    Args:
        error: 変換する例外
        
    Returns:
        chat_postEphemeralで使用するメッセージテキスト
    """
    return handle_error(error)