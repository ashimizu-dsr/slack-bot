"""
NLP Service - AIによる勤怠情報の抽出
"""
import datetime
import json
import os
import re
from typing import Optional, Dict, Any, List
from resources.shared.setup_logger import setup_logger

try:
    from openai import OpenAI
except ImportError:
    OpenAI = None

logger = setup_logger(__name__)

STATUS_ALIASES = {
    "late": {"late", "遅刻", "遅れ", "遅延"},
    "early_leave": {"early_leave", "早退"},
    "out": {"out", "外出"},
    "remote": {"remote", "在宅", "リモート", "テレワーク"},
    "vacation": {"vacation", "休暇", "休み", "欠勤", "有給", "お休み"},
    "other": {"other", "未分類", "その他"},
}

def _normalize_status(value: str) -> str:
    val = str(value).lower()
    for canonical, aliases in STATUS_ALIASES.items():
        if val in aliases or any(a in val for a in aliases):
            return canonical
    return "other"

def _format_note(att_data: Dict) -> str:
    """
    AIが抽出した備考をそのまま返す。
    余計な「勤怠連絡」や「出勤/退勤」の定型句は付与しない。
    """
    ai_note = att_data.get("note")
    
    # AIが "None", "null" と返してきた場合や、空の場合は空文字を返す
    if not ai_note or str(ai_note).lower() in ["none", "null"]:
        return ""
    
    return str(ai_note).strip()

def extract_attendance_from_text(text: str) -> Optional[Dict[str, Any]]:
    api_key = os.getenv("OPENAI_API_KEY")
    if not text or not api_key or not OpenAI:
        return None

    # --- 修正点1: 破壊的な前処理をやめる (不具合②対策) ---
    # 元のコードではコードブロックをタグに置換していましたが、
    # AIは生のコードブロック形式の方が「データである」と認識しやすいです。
    clean_text = text
    
    # 打ち消し線のみ、AIが「削除対象」と誤解しないようヒントを与える程度に留める
    clean_text = re.sub(r'~(.*?)~', r'(strike-through: \1)', clean_text)

    client = OpenAI(api_key=api_key)
    base_date = datetime.date.today() 
    
    try:
        # --- 修正点2: プロンプトの簡略化と指示の明確化 (不具合①・②対策) ---
        system_instruction = (
            "You are a professional attendance data extractor. Analyze the user's message and output JSON.\n"
            "Format: { \"is_attendance\": bool, \"attendances\": [{ \"date\": \"YYYY-MM-DD\", \"status\": \"string\", \"start_time\": \"HH:mm\", \"end_time\": \"HH:mm\", \"note\": \"string\", \"action\": \"save\" | \"delete\" }] }\n\n"
            "Rules:\n"
            "1. Status: '年休','休暇'->'vacation', '外出','情報センター'->'out', '在宅'->'remote', '遅刻'->'late', '早退'->'early_leave'.\n"
            "2. Note: Use this for specific locations, reasons, or specific times mentioned (e.g., '8:00出勤', '終日情報センター').\n"
            "   - If no specific info is provided beyond the status, leave 'note' empty (\"\").\n"
            "   - For changes like '~A~ -> B', set note to '(予定変更) B'.\n"
            "3. Code Blocks: Extract text inside ``` as official data.\n"
            "4. Today: Use the provided date to infer the year."
        )

        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": system_instruction},
                {"role": "user", "content": f"Today: {base_date} ({base_date.strftime('%A')})\nText: {clean_text}"}
            ],
            response_format={"type": "json_object"},
            temperature=0.1
        )

        data = json.loads(response.choices[0].message.content)
        # 修正ポイント: is_attendanceがFalseでも、attendancesがあれば拾う（AIの判定漏れ対策）
        if not data.get("attendances"):
            return None

        attendances = data["attendances"]
        
        def format_result(att):
            # AIが返してきた日付をそのまま使い、なければ今日を補完
            target_date = att.get("date")
            if not target_date or len(target_date) < 10:
                target_date = base_date.isoformat()
                
            return {
                "date": target_date,
                "status": _normalize_status(att.get("status", "other")),
                "note": _format_note(att),
                "action": att.get("action", "save")
            }

        # 抽出された全データを整形
        results = [format_result(a) for a in attendances]
        
        if not results:
            return None

        # 返却形式の維持（1件目をベースにし、2件目以降を _additional_attendances に入れる）
        final_result = results[0]
        if len(results) > 1:
            final_result["_additional_attendances"] = results[1:]

        return final_result

    except Exception as e:
        logger.error(f"AI Extraction Error: {e}")
        return None