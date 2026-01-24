"""
Slack UI（Block Kit）ビュー構築モジュール（後方互換性レイヤー）

このモジュールは既存のコードとの互換性を保つため、
新しいtemplatesモジュールから関数をインポートして再エクスポートします。
"""
import datetime
import json
from typing import Dict, Any, Optional, List
from resources.constants import STATUS_TRANSLATION, SECTION_TRANSLATION

# 新しいモジュールからインポート
from resources.templates.modals import (
    build_attendance_modal as create_attendance_modal_view,
    build_history_modal as create_history_modal_view,
    build_delete_confirm_modal as create_attendance_delete_confirm_modal,
    build_admin_settings_modal as create_admin_settings_modal,
    build_add_group_modal as create_add_group_modal,
    build_edit_group_modal as create_edit_group_modal,
    build_member_delete_confirm_modal as create_member_delete_confirm_modal,
    build_setup_message as create_setup_message_blocks
)

from resources.templates.cards import (
    build_attendance_card as create_attendance_card_blocks
)

# 旧関数の呼び出しを新関数にラップ
# create_attendance_modal_view は既に互換性があるので、そのまま


# ==========================================
# 旧関数名でのエクスポート（後方互換性）
# ==========================================
__all__ = [
    'create_attendance_modal_view',
    'create_history_modal_view',
    'create_attendance_delete_confirm_modal',
    'create_admin_settings_modal',
    'create_add_group_modal',
    'create_edit_group_modal',
    'create_member_delete_confirm_modal',
    'create_setup_message_blocks',
    'create_attendance_card_blocks',
]

# ==========================================
# 1. 勤怠入力/編集モーダル
# ==========================================
def create_attendance_modal_view(initial_data: Optional[Dict] = None, **kwargs) -> Dict[str, Any]:
    """
    勤怠入力または編集用のモーダルを生成します。
    
    Args:
        initial_data: 既存データ（編集モードの場合に指定）
            - date: 日付（YYYY-MM-DD形式）
            - status: ステータス（late, vacation など）
            - note: 備考
        **kwargs: 追加オプション
            - is_fixed_date: Trueの場合、日付を変更不可にする
            
    Returns:
        Slack モーダルビューの辞書
    """
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
    ユーザーの勤怠履歴を表示するモーダルを生成します。
    
    Args:
        history_records: 勤怠記録の配列
        selected_year: 選択されている年（文字列）
        selected_month: 選択されている月（"01"〜"12"）
        user_id: 対象ユーザーのID（private_metadataに保存、年月変更時に使用）
        
    Returns:
        Slack モーダルビューの辞書
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
    """
    課別メンバー設定用のモーダルを生成します。
    
    Args:
        channel_id: 対象チャンネルID（将来の拡張用、現状は未使用）
        **kwargs: 追加オプション
        
    Returns:
        Slack モーダルビューの辞書
        
    Note:
        全8セクション（1課〜7課、金融開発課）のユーザー選択肢を含みます。
    """
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
    """
    日次レポート用のBlock Kitブロックを生成します。
    
    Args:
        header: レポートのヘッダーテキスト（例: "01/21(水)の勤怠一覧"）
        section_data: {セクション名: [勤怠記録配列]}
        
    Returns:
        Slack Block Kitブロックの配列
    """
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
    """
    Botがチャンネルに参加した際のセットアップメッセージを生成します。
    
    Returns:
        Slack Block Kitブロックの配列
    """
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

