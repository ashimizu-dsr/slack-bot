"""
ワークスペース設定サービス

このモジュールは、ワークスペース全体の設定（管理者など）を管理します。
v2.0で追加された機能です。
"""
import logging
from typing import List, Dict, Any
from google.cloud import firestore

from resources.shared.errors import ValidationError
from resources.constants import get_collection_name, APP_ENV

logger = logging.getLogger(__name__)


class WorkspaceService:
    """
    ワークスペース設定を管理するサービスクラス。
    
    Firestoreの workspace_settings/{workspace_id} にアクセスし、
    管理者（レポート受信者）などの設定を提供します。
    """

    def __init__(self):
        """ワークスペースサービスの初期化"""
        # 空文字列チェック
        db_name = APP_ENV.strip() if APP_ENV and APP_ENV.strip() else "develop"
        self.db = firestore.Client(database=db_name)
        logger.info(f"WorkspaceService initialized with database: {db_name}")

    def get_admin_ids(self, workspace_id: str) -> List[str]:
        """
        ワークスペースの管理者IDを取得します。
        
        Args:
            workspace_id: Slackワークスペースの一意ID
            
        Returns:
            管理者のユーザーID配列（設定がない場合は空配列）
        """
        try:
            doc = self.db.collection(get_collection_name("workspace_settings")).document(workspace_id).get()
            
            if not doc.exists:
                logger.info(f"ワークスペース設定が存在しません: {workspace_id}")
                return []
            
            data = doc.to_dict()
            admin_ids = data.get("admin_ids", [])
            logger.info(f"管理者ID取得成功: Workspace={workspace_id}, Count={len(admin_ids)}")
            return admin_ids
        except Exception as e:
            logger.error(f"管理者ID取得失敗: {e}", exc_info=True)
            return []

    def save_admin_ids(self, workspace_id: str, admin_ids: List[str]) -> None:
        """
        ワークスペースの管理者IDを保存します。
        
        Args:
            workspace_id: Slackワークスペースの一意ID
            admin_ids: 管理者のユーザーID配列
            
        Raises:
            ValidationError: admin_idsが空の場合（少なくとも1人は必要）
        """
        if not admin_ids:
            raise ValidationError(
                "管理者が空です",
                "⚠️ 少なくとも1人の管理者を選択してください。"
            )
        
        try:
            doc_ref = self.db.collection(get_collection_name("workspace_settings")).document(workspace_id)
            
            # 既存ドキュメントの有無に関わらず、mergeでupsert
            doc_ref.set({
                "workspace_id": workspace_id,
                "admin_ids": admin_ids,
                "updated_at": firestore.SERVER_TIMESTAMP
            }, merge=True)
            
            logger.info(f"管理者ID保存成功: Workspace={workspace_id}, Count={len(admin_ids)}")
        except Exception as e:
            logger.error(f"管理者ID保存失敗: {e}", exc_info=True)
            raise

    def get_workspace_settings(self, workspace_id: str) -> Dict[str, Any]:
        """
        ワークスペース設定をすべて取得します。
        
        Args:
            workspace_id: Slackワークスペースの一意ID
            
        Returns:
            ワークスペース設定の辞書:
            {
                "admin_ids": ["U001", "U002"],
                "report_channel_id": "C01234567",
                "updated_at": "2026-01-21T10:00:00"
            }
        """
        try:
            doc = self.db.collection(get_collection_name("workspace_settings")).document(workspace_id).get()
            
            if not doc.exists:
                logger.info(f"ワークスペース設定が存在しません: {workspace_id}")
                return {
                    "admin_ids": [],
                    "report_channel_id": None,
                    "updated_at": None
                }
            
            data = doc.to_dict()
            
            # updated_atをISO形式に変換
            if "updated_at" in data and data["updated_at"]:
                data["updated_at"] = data["updated_at"].isoformat() if hasattr(data["updated_at"], 'isoformat') else str(data["updated_at"])
            
            logger.info(f"ワークスペース設定取得成功: {workspace_id}")
            return data
        except Exception as e:
            logger.error(f"ワークスペース設定取得失敗: {e}", exc_info=True)
            return {
                "admin_ids": [],
                "report_channel_id": None,
                "updated_at": None
            }
