"""
グループ関連のFirestore操作

GroupService から Firestore 操作部分を抽出。
"""

import logging
from typing import Optional, List, Dict, Any

from google.cloud import firestore
from resources.repositories.base import BaseRepository
from resources.constants import get_collection_name

logger = logging.getLogger(__name__)


class GroupRepository(BaseRepository):
    """グループドキュメントのCRUD操作"""

    def _groups_ref(self, workspace_id: str):
        """groups/{workspace_id}/groups/ のコレクション参照"""
        return (
            self._collection("groups")
            .document(workspace_id)
            .collection(get_collection_name("groups"))
        )

    def get_all(self, workspace_id: str) -> List[Dict[str, Any]]:
        docs = self._groups_ref(workspace_id).stream()
        groups = []
        for doc in docs:
            data = doc.to_dict()
            if "admin_ids" not in data:
                data["admin_ids"] = []
                try:
                    doc.reference.update({"admin_ids": []})
                except Exception as e:
                    logger.error(f"admin_idsフィールド追加失敗: {e}")
            # タイムスタンプをISO形式に変換
            for ts_field in ("created_at", "updated_at"):
                if ts_field in data and data[ts_field]:
                    data[ts_field] = (
                        data[ts_field].isoformat()
                        if hasattr(data[ts_field], "isoformat")
                        else str(data[ts_field])
                    )
            groups.append(data)
        logger.info(f"グループ取得成功: Workspace={workspace_id}, Count={len(groups)}")
        return groups

    def get_by_id(self, workspace_id: str, group_id: str) -> Optional[Dict[str, Any]]:
        doc = self._groups_ref(workspace_id).document(group_id).get()
        if not doc.exists:
            return None
        data = doc.to_dict()
        for ts_field in ("created_at", "updated_at"):
            if ts_field in data and data[ts_field]:
                data[ts_field] = (
                    data[ts_field].isoformat()
                    if hasattr(data[ts_field], "isoformat")
                    else str(data[ts_field])
                )
        return data

    def create(self, workspace_id: str, group_id: str, data: Dict[str, Any]) -> None:
        data["created_at"] = firestore.SERVER_TIMESTAMP
        data["updated_at"] = firestore.SERVER_TIMESTAMP
        self._groups_ref(workspace_id).document(group_id).set(data)
        logger.info(f"グループ作成成功: {group_id}")

    def exists(self, workspace_id: str, group_id: str) -> bool:
        return self._groups_ref(workspace_id).document(group_id).get().exists

    def update(self, workspace_id: str, group_id: str, data: Dict[str, Any]) -> None:
        data["updated_at"] = firestore.SERVER_TIMESTAMP
        self._groups_ref(workspace_id).document(group_id).update(data)
        logger.info(f"グループ更新成功: {group_id}")

    def delete(self, workspace_id: str, group_id: str) -> None:
        self._groups_ref(workspace_id).document(group_id).delete()
        logger.info(f"グループ削除成功: {group_id}")

    def find_by_name(self, workspace_id: str, name: str) -> Optional[Dict[str, Any]]:
        docs = list(
            self._groups_ref(workspace_id)
            .where("name", "==", name.strip())
            .limit(1)
            .stream()
        )
        if not docs:
            return None
        return docs[0].to_dict()
