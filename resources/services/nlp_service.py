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
            "2. ARROW (A->B): If B='出社' (plain office) -> action='delete', note='出社 (予定変更)'\n"
            "3. DATES: Process '明日/明後日' correctly using 'Today' field\n"
            "4. LOCATION: Only use status='out' for specific places (e.g., '九段下'). Plain '出社'='other'\n\n"

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
            # 例1: 在宅勤務
            {
                "role": "user",
                "content": "Today: 2026-01-28 (Tuesday)\nText: 明日は在宅です"
            },
            {
                "role": "assistant",
                "content": '{"is_attendance": true, "attendances": [{"date": "2026-01-29", "status": "remote", "note": "在宅", "action": "save"}]}'
            },
            # 例2: 取消
            {
                "role": "user",
                "content": "Today: 2026-01-28 (Tuesday)\nText: 明日の早退は取消します"
            },
            {
                "role": "assistant",
                "content": '{"is_attendance": true, "attendances": [{"date": "2026-01-29", "status": "other", "note": "早退の取消", "action": "delete"}]}'
            },
            # 例3: 矢印記法（出社への変更）
            {
                "role": "user",
                "content": "Today: 2026-01-27 (Monday)\nText: 1/30(金) 在宅 -> 出社"
            },
            {
                "role": "assistant",
                "content": '{"is_attendance": true, "attendances": [{"date": "2026-01-30", "status": "other", "note": "出社 (予定変更)", "action": "delete"}]}'
            },
            # 例4: 休暇
            {
                "role": "user",
                "content": "Today: 2026-01-28 (Tuesday)\nText: 1/30 午前休"
            },
            {
                "role": "assistant",
                "content": '{"is_attendance": true, "attendances": [{"date": "2026-01-30", "status": "vacation_am", "note": "午前休", "action": "save"}]}'
            },
            # 例5: 遅刻
            {
                "role": "user",
                "content": "Today: 2026-01-28 (Tuesday)\nText: 電車遅延で遅刻します"
            },
            {
                "role": "assistant",
                "content": '{"is_attendance": true, "attendances": [{"date": "2026-01-28", "status": "late_delay", "note": "電車遅延", "action": "save"}]}'
            },
            # 例6: 外出（具体的な場所）
            {
                "role": "user",
                "content": "Today: 2026-01-28 (Tuesday)\nText: 九段下に出社します"
            },
            {
                "role": "assistant",
                "content": '{"is_attendance": true, "attendances": [{"date": "2026-01-28", "status": "out", "note": "九段下に出社", "action": "save"}]}'
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