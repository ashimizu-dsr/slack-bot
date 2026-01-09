"""
Custom exception classes and error handling utilities.

This module defines application-specific exceptions and provides
utilities for consistent error handling and user notification.
"""

from typing import Optional


class AttendanceBotError(Exception):
    """Base exception for attendance bot errors."""

    def __init__(self, message: str, user_message: Optional[str] = None):
        super().__init__(message)
        self.user_message = user_message or message


class ValidationError(AttendanceBotError):
    """Raised when input validation fails."""
    pass


class DatabaseError(AttendanceBotError):
    """Raised when database operations fail."""
    pass


class SlackApiError(AttendanceBotError):
    """Raised when Slack API calls fail."""
    pass


class NotificationError(AttendanceBotError):
    """Raised when notification sending fails."""
    pass


def handle_error(error: Exception, user_id: Optional[str] = None,
                logger=None) -> Optional[str]:
    """
    Handle exceptions and return user-friendly error messages.

    Args:
        error: The exception that occurred
        user_id: Optional user ID for context
        logger: Optional logger instance

    Returns:
        User-friendly error message, or None for internal errors
    """
    if isinstance(error, AttendanceBotError):
        if logger:
            logger.warning(f"Business error: {error} (user: {user_id})")
        return error.user_message
    else:
        if logger:
            logger.error(f"Unexpected error: {error} (user: {user_id})", exc_info=True)
        return "⚠️ 内部エラーが発生しました。管理者にお知らせください。"


def get_error_response(error: Exception) -> dict:
    """
    Convert an exception to a Slack modal error response.

    Args:
        error: The exception to convert

    Returns:
        Dict containing error response for Slack modals
    """
    user_message = handle_error(error)
    return {
        "response_action": "errors",
        "errors": {
            "general": user_message
        }
    }