def create_attendance_card_blocks(record: Any, display_name: str = None, **kwargs) -> List[Dict[str, Any]]:
    """
    勤怠記録カード（通知用）のBlock Kitブロックを生成します。
    
    Args:
        record: AttendanceRecordオブジェクトまたは辞書
        **kwargs: 追加オプション
            - is_update: 更新通知の場合True
            - show_buttons: ボタンを表示する場合True
            
    Returns:
        Slack Block Kitブロックの配列
    """
    def get_val(obj, key):
        """オブジェクトまたは辞書から値を取得する内部関数"""
        return obj.get(key) if isinstance(obj, dict) else getattr(obj, key, None)

    user_id = get_val(record, 'user_id')
    date_val = get_val(record, 'date')
    status_jp = STATUS_TRANSLATION.get(get_val(record, 'status'), get_val(record, 'status'))
    note_val = get_val(record, 'note')

    label = "を修正しました" if kwargs.get("is_update") else "を記録しました"

    # もし display_name が渡されなかった時のためにバックアップロジックを用意
    # name_to_show = display_name if display_name else f"<@{user_id}>"
    
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
def create_attendance_delete_confirm_modal(date: str):
    """
    勤怠記録削除の確認モーダルを生成します。
    
    Args:
        date: 削除対象の日付（YYYY-MM-DD形式）
        
    Returns:
        Slack モーダルビューの辞書
    """
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
    """
    エラー通知用の汎用モーダルを生成します。
    
    Args:
        title: モーダルのタイトル
        message: エラーメッセージ（Markdown対応）
        
    Returns:
        Slack モーダルビューの辞書
    """
    return {
        "type": "modal",
        "title": {"type": "plain_text", "text": title},
        "close": {"type": "plain_text", "text": "閉じる"},
        "blocks": [{"type": "section", "text": {"type": "mrkdwn", "text": message}}]
    }

# # ==========================================
# # 6. v2.0 設定モーダル（動的グループ管理）
# # ==========================================
# def create_member_settings_modal_v2(
#     admin_ids: List[str],
#     all_groups: List[Dict[str, Any]],
#     selected_group_id: Optional[str] = None,
#     selected_group_members: List[str] = None
# ) -> Dict[str, Any]:
#     """
#     v2.0の設定モーダルを生成します（動的グループ管理）。
    
#     Args:
#         admin_ids: 現在の管理者のユーザーID配列
#         all_groups: 全グループ情報の配列
#             [{"group_id": "...", "name": "...", "member_ids": [...]}, ...]
#         selected_group_id: 現在選択されているグループID（初回はNone）
#         selected_group_members: 選択されているグループのメンバーID配列（初回は[]）
        
#     Returns:
#         Slack モーダルビューの辞書
        
#     Note:
#         - グループ選択のoptionsを動的に生成
#         - 最後に「➕ 新規グループを追加」を追加
#         - selected_group_idがある場合、そのグループを初期選択
#         - selected_group_membersをtarget_members_blockのinitial_usersに設定
#     """
#     if selected_group_members is None:
#         selected_group_members = []
    
#     # グループ選択肢を生成
#     group_options = []
#     initial_group_option = None
    
#     for group in all_groups:
#         option = {
#             "text": {"type": "plain_text", "text": group["name"]},
#             "value": group["group_id"]
#         }
#         group_options.append(option)
        
#         # 初期選択されているグループを記録
#         if selected_group_id and group["group_id"] == selected_group_id:
#             initial_group_option = option
    
#     # 「➕ 新規グループを追加」を最後に追加
#     group_options.append({
#         "text": {"type": "plain_text", "text": "➕ 新規グループを追加"},
#         "value": "action_new_group"
#     })
    
#     # グループ選択要素の構築
#     group_element = {
#         "type": "static_select",
#         "placeholder": {
#             "type": "plain_text",
#             "text": "課を選択",
#             "emoji": True
#         },
#         "options": group_options,
#         "action_id": "group_select_action"
#     }
    
#     # 初期選択がある場合は設定
#     if initial_group_option:
#         group_element["initial_option"] = initial_group_option
    
#     # 所属者選択要素の構築
#     members_element = {
#         "type": "multi_users_select",
#         "placeholder": {
#             "type": "plain_text",
#             "text": "ユーザを選択（複数選択可）",
#             "emoji": True
#         },
#         "action_id": "target_members_select"
#     }
    
