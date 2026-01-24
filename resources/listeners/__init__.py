"""
Listeners package - Slack event receivers

このパッケージは、Slackからのイベントを受け取るリスナーを提供します。
各リスナーは責務ごとに分離されています。
"""

from .attendance_listener import register_attendance_listeners
from .system_listener import register_system_listeners
from .admin_listener import register_admin_listeners


def register_all_listeners(app, attendance_service, notification_service, dispatcher=None):
    """
    全てのリスナーを登録します。
    
    Args:
        app: Slack Bolt Appインスタンス
        attendance_service: AttendanceServiceインスタンス
        notification_service: NotificationServiceインスタンス
        dispatcher: InteractionDispatcherインスタンス（Pub/Sub非同期処理用、オプション）
    """
    # 1. 勤怠操作（投稿・修正・削除）
    register_attendance_listeners(app, attendance_service, notification_service, dispatcher)
    
    # 2. システムイベント（Bot参加など）
    register_system_listeners(app)
    
    # 3. 管理機能（レポート設定、グループ管理）
    register_admin_listeners(app)
