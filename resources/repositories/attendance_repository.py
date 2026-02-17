"""
勤怠レコードのFirestore操作

shared/db.py の勤怠関連関数を移行したもの。
"""

import datetime
import logging
from typing import Optional, List, Dict, Any

from google.cloud import firestore
from resources.repositories.base import BaseRepository

logger = logging.getLogger(__name__)

COLLECTION = "attendance"


class AttendanceRepository(BaseRepository):
    """勤怠レコードのCRUD操作"""

    def save(
        self,
        workspace_id: str,
        user_id: str,
        email: Optional[str],
        date: str,
        status: str,
        note: str,
        channel_id: str,
        ts: str,
    ) -> None:
        doc_id = f"{workspace_id}_{user_id}_{date}"
        self._collection(COLLECTION).document(doc_id).set({
            "workspace_id": workspace_id,
            "user_id": user_id,
            "email": email or "",
            "date": date,
            "status": status,
            "note": note,
            "channel_id": channel_id,
            "ts": ts,
            "updated_at": firestore.SERVER_TIMESTAMP,
        })
        logger.info(f"Saved attendance: {doc_id}")

    def get_single(self, workspace_id: str, user_id: str, date: str) -> Optional[Dict[str, Any]]:
        doc_id = f"{workspace_id}_{user_id}_{date}"
        doc = self._collection(COLLECTION).document(doc_id).get()
        return doc.to_dict() if doc.exists else None

    def get_history(
        self,
        workspace_id: str,
        user_id: str,
        email: Optional[str],
        month_filter: str,
    ) -> List[Dict[str, Any]]:
        query = self._collection(COLLECTION).where("workspace_id", "==", workspace_id)
        if email:
            docs = query.where("email", "==", email).stream()
        else:
            docs = query.where("user_id", "==", user_id).stream()

        results = [d.to_dict() for d in docs]
        filtered = [r for r in results if r.get("date", "").startswith(month_filter)]
        return sorted(filtered, key=lambda x: x["date"], reverse=True)

    def delete(self, workspace_id: str, user_id: str, date: str) -> None:
        doc_id = f"{workspace_id}_{user_id}_{date}"
        self._collection(COLLECTION).document(doc_id).delete()
        logger.info(f"Deleted attendance record: {doc_id}")

    def get_by_date(self, workspace_id: str, date_str: Optional[str] = None) -> List[Dict[str, Any]]:
        target_date = date_str or datetime.datetime.now().strftime("%Y-%m-%d")
        docs = (
            self._collection(COLLECTION)
            .where("workspace_id", "==", workspace_id)
            .where("date", "==", target_date)
            .stream()
        )
        results = [d.to_dict() for d in docs]
        logger.info(f"Retrieved {len(results)} records for {target_date} in workspace {workspace_id}")
        return results