#     # 選択されているメンバーがいる場合は設定
#     if selected_group_members:
#         members_element["initial_users"] = selected_group_members
    
#     # モーダルの構築
#     return {
#         "type": "modal",
#         "callback_id": "member_settings_v2",
#         "title": {
#             "type": "plain_text",
#             "text": "9:00レポート設定",
#             "emoji": True
#         },
#         "submit": {
#             "type": "plain_text",
#             "text": "保存",
#             "emoji": True
#         },
#         "close": {
#             "type": "plain_text",
#             "text": "キャンセル",
#             "emoji": True
#         },
#         "blocks": [
#             {
#                 "type": "input",
#                 "block_id": "admin_users_block",
#                 "element": {
#                     "type": "multi_users_select",
#                     "placeholder": {
#                         "type": "plain_text",
#                         "text": "ユーザを選択（複数選択可）",
#                         "emoji": True
#                     },
#                     "action_id": "admin_users_select",
#                     **({"initial_users": admin_ids} if admin_ids else {})
#                 },
#                 "label": {
#                     "type": "plain_text",
#                     "text": "管理者",
#                     "emoji": True
#                 }
#             },
#             {
#                 "type": "context",
#                 "elements": [
#                     {
#                         "type": "mrkdwn",
#                         "text": "ⓘ 管理者には、毎朝9:00に当日の勤怠連絡が通知されます。"
#                     }
#                 ]
#             },
#             {
#                 "type": "divider"
#             },
#             {
#                 "type": "input",
#                 "block_id": "group_selection_input_block",
#                 "element": group_element,
#                 "label": {
#                     "type": "plain_text",
#                     "text": "課",
#                     "emoji": True
#                 },
#                 "optional": True
#             },
#             {
#                 "type": "input",
#                 "block_id": "target_members_block",
#                 "element": members_element,
#                 "label": {
#                     "type": "plain_text",
#                     "text": "課の所属者",
#                     "emoji": True
#                 },
#                 "optional": True
#             }
#         ],
#         "private_metadata": json.dumps({
#             "selected_group_id": selected_group_id
#         })
#     }

# # ==========================================
# # 7. v2.1 設定モーダル（テキスト入力版・UPSERT方式）
# # ==========================================
# def _generate_groups_list_text(groups: List[Dict[str, Any]]) -> str:
#     """
#     登録済みグループ一覧のテキストを生成します。
    
#     Args:
#         groups: グループ情報の配列
#             [{"group_id": "...", "name": "...", "member_ids": [...]}, ...]
        
#     Returns:
#         Markdown形式の箇条書きテキスト
        
#     Example:
#         • 営業1課 (3人)
#         • 営業2課 (5人)
#         • 開発課 (8人)
#     """
#     if not groups:
#         return "_まだグループが登録されていません_"
    
#     lines = []
#     for group in groups:
#         name = group.get("name", "不明なグループ")
#         member_count = len(group.get("member_ids", []))
#         lines.append(f"• {name} ({member_count}人)")
    
#     return "\n".join(lines)


# def create_member_settings_modal_v2_1(
#     admin_ids: List[str],
#     all_groups: List[Dict[str, Any]]
# ) -> Dict[str, Any]:
#     """
#     v2.1の設定モーダルを生成します（テキスト入力版・UPSERT方式）。
    
#     Args:
#         admin_ids: 現在の管理者のユーザーID配列
#         all_groups: 全グループ情報の配列
#             [{"group_id": "...", "name": "...", "member_ids": [...]}, ...]
        
#     Returns:
#         Slack モーダルビューの辞書
        
#     Note:
#         v2.0との違い:
#         - グループ選択のドロップダウンを廃止
#         - グループ名をテキスト入力で指定（UPSERT: 既存なら更新、新規なら作成）
#         - 登録済みグループ一覧を常時表示（context block）
#         - 動的更新（views.update）は不要
#     """
#     # 登録済みグループ一覧のテキスト生成
#     groups_text = _generate_groups_list_text(all_groups)
    
