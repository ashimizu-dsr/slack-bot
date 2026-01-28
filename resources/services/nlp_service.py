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
        # システム指示の定義（最新ルール 2026-01-28: 実例ベース最適化版）
        system_instruction = (
            "You are an attendance data extractor. Output JSON only.\n"
            "Format: {\"is_attendance\": bool, \"attendances\": [{\"date\": \"YYYY-MM-DD\", \"status\": \"string\", \"note\": \"string\", \"action\": \"save\"|\"delete\"}]}\n\n"

            "CORE RULES:\n"
            "1. PLAIN '出社': If message says just '出社' (e.g., '1/26...出社') -> action='delete' (returning to normal work)\n"
            "2. ARROW (A->B): Extract ONLY B. Ignore A completely.\n"
            "   - If B='出社' -> action='delete'\n"
            "   - If B is specific status -> Extract B's status, action='save'\n"
            "3. AFTERNOON ATTENDANCE: '午後から出社/午後出社' -> status='vacation_am' OR status='other' with note='午後出社予定'\n"
            "4. VAGUE EXPRESSIONS: If uncertain timing (e.g., '午前は病院。出社したら報告') -> status='other', include all context in note\n"
            "5. NOTE EXTRACTION:\n"
            "   - Main reason: Extract core cause concisely (e.g., '最寄り駅運転見合わせのため' not full sentence)\n"
            "   - Secondary info: Put in parentheses (e.g., '自社都合（昼休憩13:00〜14:00）')\n"
            "   - Time details: Always include if mentioned (e.g., '16:30早退', '午後出社予定')\n"
            "6. HEALTH: Format as '体調不良(症状)'\n"
            "7. CANCELLATION: '取消/キャンセル/取り消し/削除' -> action='delete'\n\n"

            "STATUS:\n"
            "- vacation/vacation_am/vacation_pm/vacation_hourly: Leave\n"
            "- out: Specific location (e.g., '九段下')\n"
            "- late_delay: Train delay | late: General lateness\n"
            "- remote: Work from home\n"
            "- early_leave: Leave early\n"
            "- other: Mixed states, vague expressions, or uncertain timing\n"
        )

        # API呼び出し
        model_name = "gpt-4o-mini-2024-07-18"  # 最新の安定版を明示的に指定
        
        # Few-shot examples（実例ベース：ユーザーの実際の使用パターンに基づく）
        few_shot_examples = [
            # 例1: 運転見合わせで在宅（重要情報を簡潔に）
            {
                "role": "user",
                "content": "Today: 2026-01-28 (Tuesday)\nText: おはようございます。最寄り駅から運転見合わせており、出社が大幅に遅れると予想されるため本日在宅勤務に切り替えさせていただきます。"
            },
            {
                "role": "assistant",
                "content": '{"is_attendance": true, "attendances": [{"date": "2026-01-28", "status": "remote", "note": "最寄り駅運転見合わせのため", "action": "save"}]}'
            },
            # 例2: 複数日+出社（出社は削除）
            {
                "role": "user",
                "content": "Today: 2026-01-25 (Saturday)\nText: 直前で恐縮ですが勤怠を下記の通りとさせて下さい\n1/26...出社\n1/27...午前中在宅/午後出社\n1/30...午後在宅(社用の為)"
            },
            {
                "role": "assistant",
                "content": '{"is_attendance": true, "attendances": [{"date": "2026-01-26", "status": "other", "note": "予定変更", "action": "delete"}, {"date": "2026-01-27", "status": "other", "note": "AM在宅/PM出社", "action": "save"}, {"date": "2026-01-30", "status": "other", "note": "AM出社/PM在宅(社用の為)", "action": "save"}]}'
            },
            # 例3: 矢印記法（Bだけ抽出、早退時刻を記載）
            {
                "role": "user",
                "content": "Today: 2026-01-23 (Friday)\nText: 直前の連絡となり申し訳ございませんが、自社都合により明日の勤怠を以下の通り変更いたします。\n在宅(通常勤務) → 在宅(16:30早退)"
            },
            {
                "role": "assistant",
                "content": '{"is_attendance": true, "attendances": [{"date": "2026-01-24", "status": "other", "note": "在宅(16:30早退)", "action": "save"}]}'
            },
            # 例4: 在宅（メイン情報+補足情報）
            {
                "role": "user",
                "content": "Today: 2026-01-28 (Tuesday)\nText: おはようございます。本日自社都合で在宅勤務に変更させてください。また、昼休憩を13時〜14時で取ります。"
            },
            {
                "role": "assistant",
                "content": '{"is_attendance": true, "attendances": [{"date": "2026-01-28", "status": "remote", "note": "自社都合（昼休憩13:00〜14:00）", "action": "save"}]}'
            },
            # 例5: 午後出社（午前休または詳細記載）
            {
                "role": "user",
                "content": "Today: 2026-01-28 (Tuesday)\nText: 本日、家族の通院のため午後から出社します。"
            },
            {
                "role": "assistant",
                "content": '{"is_attendance": true, "attendances": [{"date": "2026-01-28", "status": "vacation_am", "note": "家族の通院", "action": "save"}]}'
            },
            # 例6: 曖昧な表現（午前は病院だが出社時刻不明）
            {
                "role": "user",
                "content": "Today: 2026-01-28 (Tuesday)\nText: 明日(1/29)の午前は病院に行ってきます。(出社したら、再度ご報告させて頂きます。)"
            },
            {
                "role": "assistant",
                "content": '{"is_attendance": true, "attendances": [{"date": "2026-01-29", "status": "other", "note": "午前は病院。出社したら、再度報告", "action": "save"}]}'
            },
            # 例7: インフルエンザで複数日在宅
            {
                "role": "user",
                "content": "Today: 2026-01-27 (Monday)\nText: インフルエンザB型と診断されたため、1/27(火)〜1/29(木) 出社→在宅勤務"
            },
            {
                "role": "assistant",
                "content": '{"is_attendance": true, "attendances": [{"date": "2026-01-27", "status": "remote", "note": "体調不良(インフルエンザB型)", "action": "save"}, {"date": "2026-01-28", "status": "remote", "note": "体調不良(インフルエンザB型)", "action": "save"}, {"date": "2026-01-29", "status": "remote", "note": "体調不良(インフルエンザB型)", "action": "save"}]}'
            },
            # 例8: 出社→全休
            {
                "role": "user",
                "content": "Today: 2026-01-27 (Monday)\nText: 1/30(金) 出社→全休（所用の為）"
            },
            {
                "role": "assistant",
                "content": '{"is_attendance": true, "attendances": [{"date": "2026-01-30", "status": "vacation", "note": "所用", "action": "save"}]}'
            },
            # 例9: 在宅→出社（削除）
            {
                "role": "user",
                "content": "Today: 2026-01-27 (Monday)\nText: 1/31(土) 在宅 -> 出社"
            },
            {
                "role": "assistant",
                "content": '{"is_attendance": true, "attendances": [{"date": "2026-01-31", "status": "other", "note": "予定変更", "action": "delete"}]}'
            },
            # 例10: 取消
            {
                "role": "user",
                "content": "Today: 2026-01-28 (Tuesday)\nText: 明日の早退は取消します"
            },
            {
                "role": "assistant",
                "content": '{"is_attendance": true, "attendances": [{"date": "2026-01-29", "status": "other", "note": "早退取消", "action": "delete"}]}'
            },
            # 例11: 電車遅延
            {
                "role": "user",
                "content": "Today: 2026-01-28 (Tuesday)\nText: 電車遅延で遅刻します"
            },
            {
                "role": "assistant",
                "content": '{"is_attendance": true, "attendances": [{"date": "2026-01-28", "status": "late_delay", "note": "", "action": "save"}]}'
            },
            # 例12: 病院後に出社（遅刻）
            {
                "role": "user",
                "content": "Today: 2026-01-28 (Tuesday)\nText: 本日体調不良のため、病院行った後に出社致します"
            },
            {
                "role": "assistant",
                "content": '{"is_attendance": true, "attendances": [{"date": "2026-01-28", "status": "late", "note": "体調不良(病院)", "action": "save"}]}'
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