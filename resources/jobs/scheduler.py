"""
Report Scheduler - Background job for automated reporting.

This module handles scheduled tasks like daily report generation
and distribution.
"""

import datetime
import time
import threading
import sys
import os
from typing import Optional, Dict, Any, Callable

# Add project root to path for absolute imports
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from resources.shared.setup_logger import get_logger

logger = get_logger(__name__)


class ReportScheduler:
    """Scheduler for automated report generation and distribution."""

    def __init__(self):
        self.jobs: Dict[str, Dict[str, Any]] = {}
        self.running = False
        self.thread: Optional[threading.Thread] = None

    def add_daily_report_job(self, job_id: str, time_str: str,
                           callback: Callable, **kwargs) -> None:
        """Add a daily report job."""
        hour, minute = map(int, time_str.split(':'))
        self.jobs[job_id] = {
            'hour': hour,
            'minute': minute,
            'callback': callback,
            'kwargs': kwargs,
            'last_run': None
        }
        logger.info(f"Added daily job {job_id} at {time_str}")

    def start(self) -> None:
        """Start the scheduler."""
        if self.running:
            logger.warning("Scheduler is already running")
            return

        self.running = True
        self.thread = threading.Thread(target=self._run_loop, daemon=True)
        self.thread.start()
        logger.info("Report scheduler started")

    def stop(self) -> None:
        """Stop the scheduler."""
        self.running = False
        if self.thread:
            self.thread.join(timeout=5)
        logger.info("Report scheduler stopped")

    def _run_loop(self) -> None:
        """
        Main scheduler loop.
        修正ポイント: time.sleep(1) に変更し、毎秒チェックすることで
        実行開始時刻のズレ（最大59秒）を解消しました。
        """
        logger.info("Scheduler loop started with 1-second precision")
        while self.running:
            try:
                now = datetime.datetime.now()
                current_time = now.strftime("%H:%M")

                # Check each job
                for job_id, job_config in self.jobs.items():
                    scheduled_time = f"{job_config['hour']:02d}:{job_config['minute']:02d}"

                    # 時刻(HH:MM)が一致し、かつ今日まだ実行していない場合のみ実行
                    if current_time == scheduled_time and job_config['last_run'] != now.date():
                        try:
                            # ジョブ実行
                            job_config['callback'](**job_config['kwargs'])
                            # 実行完了日を記録（同分内の重複実行を防止）
                            job_config['last_run'] = now.date()
                            logger.info(f"Executed job {job_id} at {now.strftime('%H:%M:%S')}")
                        except Exception as e:
                            logger.error(f"Job {job_id} failed: {e}")

                # 1秒待機して次のループへ。
                # CPU負荷を抑えつつ、00秒のタイミングを逃さないための設定です。
                time.sleep(1)

            except Exception as e:
                logger.error(f"Scheduler loop error: {e}")
                time.sleep(1)