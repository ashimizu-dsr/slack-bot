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
    修正ポイント：コードブロック除去、打ち消し線の削除判定、actionフラグの追加
    """
    api_key = os.getenv("OPENAI_API_KEY")
    if not text or not api_key or not OpenAI:
        return None

    # --- 修正点1: 前処理（ノイズ除去と記号の強調） ---
    # A. コードブロック (```...``` や `...`) を完全に削除して誤認を防ぐ
    clean_text = re.sub(r'```.*?```', '', text, flags=re.DOTALL)
    clean_text = re.sub(r'`.*?`', '', clean_text)
    
    # B. 打ち消し線 (~...~) を明示的な削除指示テキストに置換する
    # AIが記号を見落とさないよう、「これは消すべきデータだ」と言葉で教える
    clean_text = re.sub(r'~(.*?)~', r'\n【DELETE/CANCEL】: \1\n', clean_text)

    client = OpenAI(api_key=api_key)
    base_date = datetime.date.today() 
    
    try:
        # --- 修正点2: システムプロンプトの更新 ---
        # "action" フィールドを追加させ、削除指示を判定させる
        system_instruction = (
            "Extract attendance info to JSON: {is_attendance: bool, attendances: "
            "[{date, status, start_time, end_time, note, action}]}. "
            "IMPORTANT: If the line contains '【DELETE/CANCEL】', set action to 'delete'. "
            "Otherwise, set action to 'save'."
        )

        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": system_instruction},
                {"role": "user", "content": f"Today: {base_date}\nText: {clean_text}"}
            ],
            response_format={"type": "json_object"},
            temperature=0.2
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
                "action": att.get("action", "save") # 追加：delete or save
            }

        # 1件目の処理
        result = format_result(attendances[0])

        # 2件目以降（複数日）の処理
        if len(attendances) > 1:
            result["_additional_attendances"] = [format_result(a) for a in attendances[1:]]

        return result

    except Exception as e:
        logger.error(f"AI Extraction Error: {e}")
        return None