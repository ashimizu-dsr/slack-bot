"""
ワークスペース関連のFirestore操作

shared/db.py の workspace / workspace_settings / channel_history 関連を統合。
"""

import logging
from typing import Optional, List, Dict, Any

from google.cloud import firestore
from resources.repositories.base import BaseRepository

logger = logging.getLogger(__name__)


class WorkspaceRepository(BaseRepository):
    """ワークスペース設定とチャンネル処理状態の管理"""

    # ----- workspaces コレクション -----

    def get_config(self, team_id: str) -> Optional[Dict[str, Any]]:
        doc = self._collection("workspaces").document(team_id).get()
        if not doc.exists:
            logger.warning(f"ワークスペース設定が見つかりません: {team_id}")
            return None
        return doc.to_dict()

    def save_config(self, team_id: str, data: Dict[str, Any]) -> None:
        self._collection("workspaces").document(team_id).set(data, merge=True)
        logger.info(f"ワークスペース設定保存成功: {team_id}")

    def get_all_workspaces(self):
        """全ワークスペースのドキュメントをストリームで返す"""
        return self._collection("workspaces").stream()

    # ----- workspace_settings コレクション -----

    def get_admin_ids(self, workspace_id: str) -> List[str]:
        doc = self._collection("workspace_settings").document(workspace_id).get()
        if not doc.exists:
            return []
        return doc.to_dict().get("admin_ids", [])

    def save_admin_ids(self, workspace_id: str, admin_ids: List[str]) -> None:
        self._collection("workspace_settings").document(workspace_id).set({
            "workspace_id": workspace_id,
            "admin_ids": admin_ids,
            "updated_at": firestore.SERVER_TIMESTAMP,
        }, merge=True)
        logger.info(f"管理者ID保存成功: Workspace={workspace_id}, Count={len(admin_ids)}")

    def get_settings(self, workspace_id: str) -> Dict[str, Any]:
        doc = self._collection("workspace_settings").document(workspace_id).get()
        if not doc.exists:
            return {"admin_ids": [], "report_channel_id": None, "updated_at": None}
        data = doc.to_dict()
        if "updated_at" in data and data["updated_at"]:
            data["updated_at"] = (
                data["updated_at"].isoformat()
                if hasattr(data["updated_at"], "isoformat")
                else str(data["updated_at"])
            )
        return data

    # ----- channel_history_processed コレクション -----

    def is_channel_processed(self, workspace_id: str, channel_id: str) -> bool:
        doc_id = f"{workspace_id}_{channel_id}"
        doc = self._collection("channel_history_processed").document(doc_id).get()
        return doc.exists

    def mark_channel_processed(self, workspace_id: str, channel_id: str) -> None:
        doc_id = f"{workspace_id}_{channel_id}"
        self._collection("channel_history_processed").document(doc_id).set({
            "workspace_id": workspace_id,
            "channel_id": channel_id,
            "processed_at": firestore.SERVER_TIMESTAMP,
        })
        logger.info(f"チャンネル過去ログ処理済みマーク: {channel_id}")
