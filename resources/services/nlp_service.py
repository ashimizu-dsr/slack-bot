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
        # システム指示の定義（最新ルール 2026-01-27）
        system_instruction = (
            "You are a professional attendance data extractor. Output JSON only.\n"
            "Format: { \"is_attendance\": bool, \"attendances\": [{ \"date\": \"YYYY-MM-DD\", \"status\": \"string\", \"note\": \"string\", \"action\": \"save\" | \"delete\" }] }\n\n"
            "=== Status Mapping (細分化ルール) ===\n"
            "1. 休暇 (vacation):\n"
            "   - vacation: 全休 (例: '休暇', '有給', 'お休み', '休み')\n"
            "   - vacation_am: 午前休 (例: 'AM休', '午前休', '午前', '午前半休')\n"
            "     ★重要: 「午前」や「AM」という言葉があれば、絶対に vacation ではなく vacation_am を選択\n"
            "   - vacation_pm: 午後休 (例: 'PM休', '午後休', '午後', '午後半休')\n"
            "     ★重要: 「午後」や「PM」という言葉があれば、絶対に vacation ではなく vacation_pm を選択\n"
            "   - vacation_hourly: 時間休 (例: '時間休', '時休', '10-12時休')\n"
            "2. 外出 (out): 場所を問わず外出 (例: '外出', '直行', '直帰', '情報センター', '楽天損保')\n"
            "3. 遅刻:\n"
            "   - late_delay: 電車遅延・交通乱れ (例: '電車遅延', '遅延証明', '交通乱れ', '遅延')\n"
            "   - late: 一般遅刻 (例: '遅刻', '遅れます')\n"
            "4. 勤務:\n"
            "   - remote: 在宅勤務 (例: '在宅', 'リモート', 'テレワーク')\n"
            "   - shift: シフト勤務 (例: 'シフト', '交代勤務', 'シフト勤務')\n"
            "5. 退勤: early_leave: 早退・退勤 (例: '早退', '退勤', '16時退社')\n"
            "6. その他: other (上記以外)\n\n"
            "=== 抽出精度強化ルール ===\n"
            "A. 時間・場所の保持: '15時退社', '楽天損保へ外出' など時間や場所は必ず note フィールドに含める。\n"
            "B. 打ち消し線 (~text~): 変更前の古い情報として扱い、打ち消し線の後の最新情報を優先。note に '(予定変更)' を付与。\n"
            "C. コードブロック (```): 内部の情報を最優先で厳密に抽出。公式データとして扱う。\n"
            "D. 時刻正規化: '8時' → '08:00', '17時半' → '17:30' の形式に変換して note に含める。\n"
            "E. 曖昧な表現: '遅れます' → 'late', '早く帰ります' → 'early_leave' として推測。\n"
            "F. AM/PM の区別: 「午前休」「AM休」→ vacation_am、「午後休」「PM休」→ vacation_pm を必ず選択。\n\n"
            "=== 出力例 ===\n"
            "Input: '~9時出勤~ 10時出勤します'\n"
            "Output: { \"is_attendance\": true, \"attendances\": [{ \"date\": \"2026-01-27\", \"status\": \"late\", \"note\": \"10時出勤 (予定変更)\", \"action\": \"save\" }] }\n\n"
            "Input: '明日15時に楽天損保へ外出します'\n"
            "Output: { \"is_attendance\": true, \"attendances\": [{ \"date\": \"2026-01-28\", \"status\": \"out\", \"note\": \"15時に楽天損保へ外出\", \"action\": \"save\" }] }\n\n"
            "Input: '明日午前休をとります'\n"
            "Output: { \"is_attendance\": true, \"attendances\": [{ \"date\": \"2026-01-28\", \"status\": \"vacation_am\", \"note\": \"午前休\", \"action\": \"save\" }] }"
        )

        # API呼び出し
        model_name = "gpt-4o-mini"
        response = client.chat.completions.create(
            model=model_name,
            messages=[
                {"role": "system", "content": system_instruction},
                {"role": "user", "content": f"Today: {base_date} ({base_date.strftime('%A')})\nText: {clean_text}"}
            ],
            response_format={"type": "json_object"},
            temperature=0.1
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