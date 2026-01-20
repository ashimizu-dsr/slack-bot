"""
Modal & Message Views - Slack UIの構築に専念する
"""
import datetime
import json
from typing import Dict, Any, Optional, List
from constants import STATUS_TRANSLATION, SECTION_TRANSLATION

# ==========================================
# 1. 勤怠入力/編集モーダル
# ==========================================
def create_attendance_modal_view(initial_data: Optional[Dict] = None, **kwargs) -> Dict[str, Any]:
    is_fixed_date = kwargs.get("is_fixed_date", False)
    
    today = datetime.date.today().isoformat() 
    initial_date = initial_data.get('date', today) if initial_data else today
    initial_status = initial_data.get('status') if initial_data else None
    initial_note = initial_data.get('note', '') if initial_data else ''

    blocks = []

    if is_fixed_date:
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*日付*\n{initial_date}"}
        })
    else:
        blocks.append({
            "type": "input",
            "block_id": "date_block",
            "element": {
                "type": "datepicker",
                "action_id": "attendance_date_change",
                "initial_date": initial_date 
            },
            "label": {"type": "plain_text", "text": "日付"}
        })

    status_options = [
        {"text": {"type": "plain_text", "text": display}, "value": val}
        for val, display in STATUS_TRANSLATION.items()
    ]
    initial_status_option = next((opt for opt in status_options if opt['value'] == initial_status), None)

    blocks.extend([
        {
            "type": "input",
            "block_id": "status_block",
            "element": {
                "type": "static_select",
                "action_id": "status_select",
                "placeholder": {"type": "plain_text", "text": "区分を選択"},
                "options": status_options,
                **({"initial_option": initial_status_option} if initial_status_option else {})
            },
            "label": {"type": "plain_text", "text": "区分"}
        },
        {
            "type": "input",
            "block_id": "note_block",
            "element": {
                "type": "plain_text_input",
                "action_id": "note_input",
                "multiline": True,
                "initial_value": initial_note,
                "placeholder": {"type": "plain_text", "text": "例）私用のため10:00頃出社します。"}
            },
            "label": {"type": "plain_text", "text": "備考"},
            "optional": True
        },
        {"type": "divider"},
    ])

    return {
        "type": "modal",
        "callback_id": "attendance_submit", 
        "private_metadata": json.dumps({
            "is_edit": initial_data is not None, 
            "date": initial_date 
        }),
        "title": {"type": "plain_text", "text": "勤怠連絡の修正"},
        "submit": {"type": "plain_text", "text": "保存"},
        "close": {"type": "plain_text", "text": "キャンセル"},
        "blocks": blocks
    }

# ==========================================
# 2. 履歴表示モーダル
# ==========================================
def create_history_modal_view(history_records: List[Dict], selected_year: str, selected_month: str, user_id: str) -> Dict[str, Any]:
    """
    不具合①対策: private_metadata に user_id を追加。
    これにより年月変更時に「誰の履歴を再取得するか」をプログラムが判別可能になります。
    """
    year_options = [{"text": {"type": "plain_text", "text": f"{y}年"}, "value": str(y)} for y in range(2025, 2036)]
    month_options = [{"text": {"type": "plain_text", "text": f"{m}月"}, "value": f"{m:02d}"} for m in range(1, 13)]

    blocks = [
        {
            "type": "actions",
            "block_id": "history_filter",
            "elements": [
                {"type": "static_select", "action_id": "history_year_change", "initial_option": next((o for o in year_options if o["value"] == selected_year), year_options[0]), "options": year_options},
                {"type": "static_select", "action_id": "history_month_change", "initial_option": next((o for o in month_options if o["value"] == selected_month), month_options[0]), "options": month_options}
            ]
        },
        {"type": "divider"}
    ]

    if not history_records:
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": "_記録がありません_"}})
    else:
        # 新しい順にソート
        sorted_records = sorted(history_records, key=lambda x: x['date'], reverse=True)
        for rec in sorted_records:
            status_jp = STATUS_TRANSLATION.get(rec['status'], rec['status'])
            blocks.append({
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"{rec['date']} │ {status_jp}"}
            })
            if rec.get('note'):
                blocks.append({
                    "type": "context",
                    "elements": [{"type": "mrkdwn", "text": f"  {rec['note']}"}]
                })
            blocks.append({"type": "divider"})

    return {
        "type": "modal",
        "callback_id": "history_view",
        "private_metadata": json.dumps({"target_user_id": user_id}), # 重要：月切り替えで使用
        "title": {"type": "plain_text", "text": "勤怠連絡一覧"},
        "close": {"type": "plain_text", "text": "閉じる"},
        "blocks": blocks
    }

