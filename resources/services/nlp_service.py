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
from resources.shared.setup_logger import setup_logger

try:
    from openai import OpenAI
except ImportError:
    OpenAI = None

logger = setup_logger(__name__)

# ステータスのエイリアス定義（正規化用）
STATUS_ALIASES = {
    "late": {"late", "遅刻", "遅れ", "遅延"},
    "early_leave": {"early_leave", "早退"},
    "out": {"out", "外出"},
    "remote": {"remote", "在宅", "リモート", "テレワーク"},
    "vacation": {"vacation", "休暇", "休み", "欠勤", "有給", "お休み"},
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
    val = str(value).lower()
    for canonical, aliases in STATUS_ALIASES.items():
        if val in aliases or any(a in val for a in aliases):
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

def extract_attendance_from_text(text: str) -> Optional[Dict[str, Any]]:
    """
    テキストから勤怠情報をAIで抽出します。
    
    Args:
        text: ユーザーが投稿したメッセージ
        
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
        # システム指示の定義
        system_instruction = (
            "You are a professional attendance data extractor. Output JSON only.\n"
            "Format: { \"is_attendance\": bool, \"attendances\": [{ \"date\": \"YYYY-MM-DD\", \"status\": \"string\", \"start_time\": \"HH:mm\", \"end_time\": \"HH:mm\", \"note\": \"string\", \"action\": \"save\" | \"delete\" }] }\n\n"
            "Rules:\n"
            "1. Sensitivity: If the text implies working hours, location (remote/out), or leave, set is_attendance=true.\n"
            "2. Mapping: '休暇/休み'->'vacation', '外出/直行/直帰/情報センター'->'out', '在宅/リモート'->'remote', '遅刻'->'late', '早退/退勤'->'early_leave'.\n"
            "3. Strikethroughs (~text~): Crucial. Represents 'old plan'. Prioritize new info following it. Prefix note with '(予定変更)'.\n"
            "4. Code Blocks (```): Treat as official, high-priority data. Extract all details strictly.\n"
            "5. Normalization: Convert '8時' to '08:00', '17時半' to '17:30'.\n"
            "6. Note: Keep specific context like '10:00出勤' or '終日情報センター'. Include '(予定変更)' if it corrects a previous plan."
        )

        # API呼び出し
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