#     return {
#         "type": "modal",
#         "callback_id": "member_settings_v2_1",
#         "title": {
#             "type": "plain_text",
#             "text": "9:00レポート設定"
#         },
#         "submit": {
#             "type": "plain_text",
#             "text": "保存"
#         },
#         "close": {
#             "type": "plain_text",
#             "text": "キャンセル"
#         },
#         "blocks": [
#             {
#                 "type": "input",
#                 "block_id": "admin_users_block",
#                 "element": {
#                     "type": "multi_users_select",
#                     "action_id": "admin_users_select",
#                     "placeholder": {
#                         "type": "plain_text",
#                         "text": "ユーザを選択"
#                     },
#                     **({"initial_users": admin_ids} if admin_ids else {})
#                 },
#                 "label": {
#                     "type": "plain_text",
#                     "text": "管理者 (全グループ共通)"
#                 }
#             },
#             {
#                 "type": "context",
#                 "elements": [
#                     {
#                         "type": "mrkdwn",
#                         "text": "ⓘ 管理者に設定されたユーザに当日の勤怠情報が通知されます。"
#                     }
#                 ]
#             },
#             {"type": "divider"},
#             {
#                 "type": "section",
#                 "text": {
#                     "type": "mrkdwn",
#                     "text": "*登録済みグループ:*"
#                 }
#             },
#             {
#                 "type": "context",
#                 "block_id": "registered_groups_list",
#                 "elements": [
#                     {
#                         "type": "mrkdwn",
#                         "text": groups_text
#                     }
#                 ]
#             },
#             {"type": "divider"},
#             {
#                 "type": "section",
#                 "text": {
#                     "type": "mrkdwn",
#                     "text": "*グループを作成・更新する*"
#                 }
#             },
#             {
#                 "type": "input",
#                 "block_id": "group_name_input_block",
#                 "element": {
#                     "type": "plain_text_input",
#                     "action_id": "group_name_input",
#                     "placeholder": {
#                         "type": "plain_text",
#                         "text": "例：営業1課"
#                     }
#                 },
#                 "label": {
#                     "type": "plain_text",
#                     "text": "グループ名"
#                 },
#                 "optional": True
#             },
#             {
#                 "type": "context",
#                 "elements": [
#                     {
#                         "type": "mrkdwn",
#                         "text": "ⓘ 登録済みグループ名を入力すると更新、新規グループ名を入力するとグループが作成されます。"
#                     }
#                 ]
#             },
#             {
#                 "type": "input",
#                 "block_id": "target_members_block",
#                 "element": {
#                     "type": "multi_users_select",
#                     "action_id": "target_members_select",
#                     "placeholder": {
#                         "type": "plain_text",
#                         "text": "メンバーを選択"
#                     }
#                 },
#                 "label": {
#                     "type": "plain_text",
#                     "text": "このグループの所属者"
#                 },
#                 "optional": True
#             }
#         ]
#     }

# # ==========================================
# # 5-3. メンバー設定モーダル v2.2（複数グループ一括管理版）
# # ==========================================
# def create_member_settings_modal_v2(
#     admin_ids: List[str] = None,
#     groups_data: List[Dict[str, Any]] = None,
#     group_count: int = None
# ) -> Dict[str, Any]:
#     """
#     v2.2の設定モーダルを生成します（複数グループ同時編集版）。
    
#     このモーダルは最大10グループを一度に編集できる機能を提供します。
#     「➕ グループの新規作成」ボタンで動的にグループ入力セットを追加可能です。
    
#     Args:
#         admin_ids: 現在の管理者（通知先）のユーザーID配列
#         groups_data: 既存グループデータの配列
#             [
#                 {"name": "営業1課", "member_ids": ["U001", "U002"]},
#                 {"name": "営業2課", "member_ids": ["U003"]},
#                 ...
#             ]
#         group_count: 表示するグループ数（Noneの場合はgroups_dataの長さ、最小1）
        
