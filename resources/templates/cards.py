"""
勤怠通知カードのBlock Kit構築モジュール

このモジュールは、勤怠記録の通知カード（メッセージカード）のUI構造を担当します。
名前の解決はサービス層で行い、このモジュールでは整形済みの表示名を受け取ります。
"""
from typing import Any, Dict, List
from resources.constants import STATUS_TRANSLATION


def build_attendance_card(
    record: Any,
    display_name: str,
    is_update: bool = False,
    show_buttons: bool = True
) -> List[Dict[str, Any]]:
    """
    勤怠記録カード（通知用）のBlock Kitブロックを生成します。
    
    Args:
        record: AttendanceRecordオブジェクトまたは辞書
        display_name: 整形済みのユーザー表示名（Service層から受け取る）
        is_update: 更新通知の場合True
        show_buttons: ボタン（修正・取消）を表示する場合True
        
    Returns:
        Slack Block Kitブロックの配列
        
    Note:
        このメソッドは名前解決を行いません。
        display_name引数で受け取った値をそのまま使用します。
    """
    def get_val(obj, key):
        """オブジェクトまたは辞書から値を取得する内部関数"""
        return obj.get(key) if isinstance(obj, dict) else getattr(obj, key, None)

    date_val = get_val(record, 'date')
    status_jp = STATUS_TRANSLATION.get(get_val(record, 'status'), get_val(record, 'status'))
    note_val = get_val(record, 'note')

    label = "を修正しました" if is_update else "を記録しました"

    blocks = [
        {
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": f"ⓘ {display_name} さんの勤怠連絡{label}"}]
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"* {date_val} [ {status_jp} ]*{f'\n  {note_val}' if note_val else ''}"}
        }
    ]

    # show_buttons = True  # 二次開発時にこちらに戻す
    show_buttons = False
    
    if show_buttons:
        blocks.append({
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "修正"},
                    "action_id": "open_update_attendance",
                    "value": str(date_val)
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "取消"},
                    "action_id": "delete_attendance_request",
                    "value": str(date_val)
                }
            ]
        })
    
    return blocks


def build_delete_notification(display_name: str, date: str) -> List[Dict[str, Any]]:
    """
    削除通知用のシンプルなブロックを生成します。
    
    Args:
        display_name: 整形済みのユーザー表示名
        date: 対象日付（YYYY-MM-DD形式）
        
    Returns:
        Slack Block Kitブロックの配列
    """
    return [{
        "type": "context",
        "elements": [{
            "type": "mrkdwn",
            "text": f"ⓘ {display_name} さんの {date} の勤怠連絡を取り消しました"
        }]
    }]
