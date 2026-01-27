"""
モーダルUI構築モジュール

このモジュールは、SlackモーダルダイアログのBlock Kit JSONを生成します。
ビジネスロジックは含まず、純粋にUI構造のみを担当します。
"""
import datetime
import json
from typing import Dict, Any, Optional, List
from resources.constants import STATUS_TRANSLATION


# ==========================================
# 1. 勤怠入力/編集モーダル
# ==========================================
def build_attendance_modal(
    initial_data: Optional[Dict] = None, 
    is_fixed_date: bool = False
) -> Dict[str, Any]:
    """
    勤怠入力または編集用のモーダルを生成します。
    
    Args:
        initial_data: 既存データ（編集モードの場合に指定）
            - date: 日付（YYYY-MM-DD形式）
            - status: ステータス（late, vacation など）
            - note: 備考
        is_fixed_date: Trueの場合、日付を変更不可にする
            
    Returns:
        Slack モーダルビューの辞書
    """
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
    initial_status_option = next(
        (opt for opt in status_options if opt['value'] == initial_status), None
    )

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
def build_history_modal(
    history_records: List[Dict], 
    selected_year: str, 
    selected_month: str, 
    user_id: str
) -> Dict[str, Any]:
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
    year_options = [
        {"text": {"type": "plain_text", "text": f"{y}年"}, "value": str(y)} 
        for y in range(2025, 2036)
    ]
    month_options = [
        {"text": {"type": "plain_text", "text": f"{m}月"}, "value": f"{m:02d}"} 
        for m in range(1, 13)
    ]

    blocks = [
        {
            "type": "actions",
            "block_id": "history_filter",
            "elements": [
                {
                    "type": "static_select", 
                    "action_id": "history_year_change", 
                    "initial_option": next(
                        (o for o in year_options if o["value"] == selected_year), 
                        year_options[0]
                    ), 
                    "options": year_options
                },
                {
                    "type": "static_select", 
                    "action_id": "history_month_change", 
                    "initial_option": next(
                        (o for o in month_options if o["value"] == selected_month), 
                        month_options[0]
                    ), 
                    "options": month_options
                }
            ]
        },
        {"type": "divider"}
    ]

    if not history_records:
        blocks.append({
            "type": "section", 
            "text": {"type": "mrkdwn", "text": "_記録がありません_"}
        })
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
        "private_metadata": json.dumps({"target_user_id": user_id}),
        "title": {"type": "plain_text", "text": "勤怠連絡一覧"},
        "close": {"type": "plain_text", "text": "閉じる"},
        "blocks": blocks
    }


# ==========================================
# 3. 削除確認モーダル
# ==========================================
def build_delete_confirm_modal(date: str) -> Dict[str, Any]:
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
        "blocks": [{
            "type": "section", 
            "text": {"type": "mrkdwn", "text": f"*{date}* の勤怠連絡を削除してもよろしいですか？"}
        }]
    }


