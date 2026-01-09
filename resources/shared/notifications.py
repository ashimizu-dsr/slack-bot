"""
Notification configuration and options.

This module defines notification options and configurations
used throughout the application.
"""

from dataclasses import dataclass
from typing import Optional


@dataclass
class NotificationOptions:
    """Options for notification behavior."""
    channel: bool = True
    dm: bool = True
    include_actions: bool = True

    def __post_init__(self):
        # At least one notification method must be enabled
        if not self.channel and not self.dm:
            raise ValueError("At least one of channel or dm must be True")


# Default notification options for different scenarios
ATTENDANCE_NEW_OPTIONS = NotificationOptions(channel=True, dm=True, include_actions=True)
ATTENDANCE_UPDATE_OPTIONS = NotificationOptions(channel=True, dm=True, include_actions=True)
ATTENDANCE_DELETE_OPTIONS = NotificationOptions(channel=True, dm=True, include_actions=False)