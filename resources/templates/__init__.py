"""
Templates package - Slack Block Kit UI components

このパッケージは、SlackのBlock Kit（UI構造）を生成するモジュールを提供します。
"""

from .cards import (
    build_attendance_card,
    build_delete_notification
)

from .modals import (
    build_attendance_modal,
    build_history_modal,
    build_delete_confirm_modal,
    build_admin_settings_modal,
    build_add_group_modal,
    build_edit_group_modal,
    build_member_delete_confirm_modal,
    build_setup_message,
    # 後方互換性のためのエイリアス
    create_attendance_modal_view,
    create_history_modal_view,
    create_attendance_delete_confirm_modal,
    create_admin_settings_modal,
    create_add_group_modal,
    create_edit_group_modal,
    create_member_delete_confirm_modal,
    create_setup_message_blocks
)

__all__ = [
    # カード
    'build_attendance_card',
    'build_delete_notification',
    # モーダル
    'build_attendance_modal',
    'build_history_modal',
    'build_delete_confirm_modal',
    'build_admin_settings_modal',
    'build_add_group_modal',
    'build_edit_group_modal',
    'build_member_delete_confirm_modal',
    'build_setup_message',
    # 後方互換性のためのエイリアス
    'create_attendance_modal_view',
    'create_history_modal_view',
    'create_attendance_delete_confirm_modal',
    'create_admin_settings_modal',
    'create_add_group_modal',
    'create_edit_group_modal',
    'create_member_delete_confirm_modal',
    'create_setup_message_blocks',
]
