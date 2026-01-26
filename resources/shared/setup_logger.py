# 標準ライブラリ
import sys
import os
import logging
import json
from logging.handlers import TimedRotatingFileHandler
from typing import Dict, Any, Optional

# サードパーティ製ライブラリ（pip installが必要です）
# none

# 自作モジュール
# none

def setup_logger(file_name="slack_bot"):
    """
    ロガーを初期化します。
    
    Args:
        file_name: ロガーの名前
        
    Returns:
        初期化されたロガーインスタンス
    """
    # ロガーを取得
    logger = logging.getLogger(file_name)

    # ログハンドラー初期化
    if logger.hasHandlers():
        logger.handlers.clear()
    
    # ログレベルの設定
    logger.setLevel(logging.INFO) # DEBUGは非表示

    # ログのフォーマット定義
    formatter = logging.Formatter('%(asctime)s [%(levelname)s] [%(name)s] %(message)s')

    # 「標準出力」へ流すためのハンドラを設定
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)
    logger.addHandler(handler)

    return logger


def log_structured(
    logger: logging.Logger,
    level: str,
    message: str,
    **extra_fields: Any
) -> None:
    """
    構造化ログを出力します（JSON形式）。
    
    GCP Cloud Loggingでの集計・検索を容易にするため、
    ログをJSON形式で出力します。
    
    Args:
        logger: ロガーインスタンス
        level: ログレベル（"info", "warning", "error"）
        message: メインメッセージ
        **extra_fields: 追加のフィールド（team_id, user_id等）
        
    Example:
        log_structured(
            logger, "warning", "AI解析失敗",
            team_id="T123",
            user_id="U456",
            text="おはようございます",
            reason="勤怠データが含まれていない"
        )
    """
    log_data = {
        "message": message,
        **extra_fields
    }
    
    json_str = json.dumps(log_data, ensure_ascii=False)
    
    log_func = getattr(logger, level.lower(), logger.info)
    log_func(json_str)


def log_ai_parse_failure(
    logger: logging.Logger,
    team_id: str,
    channel_id: str,
    user_id: str,
    text: str,
    reason: str
) -> None:
    """
    AI解析の失敗をログに記録します。
    
    後でCloud Loggingで「解析失敗一覧」を抽出できるよう、
    構造化ログとして出力します。
    
    Args:
        logger: ロガーインスタンス
        team_id: ワークスペースID
        channel_id: チャンネルID
        user_id: ユーザーID
        text: 発言内容
        reason: 解析失敗理由
        
    Example:
        log_ai_parse_failure(
            logger, "T123", "C456", "U789",
            "おはようございます",
            "勤怠データが含まれていない"
        )
    """
    log_structured(
        logger=logger,
        level="warning",
        message="[AI_PARSE_FAILURE]",
        team_id=team_id,
        channel_id=channel_id,
        user_id=user_id,
        text=text[:200],  # 長すぎる場合は切り詰める
        reason=reason
    )


def log_openai_cost(
    logger: logging.Logger,
    prompt_tokens: int,
    completion_tokens: int,
    total_tokens: int,
    model: str,
    team_id: Optional[str] = None,
    user_id: Optional[str] = None
) -> None:
    """
    OpenAI APIのコストデータをログに記録します。
    
    [COST_LOG] プレフィックスを付けることで、後でCloud Loggingで
    コスト集計がしやすくなります。
    
    Args:
        logger: ロガーインスタンス
        prompt_tokens: プロンプトトークン数
        completion_tokens: 完了トークン数
        total_tokens: 合計トークン数
        model: 使用したモデル名
        team_id: ワークスペースID（オプション）
        user_id: ユーザーID（オプション）
        
    Example:
        log_openai_cost(
            logger, 150, 50, 200, "gpt-4o-mini",
            team_id="T123", user_id="U456"
        )
    """
    extra = {}
    if team_id:
        extra["team_id"] = team_id
    if user_id:
        extra["user_id"] = user_id
    
    log_structured(
        logger=logger,
        level="info",
        message="[COST_LOG]",
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
        model=model,
        **extra
    )