# 標準ライブラリ
import sys
import os
import logging
from logging.handlers import TimedRotatingFileHandler

# サードパーティ製ライブラリ（pip installが必要です）
# none

# 自作モジュール
# none

def setup_logger(file_name="slack_bot"):

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