# ==========================================
# 4. レポート設定モーダル v2.3（グループごとadmin_ids管理）
# ==========================================
def build_admin_settings_modal(
    groups: List[Dict[str, Any]] = None, 
    user_name_map: Dict[str, str] = None
) -> Dict[str, Any]:
    """
    レポート設定モーダル（一覧表示）を生成します（v2.3）。
    
    各グループに通知先（admin_ids）を個別に設定できる形式です。
    
    Args:
        groups: グループ情報の配列（admin_idsフィールドを含む）
        user_name_map: ユーザーIDから表示名へのマッピング辞書
        
    Returns:
        Slack モーダルビューの辞書
    """
    if groups is None:
        groups = []
    
    if user_name_map is None:
        user_name_map = {}
    
    # ブロックの構築
    blocks = []
    
    # 1. グループ一覧
    if groups:
        for group in groups:
            # 通知先の名前を整形
            admin_ids = group.get("admin_ids", [])
            admin_names = []
            for uid in admin_ids:
                name = user_name_map.get(uid, f"<@{uid}>")
                admin_names.append(name)
            
            admins_text = ", ".join(admin_names) if admin_names else "（通知先未設定）"
            
            # メンバーの名前を整形
            member_ids = group.get("member_ids", [])
            member_names = []
            for uid in member_ids:
                name = user_name_map.get(uid, f"<@{uid}>")
                member_names.append(name)
            
            members_text = ", ".join(member_names) if member_names else "（メンバーなし）"
            
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*{group['name']}* (通知先:{admins_text})\n{members_text}"
                },
                "accessory": {
                    "type": "overflow",
                    "action_id": "group_overflow_action",
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
    
    # 2. 追加ボタン
    blocks.append({
        "type": "section",
        "text": {"type": "mrkdwn", "text": "*➕ 新しいグループを追加*"},
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


def build_add_group_modal() -> Dict[str, Any]:
    """
    グループ追加モーダルを生成します（v2.3）。
    
    通知先（admin_ids）を含む形式に対応。
    
    Returns:
        Slack モーダルビューの辞書
    """
    return {
        "type": "modal",
        "callback_id": "add_group_modal",
        "title": {"type": "plain_text", "text": "グループ編集"},
        "submit": {"type": "plain_text", "text": "保存"},
        "close": {"type": "plain_text", "text": "戻る"},
        "blocks": [
            {
                "type": "input",
                "block_id": "admin_block",
                "element": {
                    "type": "multi_users_select",
                    "action_id": "admin_select",
                    "placeholder": {"type": "plain_text", "text": "例：課長"}
                },
                "label": {"type": "plain_text", "text": "通知先"}
            },
            {
                "type": "context",
                "elements": [{
                    "type": "mrkdwn",
                    "text": "ⓘここに登録されたユーザには9:00に勤怠情報が通知されます。"
                }]
            },
            {"type": "divider"},
            {
                "type": "input",
                "block_id": "name_block",
                "element": {
                    "type": "plain_text_input",
                    "action_id": "name_input",
                    "placeholder": {"type": "plain_text", "text": "例：4/5課"}
                },
                "label": {"type": "plain_text", "text": "グループ名称"}
            },
            {
                "type": "input",
                "block_id": "members_block",
                "element": {
                    "type": "multi_users_select",
                    "action_id": "members_select",
                    "placeholder": {"type": "plain_text", "text": "例：4/5課所属者"}
                },
                "label": {"type": "plain_text", "text": "所属者"},
                "optional": True
            }
        ]
    }


def build_edit_group_modal(
    group_id: str, 
    group_name: str, 
    member_ids: List[str],
    admin_ids: List[str] = None
) -> Dict[str, Any]:
    """
    グループ編集モーダルを生成します（v2.3）。
    
    通知先（admin_ids）を含む形式に対応。
    
    Args:
        group_id: グループID（UUID）
        group_name: グループ名
        member_ids: メンバーのUser ID配列
        admin_ids: 管理者（通知先）のUser ID配列
        
    Returns:
        Slack モーダルビューの辞書
    """
    if admin_ids is None:
        admin_ids = []
    
    return {
        "type": "modal",
        "callback_id": "edit_group_modal",
        "title": {"type": "plain_text", "text": "グループ編集"},
        "submit": {"type": "plain_text", "text": "保存"},
        "close": {"type": "plain_text", "text": "戻る"},
        "blocks": [
            {
                "type": "input",
                "block_id": "admin_block",
                "element": {
                    "type": "multi_users_select",
                    "action_id": "admin_select",
                    **({"initial_users": admin_ids} if admin_ids else {}),
                    "placeholder": {"type": "plain_text", "text": "例：課長"}
                },
                "label": {"type": "plain_text", "text": "通知先"}
            },
            {
                "type": "context",
                "elements": [{
                    "type": "mrkdwn",
                    "text": "ⓘここに登録されたユーザには9:00に勤怠情報が通知されます。"
                }]
            },
            {"type": "divider"},
            {
                "type": "input",
                "block_id": "name_block",
                "element": {
                    "type": "plain_text_input",
                    "action_id": "name_input",
                    "initial_value": group_name,
                    "placeholder": {"type": "plain_text", "text": "例：4/5課"}
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
                    "placeholder": {"type": "plain_text", "text": "例：4/5課所属者"}
                },
                "label": {"type": "plain_text", "text": "所属者"},
                "optional": True
            }
        ],
        "private_metadata": json.dumps({"group_id": group_id})
    }


def build_member_delete_confirm_modal(
    group_id: str, 
    group_name: str
) -> Dict[str, Any]:
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
        "blocks": [{
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f":warning: *「{group_name}」の設定を完全に削除しますか？*\n"
                        f"このグループに関連付けられたメンバー情報やレポート設定がすべて消去されます。"
            }
        }],
        "private_metadata": json.dumps({"group_id": group_id, "group_name": group_name})
    }


# ==========================================
# 5. セットアップメッセージ
# ==========================================
def build_setup_message() -> List[Dict[str, Any]]:
    """
    Botがチャンネルに参加した際のセットアップメッセージを生成します。
    
    Returns:
        Slack Block Kitブロックの配列
    """
    return [
        {
            "type": "context",
            "elements": [{
                "type": "mrkdwn",
                "text": "ⓘ 勤怠連絡の管理を開始します。下のボタンより各課のメンバー設定をお願いします。"
            }]
        },
        {
            "type": "actions",
            "block_id": "setup_actions",
            "elements": [{
                "type": "button",
                "text": {"type": "plain_text", "text": "設定"},
                "action_id": "open_member_settings",
            }]
        }
    ]


# ==========================================
# 後方互換性のためのエイリアス
# ==========================================
def create_attendance_modal_view(initial_data: Optional[Dict] = None, **kwargs) -> Dict[str, Any]:
    """旧関数名との互換性のため"""
    return build_attendance_modal(initial_data, is_fixed_date=kwargs.get("is_fixed_date", False))


def create_history_modal_view(
    history_records: List[Dict], 
    selected_year: str, 
    selected_month: str, 
    user_id: str
) -> Dict[str, Any]:
    """旧関数名との互換性のため"""
    return build_history_modal(history_records, selected_year, selected_month, user_id)


def create_attendance_delete_confirm_modal(date: str) -> Dict[str, Any]:
    """旧関数名との互換性のため"""
    return build_delete_confirm_modal(date)


def create_admin_settings_modal(
    groups: List[Dict[str, Any]] = None, 
    user_name_map: Dict[str, str] = None,
    admin_ids: List[str] = None  # 後方互換性のため残すが無視
) -> Dict[str, Any]:
    """旧関数名との互換性のため（v2.3では admin_ids は無視）"""
    return build_admin_settings_modal(groups, user_name_map)


def create_add_group_modal() -> Dict[str, Any]:
    """旧関数名との互換性のため"""
    return build_add_group_modal()


def create_edit_group_modal(
    group_id: str, 
    group_name: str, 
    member_ids: List[str],
    admin_ids: List[str] = None
) -> Dict[str, Any]:
    """旧関数名との互換性のため"""
    return build_edit_group_modal(group_id, group_name, member_ids, admin_ids)


def create_member_delete_confirm_modal(
    group_id: str, 
    group_name: str
) -> Dict[str, Any]:
    """旧関数名との互換性のため"""
    return build_member_delete_confirm_modal(group_id, group_name)


def create_setup_message_blocks() -> List[Dict[str, Any]]:
    """旧関数名との互換性のため"""
    return build_setup_message()
