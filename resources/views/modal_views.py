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
    # 引数 kwargs から is_fixed_date を取得（修正ボタン経由なら True が入る）
    is_fixed_date = kwargs.get("is_fixed_date", False)
    
    today = datetime.date.today().isoformat() 
    initial_date = initial_data.get('date', today) if initial_data else today
    initial_status = initial_data.get('status') if initial_data else None
    initial_note = initial_data.get('note', '') if initial_data else ''

    blocks = []

    # --- 日付部分のロジック ---
    if is_fixed_date:
        # 修正の場合：日付をテキストとして表示（変更不可）
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*日付*\n{initial_date}"}
        })
    else:
        # 新規の場合：日付を選択可能にする
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

    # --- 区分・備考（ここは共通） ---
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
            "date": initial_date # 変更不可の場合もこの日付が保存処理に渡る
        }),
        "title": {"type": "plain_text", "text": "勤怠連絡の修正"},
        "submit": {"type": "plain_text", "text": "保存"},
        "close": {"type": "plain_text", "text": "キャンセル"},
        "blocks": blocks
    }

# ==========================================
# 2. 履歴表示モーダル
# ==========================================
def create_history_modal_view(history_records: List[Dict], selected_year: str, selected_month: str) -> Dict[str, Any]:
    # 年度設定などはそのまま
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
        for rec in history_records:
            status_jp = STATUS_TRANSLATION.get(rec['status'], rec['status'])
            
            # 1. メイン行：日付と区分（通常の太さ）
            blocks.append({
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"{rec['date']} │ {status_jp}"}
            })
            
            # 2. 備考行：ここを context ブロックにすることで「小文字・グレー」になります
            if rec.get('note'):
                blocks.append({
                    "type": "context",
                    "elements": [
                        {
                            "type": "mrkdwn", 
                            "text": f"  {rec['note']}" # 先頭にスペースを入れてインデント
                        }
                    ]
                })
            
            # 3. 区切り線
            blocks.append({"type": "divider"})

    return {
        "type": "modal",
        "callback_id": "history_view",
        "title": {"type": "plain_text", "text": "勤怠連絡一覧"},
        "close": {"type": "plain_text", "text": "閉じる"},
        "blocks": blocks
    }

# ==========================================
# 3. メンバー一括設定モーダル
# ==========================================
def create_member_settings_modal_view(channel_id: str, **kwargs) -> Dict[str, Any]:
    # **kwargs により、以前の引数 initial_members_by_section 等が来ても無視して動作します
    from shared.db import get_channel_members_with_section
    
    # --- 【ここを修正】 ---
    # DBの戻り値が (辞書, バージョン) のタプルなので、[0]番目だけを取り出す
    result = get_channel_members_with_section()
    if isinstance(result, tuple):
        current_members_by_section = result[0]
    else:
        current_members_by_section = result or {}
    # ----------------------

    all_section_ids = ["sec_1", "sec_2", "sec_3", "sec_4", "sec_5", "sec_6", "sec_7", "sec_finance"]

    blocks = []
    for sec_id in all_section_ids:
        sec_name = SECTION_TRANSLATION.get(sec_id, sec_id)
        # これで current_members_by_section が辞書になり、.get() が動くようになります
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
    """
    勤怠レポートのSlackブロックを生成する
    """
    # ヘッダーの整形（呼び出し元が「2026-01-09 勤怠集計レポート」と送ってきても変換する）
    clean_header = header

    blocks = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": clean_header
            }
        },
        {"type": "divider"}
    ]

    for section_name, records in section_data.items():
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*{section_name}*"}
        })

        if not records:
            blocks.append({
                "type": "context",
                "elements": [
                    {"type": "mrkdwn", "text": "連絡なし"} # 小文字相当
                ]
            })
        else:
            member_lines = []
            for r in records:
                user_id = r.get("user_id")
                
                # 2. 【ここが修正ポイント】翻訳辞書を使って日本語に変換する
                raw_status = r.get("status", "other")
                status_jp = STATUS_TRANSLATION.get(raw_status, raw_status)
                
                note = r.get("note", "")
                
                # 3. 変換した status_jp を使って組み立てる
                line = f"• <@{user_id}> - {status_jp}"
                if note:
                    line += f" ({note})"
                member_lines.append(line)

            blocks.append({
                "type": "context",
                "elements": [
                    {"type": "mrkdwn", "text": "\n".join(member_lines)}
                ]
            })
        
        blocks.append({"type": "divider"})

    return blocks

def create_setup_message_blocks():
    # ご指定の内容に修正（小文字・設定ボタン）
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

def create_attendance_card_blocks(record: Any, message_text: str = "", options: Optional[List[Dict]] = None, **kwargs) -> List[Dict[str, Any]]:
    """
    ヘッダーをcontextブロックで小文字化し、内容をsectionで表示します。
    常に「修正」「取消」ボタンを含めて返却します。
    """
    def get_val(obj, key):
        if isinstance(obj, dict): return obj.get(key)
        return getattr(obj, key, None)

    status_raw = get_val(record, 'status')
    user_id = get_val(record, 'user_id')
    date_val = get_val(record, 'date')
    note_val = get_val(record, 'note')

    # kwargsからフラグを取得（NotificationServiceから渡される想定）
    is_update = kwargs.get("is_update", False)
    # ボタンを表示するかどうかの制御（デフォルトTrue）
    show_buttons = kwargs.get("show_buttons", True)

    status_jp = STATUS_TRANSLATION.get(status_raw, status_raw)
    
    # 修正時はヘッダーのテキストを少し変える
    label = "を修正しました" if is_update else "を記録しました"
    header_text = f"ⓘ <@{user_id}> さんの勤怠連絡{label}"
    
    body_date = f"* {date_val} [ {status_jp} ]*"
    
    body_note = ""
    if note_val:
        indented_note = "\n".join([f"  {line}" for line in note_val.split("\n")])
        body_note = f"\n{indented_note}"

    blocks = [
        {
            "type": "context",
            "elements": [
                {"type": "mrkdwn", "text": header_text}
            ]
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn", 
                "text": f"{body_date}{body_note}"
            }
        }
    ]
    
    # --- ボタンブロックの追加 ---
    if show_buttons:
        # optionsが渡されていない場合は、標準の「修正・取消」ボタンを作成
        if not options:
            options = [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "修正"},
                    "action_id": "open_update_attendance",
                    "value": str(date_val)  # 日付を渡す
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "取消"},
                    "style": "danger",
                    "action_id": "delete_attendance_request",
                    "value": str(date_val) # 日付を渡す
                }
            ]
        
        blocks.append({"type": "actions", "elements": options})
        
    return blocks

def create_delete_confirm_modal(date: str):
    """勤怠削除の最終確認モーダル"""
    return {
        "type": "modal",
        "callback_id": "delete_attendance_confirm_callback",
        "private_metadata": date,  # 削除対象の日付を渡す
        "title": {"type": "plain_text", "text": "勤怠の削除"},
        "submit": {"type": "plain_text", "text": "削除する"},
        "close": {"type": "plain_text", "text": "キャンセル"},
        "blocks": [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*{date}* の勤怠連絡を削除してもよろしいですか？\nこの操作は取り消せません。"
                }
            }
        ]
    }