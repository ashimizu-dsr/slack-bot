import datetime
import json
import os
import re
from typing import Optional, Dict, Any, List
from resources.shared.setup_logger import setup_logger

# OpenAIのインポート（エラーハンドリング付き）
try:
    from openai import OpenAI
except ImportError:
    OpenAI = None

logger = setup_logger(__name__)

# ==========================================
# 1. 定数・設定
# ==========================================
STATUS_ALIASES = {
    "late": {"late", "遅刻", "遅れ", "遅延"},
    "early_leave": {"early_leave", "早退"},
    "out": {"out", "外出"},
    "remote": {"remote", "在宅", "リモート", "テレワーク"},
    "vacation": {"vacation", "休暇", "休み", "欠勤", "有給", "お休み"},
    "other": {"other", "未分類", "その他"},
}

# ==========================================
# 2. 内部補助関数
# ==========================================

def _normalize_status(value: str) -> str:
    val = value.lower()
    for canonical, aliases in STATUS_ALIASES.items():
        if val in aliases or any(a in val for a in aliases):
            return canonical
    return "other"

def _format_note(att_data: Dict) -> str:
    """AIが抽出した各フィールドを結合して備考欄を作成"""
    parts = []
    if att_data.get("start_time"): parts.append(f"{att_data['start_time']}出勤")
    if att_data.get("end_time"):   parts.append(f"{att_data['end_time']}退勤")
    if att_data.get("break_minutes"): parts.append(f"休憩{att_data['break_minutes']}分")
    
    ai_note = att_data.get("note")
    if ai_note and str(ai_note).lower() != "none":
        parts.append(str(ai_note))
        
    return " / ".join(parts) if parts else "勤怠連絡"

# ==========================================
# 3. メインサービス関数
# ==========================================

def extract_attendance_from_text(text: str) -> Optional[Dict[str, Any]]:
    """
    メッセージから勤怠情報を抽出する。
    修正ポイント：
    1. コードブロックを消さずにタグ付けし、文脈としてAIに渡す（不具合③対策）。
    2. 打ち消し線を「部分削除」としてマークし、残りの情報の更新を可能にする（不具合②対策）。
    3. プロンプトのルールを強化し、actionの判定精度を向上。
    """
    api_key = os.getenv("OPENAI_API_KEY")
    if not text or not api_key or not OpenAI:
        return None

    # --- 修正点1: 前処理（タグ付けによる文脈維持） ---
    
    # A. コードブロック (```...```) は消さずに特別なタグで囲む
    # これにより「コードブロック内しか書かない人」の情報も拾えるようになります
    clean_text = text.replace("```", "\n[STR_CODE_BLOCK_CONTENT]\n").replace("｀｀｀", "\n[STR_CODE_BLOCK_CONTENT]\n")
    
    # B. インラインコード (`...`) も同様
    clean_text = clean_text.replace("`", "[INLINE_CODE]").replace("｀", "[INLINE_CODE]")
    
    # C. 打ち消し線 (~...~) を「無効化されたデータ」としてマーク
    # 「物理削除」しないことで、同じ行にある別の有効な情報をAIが認識できます
    clean_text = re.sub(r'~(.*?)~', r'\n(INSTRUCTION_IGNORE_THIS_PART: \1)\n', clean_text)

    client = OpenAI(api_key=api_key)
    base_date = datetime.date.today() 
    
    try:
        # --- 修正点2: プロンプトのルール厳格化 ---
        system_instruction = (
            "You are a professional attendance data extractor. Output JSON: "
            "{is_attendance: bool, attendances: [{date, status, start_time, end_time, note, action}]}\n\n"
            "Rules:\n"
            "1. Information inside (INSTRUCTION_IGNORE_THIS_PART: ...) must be treated as canceled/deleted. "
            "If ONLY a part of the line is ignored, set action to 'save' and extract the remaining valid info.\n"
            "2. If an ENTIRE date or an entire line is ignored, set action to 'delete'.\n"
            "3. Text inside [STR_CODE_BLOCK_CONTENT] or [INLINE_CODE] is usually an example. "
            "Ignore it IF there is clear attendance info outside the blocks. "
            "However, if the ONLY information provided is inside these blocks, treat it as the user's input.\n"
            "4. Priority: Remaining plain text > Code block content > Ignored part.\n"
            "5. If multiple items exist for the same date, merge them into one record with action 'save' unless everything is canceled."
        )

        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": system_instruction},
                {"role": "user", "content": f"Today: {base_date}\nText: {clean_text}"}
            ],
            response_format={"type": "json_object"},
            temperature=0.1 # 判定のブレを抑えるため低めに設定
        )

        data = json.loads(response.choices[0].message.content)
        if not data.get("is_attendance") or not data.get("attendances"):
            return None

        attendances = data["attendances"]
        
        # 整形処理用のヘルパー
        def format_result(att):
            return {
                "date": att.get("date") or base_date.isoformat(),
                "status": _normalize_status(att.get("status", "other")),
                "note": _format_note(att),
                "action": att.get("action", "save")
            }

        # 1件目の処理
        result = format_result(attendances[0])

        # 2件目以降（複数日対応）
        if len(attendances) > 1:
            result["_additional_attendances"] = [format_result(a) for a in attendances[1:]]

        return result

    except Exception as e:
        logger.error(f"AI Extraction Error: {e}")
        return None