#     Returns:
#         Slack モーダルビューの辞書
        
#     Note:
#         - group_countを指定することで、空のグループ入力セットを追加可能
#         - 最大10グループ
#         - 各グループの入力セットには初期値（groups_data）を設定
#         - private_metadataに現在のgroup_countを保存し、動的更新に使用
        
#     Example:
#         # 新規（グループなし）
#         view = create_member_settings_modal_v2()
        
#         # 既存グループを編集
#         view = create_member_settings_modal_v2(
#             admin_ids=["U001"],
#             groups_data=[
#                 {"name": "営業1課", "member_ids": ["U002", "U003"]},
#                 {"name": "営業2課", "member_ids": ["U004"]}
#             ]
#         )
        
#         # 動的にグループを追加（views.update用）
#         view = create_member_settings_modal_v2(
#             admin_ids=["U001"],
#             groups_data=[...],
#             group_count=3  # 既存2つ + 新規1つ
#         )
#     """
#     if groups_data is None:
#         groups_data = []
    
#     if admin_ids is None:
#         admin_ids = []
    
#     # グループ数を決定（最小1、最大10）
#     if group_count is None:
#         group_count = max(len(groups_data), 1)
    
#     group_count = min(max(group_count, 1), 10)
    
#     # ==========================================
#     # ブロックの構築
#     # ==========================================
#     blocks = []
    
#     # 1. 通知先（管理者）
#     admin_element = {
#         "type": "multi_users_select",
#         "action_id": "admin_users_select",
#         "placeholder": {"type": "plain_text", "text": "ユーザを選択"}
#     }
    
#     if admin_ids:
#         admin_element["initial_users"] = admin_ids
    
#     blocks.append({
#         "type": "input",
#         "block_id": "admin_users_block",
#         "element": admin_element,
#         "label": {"type": "plain_text", "text": "通知先"}
#     })
    
#     # 2. 説明文
#     blocks.append({
#         "type": "context",
#         "elements": [
#             {
#                 "type": "mrkdwn",
#                 "text": "ⓘ 通知先に登録されたユーザには、午前9時に下記グループの当日の勤怠連絡が通知されます。"
#             }
#         ]
#     })
    
#     blocks.append({"type": "divider"})
    
#     # 3. グループ入力セットを追加
#     for i in range(1, group_count + 1):
#         # 既存データがあれば初期値として設定
#         initial_name = ""
#         initial_members = []
        
#         if i <= len(groups_data):
#             initial_name = groups_data[i - 1].get("name", "")
#             initial_members = groups_data[i - 1].get("member_ids", [])
        
#         # グループ名入力
#         name_element = {
#             "type": "plain_text_input",
#             "action_id": "group_name_input",
#             "placeholder": {"type": "plain_text", "text": "例：1課"}
#         }
        
#         if initial_name:
#             name_element["initial_value"] = initial_name
        
#         blocks.append({
#             "type": "input",
#             "block_id": f"group_name_{i}",
#             "element": name_element,
#             "label": {"type": "plain_text", "text": f"#{i}：グループ名"},
#             "optional": True
#         })
        
#         # メンバー選択
#         members_element = {
#             "type": "multi_users_select",
#             "action_id": "target_members_select",
#             "placeholder": {"type": "plain_text", "text": f"例：{i}課の所属者"}
#         }
        
#         if initial_members:
#             members_element["initial_users"] = initial_members
        
#         blocks.append({
#             "type": "input",
#             "block_id": f"group_members_{i}",
#             "element": members_element,
#             "label": {"type": "plain_text", "text": f"#{i}：メンバー"},
#             "optional": True
#         })
        
#         blocks.append({"type": "divider"})
    
