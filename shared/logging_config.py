"""
Logging configuration and utilities.

This module provides structured logging utilities used throughout
the application for consistent log formatting and levels.
"""

import logging
from typing import Optional


def setup_logging(level: int = logging.INFO) -> None:
    """Setup application-wide logging configuration."""
    logging.basicConfig(
        level=level,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )


def get_logger(name: str) -> logging.Logger:
    """Get a configured logger instance."""
    return logging.getLogger(name)


class AttendanceLogger:
    """Logger with structured attendance-specific methods."""

    def __init__(self, logger: logging.Logger):
        self.logger = logger

    def log_attendance_action(self, action: str, user_id: Optional[str] = None,
                            date: Optional[str] = None, status: Optional[str] = None,
                            level: int = logging.INFO) -> None:
        """Log attendance-related actions with structured data."""
        message = f"Attendance {action}"
        if user_id:
            message += f" - User: {user_id}"
        if date:
            message += f" - Date: {date}"
        if status:
            message += f" - Status: {status}"

        self.logger.log(level, message)

    def log_notification(self, type_: str, target: str, success: bool = True) -> None:
        """Log notification events."""
        status = "sent" if success else "failed"
        self.logger.info(f"Notification {status} - Type: {type_}, Target: {target}")

    def log_error(self, operation: str, error: Exception, user_id: Optional[str] = None) -> None:
        """Log errors with context."""
        user_context = f" (User: {user_id})" if user_id else ""
        self.logger.error(f"{operation} failed{user_context}: {error}", exc_info=True)