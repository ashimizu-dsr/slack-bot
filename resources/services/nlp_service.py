"""
自然言語処理サービス

このモジュールは、OpenAI APIを使用してユーザーのメッセージから
勤怠情報を抽出します。打ち消し線や複数日の記録にも対応しています。
"""
import datetime
import json
import os
import re
from typing import Optional, Dict, Any, List
from resources.shared.setup_logger import setup_logger, log_openai_cost
from resources.constants import STATUS_AI_ALIASES  # constantsから読み込む

try:
    from openai import OpenAI
except ImportError:
    OpenAI = None

logger = setup_logger(__name__)

# ステータスのエイリアス定義（正規化用）- 最新ルール 2026-01-27
STATUS_ALIASES = {
    # 休暇（細分化）
    "vacation": {"vacation", "休暇", "休み", "欠勤", "有給", "お休み", "全休"},
    "vacation_am": {"vacation_am", "am休", "午前休", "午前半休"},
    "vacation_pm": {"vacation_pm", "pm休", "午後休", "午後半休"},
    "vacation_hourly": {"vacation_hourly", "時間休", "時休"},
    
    # 外出（場所を問わず）
    "out": {"out", "外出", "直行", "直帰", "外勤", "情報センター", "楽天損保"},
    
    # 遅刻（細分化）
    "late_delay": {"late_delay", "電車遅延", "遅延", "遅延証明", "交通乱れ", "交通トラブル"},
    "late": {"late", "遅刻", "遅れ"},
    
    # 勤務
    "remote": {"remote", "在宅", "リモート", "テレワーク", "在宅勤務"},
    "shift": {"shift", "シフト", "交代勤務", "シフト勤務"},
    
    # 退勤
    "early_leave": {"early_leave", "早退", "退勤", "早帰り"},
    
    # その他
    "other": {"other", "未分類", "その他"},
}

def _normalize_status(value: str) -> str:
    """
    ステータス値を正規化します。
    
    Args:
        value: AIが抽出したステータス値（日本語または英語）
        
    Returns:
        正規化されたステータス値（late, early_leave, out, remote, vacation, other）
        
    Note:
        エイリアスに該当しない場合は "other" を返します。
    """
    val = str(value).lower().strip()
    
    # 1. まず完全一致を確認（AIが返した status をそのまま使う）
    for canonical in STATUS_AI_ALIASES.keys():
        if val == canonical:
            return canonical
    # 2. 次に、日本語キーワードなどのエイリアスに含まれているか確認
    # 詳細なステータス（vacation_am等）から先にチェックされるように
    # STATUS_AI_ALIASES の定義順も重要です
    for canonical, aliases in STATUS_AI_ALIASES.items():
        if val in aliases or any(a == val for a in aliases):
            return canonical
            
    return "other"


def _format_note(att_data: Dict) -> str:
    """
    AIが抽出した備考を整形します。
    
    Args:
        att_data: AIの抽出結果（"note"キーを含む辞書）
        
    Returns:
        整形された備考文字列（空の場合は空文字列）
        
    Note:
        現行仕様では、AIが抽出した備考をそのまま返します。
        過去のバージョンでは「勤怠連絡」などの定型句を付与していましたが、
        現在は削除されています。
    """
    ai_note = att_data.get("note")
    
    # AIが "None", "null" と返してきた場合や、空の場合は空文字を返す
    if not ai_note or str(ai_note).lower() in ["none", "null"]:
        return ""
    
    return str(ai_note).strip()

