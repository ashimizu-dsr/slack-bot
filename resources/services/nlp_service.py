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
    user_id: Optional[str] = None,
    message_ts: Optional[str] = None,
    thread_context: Optional[str] = None,
    workspace_user_list: Optional[List[Dict[str, Any]]] = None,
) -> Optional[Dict[str, Any]]:
    """
    テキストから勤怠情報をAIで抽出します。

    Args:
        text: ユーザーが投稿したメッセージ（単体、またはスレッド返信の場合は子メッセージ）
        team_id: ワークスペースID（コストログ用、オプション）
        user_id: ユーザーID（コストログ用、オプション）
        message_ts: メッセージのタイムスタンプ（過去ログ処理用、オプション）
        thread_context: スレッド返信時に「親メッセージ＋返信」をセットにした文字列。
            「親: ... / 返信: ...」の形式で渡すと、AIに「以下のやり取りから最終的な出勤ステータスを判定して」と投げる。
        workspace_user_list: ワークスペースのユーザー一覧。メッセージ内の人名から誰の勤怠かを判定する際に使用。
            [{ user_id, email, real_name, display_name }, ...]。省略時は常に送信者本人の勤怠として扱う。

    Returns:
        抽出結果の辞書:
        {
            "date": "YYYY-MM-DD",
            "status": "late" など,
            "note": "備考",
            "action": "save" または "delete",
            "target_user_id": "Slack user_id または None（誰の勤怠か。省略時は送信者本人）",
            "_additional_attendances": [...] (複数日の場合)
        }
        抽出できない場合はNone

    Note:
        - OpenAI API (gpt-4o-mini) を使用
        - 打ち消し線 (~text~) は前処理で "(strike-through: text)" に変換
        - 複数日の記録にも対応（_additional_attendances に格納）
        - message_tsが指定されている場合は、そのタイムスタンプの日付を基準に「明日」などを解釈
        - thread_context 指定時は「やり取りから最終的な出勤ステータスを判定」するプロンプトで送る
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
    if thread_context:
        thread_context = re.sub(r'~(.*?)~', r'(strike-through: \1)', thread_context)

    client = OpenAI(api_key=api_key)
    
    # 基準日の決定：message_tsがある場合はそれを基準に、なければ今日
    if message_ts:
        try:
            # Slackのタイムスタンプ（Unix時間）をdatetimeに変換
            ts_float = float(message_ts)
            base_datetime = datetime.datetime.fromtimestamp(ts_float)
            base_date = base_datetime.date()
            logger.info(f"基準日をメッセージのタイムスタンプから設定: {base_date} (ts={message_ts})")
        except (ValueError, TypeError) as e:
            logger.warning(f"message_tsの変換に失敗、今日を基準日とします: {e}")
            base_date = datetime.date.today()
    else:
        base_date = datetime.date.today() 
    
    try:
        # システム指示の定義（最新ルール 2026-01-28: 実例ベース最適化版 + target_user_id）
        system_instruction = (
            "You are an attendance data extractor. Output JSON only.\n"
            "Format: {\"is_attendance\": bool, \"target_user_id\": \"Slack user_id or null\", \"attendances\": [{\"date\": \"YYYY-MM-DD\", \"status\": \"string\", \"note\": \"string\", \"action\": \"save\"|\"delete\"}]}\n\n"

            "CORE RULES:\n"
            "1. PLAIN '出社': If message says just '出社' (e.g., '1/26...出社') -> action='delete' (returning to normal work)\n"
            "2. '変更' KEYWORD: '変更' means UPDATE, not delete. Always action='save' when '変更' is mentioned.\n"
            "3. ARROW (A->B): Extract ONLY B. Ignore A completely. Always action='save' unless B='出社'.\n"
            "   - If B='出社' -> action='delete'\n"
            "   - If B is any other status (even if A and B are similar) -> Extract B's status, action='save'\n"
            "   - Examples: '在宅→在宅(早退)' -> Extract '在宅(早退)', action='save'\n"
            "4. DATE EXTRACTION: If date is explicitly written (e.g., '1/23(金)'), use that date. Ignore relative dates like '明日' in this case.\n"
            "5. LATENESS DETECTION - CRITICAL:\n"
            "   - '〜後に出社/〜してから出社/終わり次第向かう/向かいます' -> status='late'\n"
            "   - Time specified (e.g., '10時出社', '十時出社') -> status='late' and MUST include time in note\n"
            "   - Always extract and preserve time information in note (e.g., '体調不良（10時出社）')\n"
            "6. SAME DAY MULTIPLE STATUSES - CRITICAL:\n"
            "   - If ONE day has multiple statuses/events (e.g., '在宅' + '中抜け'), create ONLY ONE record\n"
            "   - Use status='other' and combine all details in note (e.g., '在宅（11時から1時間中抜け）')\n"
            "   - NEVER create multiple records for the same date\n"
            "7. AFTERNOON ATTENDANCE: '午後から出社/午後出社' -> status='vacation_am' OR status='other' with note='午後出社予定'\n"
            "8. VAGUE EXPRESSIONS: If uncertain timing (e.g., '午前は病院。出社したら報告') -> status='other', include all context in note\n"
            "9. NOTE EXTRACTION:\n"
            "   - Main reason: Extract core cause concisely\n"
            "   - Time details: ALWAYS include if mentioned (e.g., '10時出社', '11時から1時間中抜け')\n"
            "   - Secondary info: Put in parentheses (e.g., '体調不良（10時出社）', '在宅（昼休憩13:00〜14:00）')\n"
            "10. HEALTH: Format as '体調不良(症状/時間)'\n"
            "11. CANCELLATION: Only '取消/キャンセル/取り消し/削除' -> action='delete'. '変更' is NOT cancellation.\n"
            "12. TARGET PERSON: When the message clearly refers to ANOTHER person's attendance (e.g. '荒木課長 在宅', '荒木さんの勤怠'), set target_user_id to that person's user_id from the 'Workspace users' list. When the message is about the sender's own attendance, set target_user_id to null.\n\n"

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
            # 例3: 矢印記法（在宅→在宅(早退)への変更、日付明示）
            {
                "role": "user",
                "content": "Today: 2026-01-22 (Thursday)\nText: 直前の連絡となり申し訳ございませんが、自社都合により明日の勤怠を以下の通り変更いたします。\n1/23(金)\n在宅(通常勤務) → 在宅(16:30早退)\n勤怠一覧は更新済みです。"
            },
            {
                "role": "assistant",
                "content": '{"is_attendance": true, "attendances": [{"date": "2026-01-23", "status": "other", "note": "在宅(16:30早退)", "action": "save"}]}'
            },
            # 例4: 在宅に変更（メイン情報+補足情報）
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
            # 例12: 病院後に出社（終わり次第向かう=遅刻）
            {
                "role": "user",
                "content": "Today: 2026-01-28 (Tuesday)\nText: 子どもの病院に時間がかかっており、終わり次第向かいます。"
            },
            {
                "role": "assistant",
                "content": '{"is_attendance": true, "attendances": [{"date": "2026-01-28", "status": "late", "note": "子どもの病院に時間がかかっている", "action": "save"}]}'
            },
            # 例13: 時間指定の遅刻（時間を必ず記載）
            {
                "role": "user",
                "content": "Today: 2026-01-28 (Tuesday)\nText: 体調不良の為、10時出社とさせてください。"
            },
            {
                "role": "assistant",
                "content": '{"is_attendance": true, "attendances": [{"date": "2026-01-28", "status": "late", "note": "体調不良（10時出社）", "action": "save"}]}'
            },
            # 例14: 時間指定の遅刻（漢数字）
            {
                "role": "user",
                "content": "Today: 2026-01-28 (Tuesday)\nText: 朝から体調が優れず、十時出社とさせて下さい。"
            },
            {
                "role": "assistant",
                "content": '{"is_attendance": true, "attendances": [{"date": "2026-01-28", "status": "late", "note": "体調不良（10時出社）", "action": "save"}]}'
            },
            # 例15: 同日に複数の情報（在宅+中抜け）
            {
                "role": "user",
                "content": "Today: 2026-01-29 (Wednesday)\nText: 急遽所用のため在宅とさせてください。また11時から1時間程度中抜けします。"
            },
            {
                "role": "assistant",
                "content": '{"is_attendance": true, "attendances": [{"date": "2026-01-29", "status": "other", "note": "在宅（11時から1時間程度中抜け）", "action": "save"}]}'
            }
        ]
        
        # スレッド返信時は「やり取りから最終的な出勤ステータスを判定」する形でユーザーメッセージを構成
        if thread_context:
            user_content = (
                "以下のやり取りから、最終的な出勤ステータスを判定してください。"
                "（例: 親で遅刻予定、返信で「間に合いました」なら出社/遅刻取り消しとして扱う）\n\n"
                "【やり取り】\n"
                f"{thread_context}\n\n"
                f"Today: {base_date} ({base_date.strftime('%A')})"
            )
        else:
            user_content = f"Today: {base_date} ({base_date.strftime('%A')})\nText: {clean_text}"

        # ワークスペースユーザー一覧を渡す場合（誰の勤怠かを判定するため）
        if workspace_user_list:
            lines = ["Workspace users (use exact user_id for target_user_id when message is for someone else):"]
            for u in workspace_user_list:
                uid = u.get("user_id") or ""
                rn = (u.get("real_name") or "").strip()
                dn = (u.get("display_name") or "").strip()
                em = (u.get("email") or "").strip()
                lines.append(f"  {uid}: {rn} ({dn}) / {em}")
            user_content = user_content + "\n\n" + "\n".join(lines)

        messages = [
            {"role": "system", "content": system_instruction},
            *few_shot_examples,  # Few-shot examplesを挿入
            {"role": "user", "content": user_content},
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

        # 誰の勤怠か（メッセージ内に他人の名前がある場合にAIが設定）
        raw_target = data.get("target_user_id")
        final_result["target_user_id"] = (raw_target if raw_target and str(raw_target).strip() else None)

        logger.info(f"AI抽出成功: {len(results)}件の勤怠情報を抽出")
        return final_result

    except Exception as e:
        logger.error(f"AI Extraction Error: {e}", exc_info=True)
        return None