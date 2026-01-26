"""
Listeners package - Slack event receivers

このパッケージは、Slackからのイベントを受け取るリスナーを提供します。
各リスナーは責務ごとに分離され、Pub/Sub対応で非同期処理を実現しています。
"""

from resources.listeners.attendance_listener import AttendanceListener
# from resources.listeners.system_listener import SystemListener
from resources.listeners.admin_listener import AdminListener


def register_all_listeners(app, attendance_service):
    """
    全てのリスナーを登録します。
    
    Args:
        app: Slack Bolt Appインスタンス
        attendance_service: AttendanceServiceインスタンス
    
    Returns:
        dict: action_type -> Listenerインスタンスのマッピング
    """
    # 1. 勤怠操作（投稿・修正・削除）
    attendance_listener = AttendanceListener(attendance_service)
    attendance_listener.handle_sync(app)
    
    # # 2. システムイベント（Bot参加など）
    # system_listener = SystemListener()
    # system_listener.handle_sync(app)
    
    # 3. 管理機能（レポート設定、グループ管理）
    admin_listener = AdminListener()
    admin_listener.handle_sync(app)
    
    # Pub/Sub処理用のマッピングを返す
    listener_map = {
        "AttendanceListener": attendance_listener,
        # "SystemListener": system_listener,
        "AdminListener": admin_listener
    }
    
    return listener_map