def extract_attendance_from_text(
    text: str, 
    team_id: Optional[str] = None, 
    user_id: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    """
    テキストから勤怠情報をAIで抽出します。
    
    Args:
        text: ユーザーが投稿したメッセージ
        team_id: ワークスペースID（コストログ用、オプション）
        user_id: ユーザーID（コストログ用、オプション）
        
    Returns:
        抽出結果の辞書:
        {
            "date": "YYYY-MM-DD",
            "status": "late" など,
            "note": "備考",
            "action": "save" または "delete",
            "_additional_attendances": [...] (複数日の場合)
        }
        抽出できない場合はNone
        
    Note:
        - OpenAI API (gpt-4o-mini) を使用
        - 打ち消し線 (~text~) は前処理で "(strike-through: text)" に変換
        - 複数日の記録にも対応（_additional_attendances に格納）
    """
    logger.info(f"DEBUG_AI_INPUT: [{text}] (type: {type(text)})")
    api_key = os.getenv("OPENAI_API_KEY")
    if not text or not api_key or not OpenAI:
        logger.warning("AI抽出がスキップされました（API_KEYまたはテキストが空）")
        return None

    # 【打ち消し線の前処理】
    # Slackの ~text~ 記法を AIが理解しやすい形式に変換
    clean_text = text
    clean_text = re.sub(r'~(.*?)~', r'(strike-through: \1)', clean_text)

    client = OpenAI(api_key=api_key)
    base_date = datetime.date.today() 
    
    try:
        # システム指示の定義（最新ルール 2026-01-28: Few-shot最適化版）
        system_instruction = (
            "You are an attendance data extractor. Output JSON only.\n"
            "Format: {\"is_attendance\": bool, \"attendances\": [{\"date\": \"YYYY-MM-DD\", \"status\": \"string\", \"note\": \"string\", \"action\": \"save\"|\"delete\"}]}\n\n"

            "RULES:\n"
            "1. CANCELLATION WORDS: If message contains '取消/キャンセル/取り消し/削除' -> action='delete'\n"
            "2. MULTIPLE STATUSES: If one day has 2+ statuses (e.g., '在宅(午後休)', '午前休+午後出社'), use status='other' and put details in note.\n"
            "3. ARROW (A->B) - CRITICAL:\n"
            "   - If B is specific status (在宅/休暇/遅刻 etc) -> Extract B's status, action='save' (this updates the plan)\n"
            "   - If B='出社' (plain office, returning to normal) -> action='delete', status='other', note='予定変更'\n"
            "   - If B has mixed statuses -> status='other', action='save'\n"
            "4. DATES: Process '明日/明後日' correctly using 'Today' field. Support date ranges like '1/27〜1/29'.\n"
            "5. LATENESS DETECTION: '〜後に出社/〜してから出社/10時に出社/遅れて出社' -> status='late'. Reason goes to note.\n"
            "6. HEALTH REASONS: If specific symptoms mentioned (頭痛/発熱/眩暈/腹痛/インフルエンザ etc), format note as '体調不良(症状)'.\n"
            "7. LOCATION: Only use status='out' for specific places (e.g., '九段下'). Plain '出社' without context='other'\n"
            "8. NOTE FIELD: Extract ONLY reasons, times, or details. DO NOT repeat the status type (e.g., if status='vacation_am', note should NOT be '午前休').\n\n"

            "STATUS:\n"
            "- vacation/vacation_am/vacation_pm/vacation_hourly: Paid leave\n"
            "- out: Specific office location or business trip\n"
            "- late_delay: Train delay | late: General lateness\n"
            "- remote: Work from home\n"
            "- early_leave: Leave early\n"
            "- other: Office return, mixed states, deletions, or unknown\n"
        )

        # API呼び出し
        model_name = "gpt-4o-mini-2024-07-18"  # 最新の安定版を明示的に指定
        
        # Few-shot examples（実例を追加して学習効果を高める）
        few_shot_examples = [
            # 例1: 在宅勤務（理由なし）
            {
                "role": "user",
                "content": "Today: 2026-01-28 (Tuesday)\nText: 明日は在宅です"
            },
            {
                "role": "assistant",
                "content": '{"is_attendance": true, "attendances": [{"date": "2026-01-29", "status": "remote", "note": "", "action": "save"}]}'
            },
            # 例1-2: 在宅勤務（体調不良）
            {
                "role": "user",
                "content": "Today: 2026-01-28 (Tuesday)\nText: 本日、頭痛と眩暈がするため在宅勤務"
            },
            {
                "role": "assistant",
                "content": '{"is_attendance": true, "attendances": [{"date": "2026-01-28", "status": "remote", "note": "体調不良(頭痛・眩暈)", "action": "save"}]}'
            },
            # 例1-3: 在宅勤務（発熱）
            {
                "role": "user",
                "content": "Today: 2026-01-28 (Tuesday)\nText: 発熱のため本日在宅にします"
            },
            {
                "role": "assistant",
                "content": '{"is_attendance": true, "attendances": [{"date": "2026-01-28", "status": "remote", "note": "体調不良(発熱)", "action": "save"}]}'
            },
            # 例2: 取消（削除理由を簡潔に）
            {
                "role": "user",
                "content": "Today: 2026-01-28 (Tuesday)\nText: 明日の早退は取消します"
            },
            {
                "role": "assistant",
                "content": '{"is_attendance": true, "attendances": [{"date": "2026-01-29", "status": "other", "note": "早退取消", "action": "delete"}]}'
            },
            # 例3: 矢印記法（出社→在宅に変更、理由付き）
            {
                "role": "user",
                "content": "Today: 2026-01-27 (Monday)\nText: インフルエンザB型と診断されたため、1/27(火)〜1/29(木) 出社→在宅勤務"
            },
            {
                "role": "assistant",
                "content": '{"is_attendance": true, "attendances": [{"date": "2026-01-27", "status": "remote", "note": "体調不良(インフルエンザB型)", "action": "save"}, {"date": "2026-01-28", "status": "remote", "note": "体調不良(インフルエンザB型)", "action": "save"}, {"date": "2026-01-29", "status": "remote", "note": "体調不良(インフルエンザB型)", "action": "save"}]}'
            },
            # 例3-2: 矢印記法（出社への変更=削除）
            {
                "role": "user",
                "content": "Today: 2026-01-27 (Monday)\nText: 1/31(土) 在宅 -> 出社"
            },
            {
                "role": "assistant",
                "content": '{"is_attendance": true, "attendances": [{"date": "2026-01-31", "status": "other", "note": "予定変更", "action": "delete"}]}'
            },
            # 例3-3: 矢印記法（出社→全休に変更）
            {
                "role": "user",
                "content": "Today: 2026-01-27 (Monday)\nText: 1/30(金) 出社→全休（所用の為）"
            },
            {
                "role": "assistant",
                "content": '{"is_attendance": true, "attendances": [{"date": "2026-01-30", "status": "vacation", "note": "所用", "action": "save"}]}'
            },
            # 例3-4: 複合ステータス（在宅+午後休）
            {
                "role": "user",
                "content": "Today: 2026-01-27 (Monday)\nText: 2/3(月) 出社 -> 在宅(午後休)"
            },
            {
                "role": "assistant",
                "content": '{"is_attendance": true, "attendances": [{"date": "2026-02-03", "status": "other", "note": "在宅(午後休)", "action": "save"}]}'
            },
            # 例5: 複合ステータス（午前休+在宅）
            {
                "role": "user",
                "content": "Today: 2026-01-28 (Tuesday)\nText: 明日は午前休、午後から在宅"
            },
            {
                "role": "assistant",
                "content": '{"is_attendance": true, "attendances": [{"date": "2026-01-29", "status": "other", "note": "午前休・午後在宅", "action": "save"}]}'
            },
            # 例6: 時間休（時間を記載）
            {
                "role": "user",
                "content": "Today: 2026-01-28 (Tuesday)\nText: 1/30 時間休 10-12時"
            },
            {
                "role": "assistant",
                "content": '{"is_attendance": true, "attendances": [{"date": "2026-01-30", "status": "vacation_hourly", "note": "10-12時", "action": "save"}]}'
            },
            # 例7: 午前休（体調不良）
            {
                "role": "user",
                "content": "Today: 2026-01-28 (Tuesday)\nText: 明日午前休（体調不良で病院）"
            },
            {
                "role": "assistant",
                "content": '{"is_attendance": true, "attendances": [{"date": "2026-01-29", "status": "vacation_am", "note": "体調不良(病院)", "action": "save"}]}'
            },
            # 例8: 午前休（私用）
            {
                "role": "user",
                "content": "Today: 2026-01-28 (Tuesday)\nText: 明日午前休（私用）"
            },
            {
                "role": "assistant",
                "content": '{"is_attendance": true, "attendances": [{"date": "2026-01-29", "status": "vacation_am", "note": "私用", "action": "save"}]}'
            },
            # 例9: 遅刻（電車遅延）
            {
                "role": "user",
                "content": "Today: 2026-01-28 (Tuesday)\nText: 電車遅延で遅刻します"
            },
            {
                "role": "assistant",
                "content": '{"is_attendance": true, "attendances": [{"date": "2026-01-28", "status": "late_delay", "note": "", "action": "save"}]}'
            },
            # 例10: 遅刻（病院）
            {
                "role": "user",
                "content": "Today: 2026-01-28 (Tuesday)\nText: 本日体調不良のため、病院行った後に出社致します"
            },
            {
                "role": "assistant",
                "content": '{"is_attendance": true, "attendances": [{"date": "2026-01-28", "status": "late", "note": "体調不良(病院)", "action": "save"}]}'
            },
            # 例11: 遅刻（用事）
            {
                "role": "user",
                "content": "Today: 2026-01-28 (Tuesday)\nText: 用事を済ませてから出社します"
            },
            {
                "role": "assistant",
                "content": '{"is_attendance": true, "attendances": [{"date": "2026-01-28", "status": "late", "note": "用事", "action": "save"}]}'
            },
            # 例12: 遅刻（時間指定）
            {
                "role": "user",
                "content": "Today: 2026-01-28 (Tuesday)\nText: 10時に出社します"
            },
            {
                "role": "assistant",
                "content": '{"is_attendance": true, "attendances": [{"date": "2026-01-28", "status": "late", "note": "10時", "action": "save"}]}'
            },
            # 例13: 外出（場所を記載）
            {
                "role": "user",
                "content": "Today: 2026-01-28 (Tuesday)\nText: 九段下に出社します"
            },
            {
                "role": "assistant",
                "content": '{"is_attendance": true, "attendances": [{"date": "2026-01-28", "status": "out", "note": "九段下", "action": "save"}]}'
            }
        ]
        
        messages = [
            {"role": "system", "content": system_instruction},
            *few_shot_examples,  # Few-shot examplesを挿入
            {"role": "user", "content": f"Today: {base_date} ({base_date.strftime('%A')})\nText: {clean_text}"}
        ]
        
        response = client.chat.completions.create(
            model=model_name,
            messages=messages,
            response_format={"type": "json_object"},
            temperature=0.0  # 0.1 -> 0.0 に変更（より一貫性のある出力）
        )

        # OpenAI APIコストのログ出力
        usage = response.usage
        if usage:
            log_openai_cost(
                logger=logger,
                prompt_tokens=usage.prompt_tokens,
                completion_tokens=usage.completion_tokens,
                total_tokens=usage.total_tokens,
                model=model_name,
                team_id=team_id,
                user_id=user_id
            )

        data = json.loads(response.choices[0].message.content)
        
        # attendancesが存在しない場合は抽出失敗
        if not data.get("attendances"):
            logger.info("AI抽出結果: 勤怠情報なし")
            return None

        attendances = data["attendances"]
        
        def format_result(att):
            """抽出結果を整形する内部関数"""
            # 日付の補完（AIが返さなかった場合は今日を使用）
            target_date = att.get("date")
            if not target_date or len(target_date) < 10:
                target_date = base_date.isoformat()
                
            return {
                "date": target_date,
                "status": _normalize_status(att.get("status", "other")),
                "note": _format_note(att),
                "action": att.get("action", "save")
            }

        # 全てのデータを整形
        results = [format_result(a) for a in attendances]
        
        if not results:
            return None

        # 返却形式: 1件目をベースにし、2件目以降を _additional_attendances に入れる
        final_result = results[0]
        if len(results) > 1:
            final_result["_additional_attendances"] = results[1:]

        logger.info(f"AI抽出成功: {len(results)}件の勤怠情報を抽出")
        return final_result

    except Exception as e:
        logger.error(f"AI Extraction Error: {e}", exc_info=True)
        return None