# ==========================================
# 3. メンバー一括設定モーダル
# ==========================================
def create_member_settings_modal_view(channel_id: str, **kwargs) -> Dict[str, Any]:
    from resources.shared.db import get_channel_members_with_section
    
    result = get_channel_members_with_section()
    current_members_by_section = result[0] if isinstance(result, tuple) else (result or {})

    all_section_ids = ["sec_1", "sec_2", "sec_3", "sec_4", "sec_5", "sec_6", "sec_7", "sec_finance"]

    blocks = []
    for sec_id in all_section_ids:
        sec_name = SECTION_TRANSLATION.get(sec_id, sec_id)
        members_in_this_sec = current_members_by_section.get(sec_id, [])

        blocks.append({
            "type": "input",
            "block_id": f"user_select_block_{sec_id}",
            "label": {"type": "plain_text", "text": sec_name},
            "optional": True,
            "element": {
                "type": "multi_users_select",
                "action_id": "user_select",
                "placeholder": {"type": "plain_text", "text": "人員を選択"},
                "initial_users": members_in_this_sec if members_in_this_sec else []
            }
        })

    return {
        "type": "modal",
        "callback_id": "member_settings_submit",
        "title": {"type": "plain_text", "text": "設定"},
        "submit": {"type": "plain_text", "text": "保存"},
        "close": {"type": "plain_text", "text": "キャンセル"},
        "blocks": blocks,
        "private_metadata": json.dumps({"channel_id": channel_id})
    }

# ==========================================
# 4. レポート & セットアップ
# ==========================================
def build_daily_report_blocks(header: str, section_data: dict):
    blocks = [
        {"type": "header", "text": {"type": "plain_text", "text": header}},
        {"type": "divider"}
    ]

    for section_name, records in section_data.items():
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": f"*{section_name}*"}})
        if not records:
            blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": "連絡なし"}]})
        else:
            member_lines = []
            for r in records:
                status_jp = STATUS_TRANSLATION.get(r.get("status"), r.get("status"))
                line = f"• <@{r['user_id']}> - {status_jp}"
                if r.get("note"):
                    line += f" ({r['note']})"
                member_lines.append(line)

            blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": "\n".join(member_lines)}]})
        blocks.append({"type": "divider"})
    return blocks

def create_setup_message_blocks():
    """管理開始時のセットアップ用メッセージ"""
    return [
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": "ⓘ 勤怠連絡の管理を開始します。下のボタンより各課のメンバー設定をお願いします。"
                }
            ]
        },
        {
            "type": "actions",
            "block_id": "setup_actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "設定"},
                    "action_id": "open_member_settings",
                }
            ]
        }
    ]

def create_attendance_card_blocks(record: Any, **kwargs) -> List[Dict[str, Any]]:
    def get_val(obj, key):
        return obj.get(key) if isinstance(obj, dict) else getattr(obj, key, None)

    user_id = get_val(record, 'user_id')
    date_val = get_val(record, 'date')
    status_jp = STATUS_TRANSLATION.get(get_val(record, 'status'), get_val(record, 'status'))
    note_val = get_val(record, 'note')

    label = "を修正しました" if kwargs.get("is_update") else "を記録しました"
    
    blocks = [
        {
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": f"ⓘ <@{user_id}> さんの勤怠連絡{label}"}]
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"* {date_val} [ {status_jp} ]*{f'\n  {note_val}' if note_val else ''}"}
        }
    ]
    
    if kwargs.get("show_buttons", True):
        blocks.append({
            "type": "actions",
            "elements": [
                {"type": "button", "text": {"type": "plain_text", "text": "修正"}, "action_id": "open_update_attendance", "value": str(date_val)},
                {"type": "button", "text": {"type": "plain_text", "text": "取消"}, "action_id": "delete_attendance_request", "value": str(date_val)}
            ]
        })
    return blocks

# ==========================================
# 5. エラー & 削除確認
# ==========================================
def create_delete_confirm_modal(date: str):
    return {
        "type": "modal",
        "callback_id": "delete_attendance_confirm_callback",
        "private_metadata": date,
        "title": {"type": "plain_text", "text": "勤怠の削除"},
        "submit": {"type": "plain_text", "text": "削除する"},
        "close": {"type": "plain_text", "text": "キャンセル"},
        "blocks": [{"type": "section", "text": {"type": "mrkdwn", "text": f"*{date}* の勤怠連絡を削除してもよろしいですか？"}}]
    }

def create_error_modal(title: str, message: str):
    """不具合④対策: エラー通知用汎用モーダル"""
    return {
        "type": "modal",
        "title": {"type": "plain_text", "text": title},
        "close": {"type": "plain_text", "text": "閉じる"},
        "blocks": [{"type": "section", "text": {"type": "mrkdwn", "text": message}}]
    }