#     # 4. グループ追加ボタン（10未満の場合のみ）
#     if group_count < 10:
#         blocks.append({
#             "type": "actions",
#             "block_id": "add_group_action_block",
#             "elements": [
#                 {
#                     "type": "button",
#                     "text": {"type": "plain_text", "text": "➕ グループの新規作成"},
#                     "value": "add_group",
#                     "action_id": "add_group_button_action"
#                 }
#             ]
#         })
#     else:
#         blocks.append({
#             "type": "context",
#             "elements": [
#                 {"type": "mrkdwn", "text": "_グループは最大10個までです_"}
#             ]
#         })
    
#     # ==========================================
#     # モーダル構造を返す
#     # ==========================================
#     return {
#         "type": "modal",
#         "callback_id": "member_settings_v2",
#         "title": {"type": "plain_text", "text": "勤怠レポートの設定"},
#         "submit": {"type": "plain_text", "text": "保存"},
#         "close": {"type": "plain_text", "text": "キャンセル"},
#         "blocks": blocks,
#         "private_metadata": json.dumps({
#             "group_count": group_count,
#             "groups_data": groups_data  # group_idも含む全データを保存
#         })
#     }

# ==========================================
# 6. レポート設定モーダル v2.22（一覧表示 + views.push版）
# ==========================================

def create_admin_settings_modal(admin_ids: List[str] = None, groups: List[Dict[str, Any]] = None, user_name_map: Dict[str, str] = None) -> Dict[str, Any]:
    """
    レポート設定モーダル（一覧表示）を生成します（v2.22）。
    
    このモーダルはグループを一覧形式で表示し、オーバーフローメニュー（...）から
    個別に編集・削除できる機能を提供します。
    """
    if admin_ids is None:
        admin_ids = []
    
    if groups is None:
        groups = []
    
    # ブロックの構築
    blocks = []
    
    # 1. 通知先（管理者）
    admin_element = {
        "type": "multi_users_select",
        "action_id": "admin_select",
        "placeholder": {"type": "plain_text", "text": "ユーザを選択"}
    }
    
    if admin_ids:
        # 無効なIDが混じっているとエラーになるためクレンジング
        valid_admin_ids = [uid for uid in admin_ids if uid and isinstance(uid, str)]
        if valid_admin_ids:
            admin_element["initial_users"] = valid_admin_ids
    
    blocks.append({
        "type": "input",
        "block_id": "admin_block",
        "element": admin_element,
        "label": {"type": "plain_text", "text": "レポート通知先"}
    })
    
    # 2. 説明文
    blocks.append({
        "type": "context",
        "elements": [
            {
                "type": "mrkdwn",
                "text": "ⓘ ここに登録されたユーザには9:00に勤怠情報が通知されます。"
            }
        ]
    })
    
    blocks.append({"type": "divider"})

    if user_name_map is None:
        user_name_map = {}
    
    # 3. グループ一覧
    if groups:
        for i, group in enumerate(groups, 1):
            # --- 【修正箇所】メンション <@uid> をやめて表示名にする ---
            member_names = []
            for uid in group.get("member_ids", []):
                name = user_name_map.get(uid, f"<@{uid}>") # マップにあれば名前、なければID
                member_names.append(name)
            
            members_text = ", ".join(member_names)

            if not members_text:
                members_text = "（メンバーなし）"
            
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*{group['name']}*\n{members_text}"
                },
                "accessory": {
                    "type": "overflow",
                    "action_id": "group_overflow_action",  # 【修正箇所】固定値に変更
                    "options": [
                        {
                            "text": {"type": "plain_text", "text": "🔄 編集", "emoji": True},
                            "value": f"edit_{group['group_id']}"
                        },
                        {
                            "text": {"type": "plain_text", "text": "❌ 削除", "emoji": True},
                            "value": f"delete_{group['group_id']}"
                        }
                    ]
                }
            })
            blocks.append({"type": "divider"})
    else:
        # グループが0件の場合
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": "_まだグループが登録されていません_"}
        })
        blocks.append({"type": "divider"})
    
    # 4. 追加ボタン
    blocks.append({
        "type": "section",
        "text": {"type": "mrkdwn", "text": "*+ 新しいグループを追加*"},
        "accessory": {
            "type": "button",
            "text": {"type": "plain_text", "text": "追加", "emoji": True},
            "style": "primary",
            "action_id": "add_new_group"
        }
    })
    
    return {
        "type": "modal",
        "callback_id": "admin_settings_modal",
        "title": {"type": "plain_text", "text": "レポート設定", "emoji": True},
        "submit": {"type": "plain_text", "text": "保存", "emoji": True},
        "close": {"type": "plain_text", "text": "キャンセル", "emoji": True},
        "blocks": blocks
    }


