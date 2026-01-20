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
    1. コードブロック（半角/全角）を完全に物理削除し、誤認を防止。
    2. 打ち消し線を AI が認識しやすい【DELETE】マーカーに置換。
    3. AI に action: delete を出力させるようプロンプトを強化。
    """
    api_key = os.getenv("OPENAI_API_KEY")
    if not text or not api_key or not OpenAI:
        return None

    # --- 修正点1: 前処理（コードブロックとノイズの除去） ---
    
    # A. 複数行コードブロック (```...```) を削除 (半角・全角の両方に対応)
    # re.DOTALL を指定することで改行を含むブロックも一括削除します
    clean_text = re.sub(r'```[\s\S]*?```', '', text)
    clean_text = re.sub(r'｀｀｀[\s\S]*?｀｀｀', '', clean_text)
    
    # B. インラインコード (`...`) を削除
    clean_text = re.sub(r'`.*?`', '', clean_text)
    clean_text = re.sub(r'｀.*?｀', '', clean_text)
    
    # C. 打ち消し線 (~...~) を明示的な削除指示テキストに置換
    # AIが記号を見落とさないよう、テキストとして強調します
    clean_text = re.sub(r'~(.*?)~', r'\n【DELETE/CANCEL】: \1\n', clean_text)

    # デバッグ用：AIに渡る前のテキストを確認したい場合は有効にしてください
    # logger.info(f"Cleaned text for AI: {clean_text}")

    client = OpenAI(api_key=api_key)
    base_date = datetime.date.today() 
    
    try:
        # --- 修正点2: システムプロンプトの更新 ---
        # 削除指示（DELETE/CANCEL）がある場合は action: delete を出すよう厳格に指定
        system_instruction = (
            "You are a professional attendance data extractor. "
            "Extract info to JSON: {is_attendance: bool, attendances: [{date, status, start_time, end_time, note, action}]}. "
            "\nRules:\n"
            "1. If a line or information is marked with '【DELETE/CANCEL】', you MUST set action to 'delete'.\n"
            "2. Otherwise, set action to 'save'.\n"
            "3. If the text is empty after preprocessing or contains no attendance, set is_attendance to false."
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
            # AIが返してきた action をそのまま利用（デフォルトは save）
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