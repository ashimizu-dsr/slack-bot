"""
Handlers package - Slack event and interaction handlers
マルチワークスペース（OAuth/配布）対応版
"""

import sys
import os

# Add project root to path for absolute imports
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from .slack_handlers import register_slack_handlers
from .action_handlers import register_action_handlers
from .modal_handlers import register_modal_handlers, register_modal_handlers_v22
# from .command_handlers import register_command_handlers
# from .shortcut_handlers import register_shortcut_handlers

def register_all_handlers(app, attendance_service, notification_service):
    """
    全てのハンドラーを登録します。
    マルチワークスペース対応のため、各ハンドラー内では 
    引数から渡される context や client を優先的に使用します。
    """
    
    # 1. 勤怠提出（ボタンクリック等）
    register_action_handlers(app, attendance_service, notification_service)
    
    # 2. モーダル操作（勤怠編集・保存、設定保存）
    register_modal_handlers(app, attendance_service, notification_service)
    
    # 2-1. v2.22: レポート設定モーダル（新規）
    register_modal_handlers_v22(app)
    
    # 3. スラッシュコマンド
    # register_command_handlers(app)
    
    # 4. ショートカット（履歴確認、メンバー設定）
    # register_shortcut_handlers(app, attendance_service)
    
    # 5. その他イベント（アプリのホーム画面表示、メッセージ受信など）
    register_slack_handlers(app, attendance_service, notification_service)

    # 【重要】ミドルウェアや各ハンドラーで context.client を使うよう設計されているため、
    # ここでの登録順序に依存関係はありませんが、構造を整理しました。