def create_add_group_modal() -> Dict[str, Any]:
    """
    グループ追加モーダルを生成します（v2.22）。
    
    Returns:
        Slack モーダルビューの辞書
    """
    return {
        "type": "modal",
        "callback_id": "add_group_modal",
        "title": {"type": "plain_text", "text": "グループの追加"},
        "submit": {"type": "plain_text", "text": "保存"},
        "close": {"type": "plain_text", "text": "戻る"},
        "blocks": [
            {
                "type": "input",
                "block_id": "name_block",
                "element": {
                    "type": "plain_text_input",
                    "action_id": "name_input",
                    "placeholder": {"type": "plain_text", "text": "グループ名称を入力"}
                },
                "label": {"type": "plain_text", "text": "グループ名称"}
            },
            {
                "type": "input",
                "block_id": "members_block",
                "element": {
                    "type": "multi_users_select",
                    "action_id": "members_select",
                    "placeholder": {"type": "plain_text", "text": "メンバーを選択"}
                },
                "label": {"type": "plain_text", "text": "所属メンバー"},
                "optional": True
            }
        ]
    }


def create_edit_group_modal(group_id: str, group_name: str, member_ids: List[str]) -> Dict[str, Any]:
    """
    グループ編集モーダルを生成します（v2.22）。
    
    Args:
        group_id: グループID（UUID）
        group_name: グループ名
        member_ids: メンバーのUser ID配列
        
    Returns:
        Slack モーダルビューの辞書
    """
    return {
        "type": "modal",
        "callback_id": "edit_group_modal",
        "title": {"type": "plain_text", "text": "グループの編集"},
        "submit": {"type": "plain_text", "text": "更新"},
        "close": {"type": "plain_text", "text": "戻る"},
        "blocks": [
            {
                "type": "input",
                "block_id": "name_block",
                "element": {
                    "type": "plain_text_input",
                    "action_id": "name_input",
                    "initial_value": group_name
                },
                "label": {"type": "plain_text", "text": "グループ名称"}
            },
            {
                "type": "input",
                "block_id": "members_block",
                "element": {
                    "type": "multi_users_select",
                    "action_id": "members_select",
                    **({"initial_users": member_ids} if member_ids else {}),
                    "placeholder": {"type": "plain_text", "text": "メンバーを選択"}
                },
                "label": {"type": "plain_text", "text": "所属メンバー"},
                "optional": True
            }
        ],
        "private_metadata": json.dumps({"group_id": group_id})
    }


def create_member_delete_confirm_modal(group_id: str, group_name: str) -> Dict[str, Any]:
    """
    削除確認モーダルを生成します（v2.22）。
    
    Args:
        group_id: グループID（UUID）
        group_name: グループ名
        
    Returns:
        Slack モーダルビューの辞書
    """
    return {
        "type": "modal",
        "callback_id": "delete_confirm_modal",
        "title": {"type": "plain_text", "text": "削除の確認"},
        "submit": {"type": "plain_text", "text": "削除する", "emoji": True},
        "close": {"type": "plain_text", "text": "キャンセル", "emoji": True},
        "blocks": [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f":warning: *「{group_name}」の設定を完全に削除しますか？*\nこのグループに関連付けられたメンバー情報やレポート設定がすべて消去されます。"
                }
            }
        ],
        "private_metadata": json.dumps({"group_id": group_id, "group_name": group_name})
    }