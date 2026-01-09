"""
Slack Utilities - API通信やデータ加工の補助
"""
from typing import Optional, List, Dict

def get_user_email(client, user_id: str, logger) -> Optional[str]:
    """Slack APIを使用してメールアドレスを取得"""
    try:
        result = client.users_info(user=user_id)
        if result["ok"]:
            return result["user"]["profile"].get("email")
    except Exception as e:
        logger.error(f"Email取得失敗 (User:{user_id}): {e}")
    return None

def generate_time_options(interval_minutes: int = 5) -> List[Dict]:
    """時刻選択用のドロップダウン肢を生成"""
    options = []
    for minutes in range(0, 24 * 60, interval_minutes):
        time_str = f"{minutes // 60:02d}:{minutes % 60:02d}"
        options.append({"text": {"type": "plain_text", "text": time_str}, "value": time_str})
    return options