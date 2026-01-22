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

def sanitize_group_name(name: str) -> str:
    """
    グループ名をFirestoreドキュメントIDとして使用可能な形式に変換します（v2.2）。
    
    FirestoreのドキュメントIDには特定の文字制限があるため、
    禁止文字を安全な文字に置換します。
    
    Args:
        name: 元のグループ名
        
    Returns:
        サニタイズされたグループ名
        
    Note:
        - スラッシュ '/' とバックスラッシュ '\\' をアンダースコア '_' に置換
        - 先頭と末尾のピリオド '.' を削除
        - 最大50文字を推奨（Firestore自体は1500バイトまで対応）
        
    Example:
        >>> sanitize_group_name("営業/開発")
        "営業_開発"
        >>> sanitize_group_name(".test.")
        "test"
    """
    if not name:
        return ""
    
    # 禁止文字を置換
    sanitized = name.replace("/", "_").replace("\\", "_")
    
    # 先頭と末尾のピリオドを削除
    sanitized = sanitized.strip(".")
    
    return sanitized