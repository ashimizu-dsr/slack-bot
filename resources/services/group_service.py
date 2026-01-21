"""
グループ管理サービス

このモジュールは、動的なグループ（課）の管理機能を提供します。
v2.0で追加された機能です。
"""

import uuid
import logging
from typing import List, Dict, Any, Optional
from google.cloud import firestore

from resources.shared.errors import ValidationError

logger = logging.getLogger(__name__)


class GroupService:
    """
    グループ（課）を管理するサービスクラス。
    
    Firestoreの groups/{workspace_id}/groups/{group_id} にアクセスし、
    動的なグループ管理機能を提供します。
    """

    def __init__(self):
        """グループサービスの初期化"""
        self.db = firestore.Client()

    def get_all_groups(self, workspace_id: str) -> List[Dict[str, Any]]:
        """
        ワークスペース内の全グループを取得します。
        
        Args:
            workspace_id: Slackワークスペースの一意ID
            
        Returns:
            グループ情報の配列:
            [
                {
                    "group_id": "group_a1b2c3d4",
                    "name": "営業1課",
                    "member_ids": ["U001", "U002"],
                    "created_at": "2026-01-21T10:00:00",
                    "updated_at": "2026-01-21T10:00:00"
                },
                ...
            ]
        """
        try:
            groups_ref = self.db.collection("groups").document(workspace_id).collection("groups")
            docs = groups_ref.stream()
            
            groups = []
            for doc in docs:
                data = doc.to_dict()
                # created_at, updated_at をISO形式の文字列に変換
                if "created_at" in data and data["created_at"]:
                    data["created_at"] = data["created_at"].isoformat() if hasattr(data["created_at"], 'isoformat') else str(data["created_at"])
                if "updated_at" in data and data["updated_at"]:
                    data["updated_at"] = data["updated_at"].isoformat() if hasattr(data["updated_at"], 'isoformat') else str(data["updated_at"])
                groups.append(data)
            
            logger.info(f"グループ取得成功: Workspace={workspace_id}, Count={len(groups)}")
            return groups
        except Exception as e:
            logger.error(f"グループ取得失敗: {e}", exc_info=True)
            return []

    def get_group_by_id(self, workspace_id: str, group_id: str) -> Optional[Dict[str, Any]]:
        """
        特定のグループを取得します。
        
        Args:
            workspace_id: Slackワークスペースの一意ID
            group_id: グループの一意ID
            
        Returns:
            グループ情報の辞書（存在しない場合はNone）
        """
        try:
            group_ref = self.db.collection("groups").document(workspace_id)\
                                .collection("groups").document(group_id)
            doc = group_ref.get()
            
            if not doc.exists:
                logger.warning(f"グループが存在しません: {group_id}")
                return None
            
            data = doc.to_dict()
            # タイムスタンプをISO形式に変換
            if "created_at" in data and data["created_at"]:
                data["created_at"] = data["created_at"].isoformat() if hasattr(data["created_at"], 'isoformat') else str(data["created_at"])
            if "updated_at" in data and data["updated_at"]:
                data["updated_at"] = data["updated_at"].isoformat() if hasattr(data["updated_at"], 'isoformat') else str(data["updated_at"])
            
            logger.info(f"グループ取得成功: {group_id}")
            return data
        except Exception as e:
            logger.error(f"グループ取得失敗: {e}", exc_info=True)
            return None

    def create_group(
        self, 
        workspace_id: str, 
        name: str, 
        member_ids: List[str] = None, 
        created_by: str = None
    ) -> str:
        """
        新しいグループを作成します。
        
        Args:
            workspace_id: Slackワークスペースの一意ID
            name: グループ名
            member_ids: 初期メンバーのユーザーID配列（省略時は空配列）
            created_by: 作成者のユーザーID（省略可能）
            
        Returns:
            作成されたグループのgroup_id
            
        Raises:
            ValidationError: グループ名が空の場合
        """
        if not name or not name.strip():
            raise ValidationError("グループ名が空です", "⚠️ グループ名を入力してください。")
        
        # TODO: グループ名の重複チェック（v2.1で実装予定）
        
        try:
            group_id = f"group_{uuid.uuid4()}"
            group_ref = self.db.collection("groups").document(workspace_id)\
                                .collection("groups").document(group_id)
            
            data = {
                "group_id": group_id,
                "name": name.strip(),
                "member_ids": member_ids or [],
                "created_at": firestore.SERVER_TIMESTAMP,
                "updated_at": firestore.SERVER_TIMESTAMP
            }
            
            if created_by:
                data["created_by"] = created_by
            
            group_ref.set(data)
            logger.info(f"グループ作成成功: {group_id}, Name={name}, Members={len(member_ids or [])}")
            return group_id
        except Exception as e:
            logger.error(f"グループ作成失敗: {e}", exc_info=True)
            raise

    def update_group_members(self, workspace_id: str, group_id: str, member_ids: List[str]) -> None:
        """
        グループのメンバーを更新します。
        
        Args:
            workspace_id: Slackワークスペースの一意ID
            group_id: グループの一意ID
            member_ids: 新しいメンバーのユーザーID配列
            
        Raises:
            ValidationError: グループが存在しない場合
        """
        try:
            group_ref = self.db.collection("groups").document(workspace_id)\
                                .collection("groups").document(group_id)
            
            # 存在確認
            if not group_ref.get().exists:
                raise ValidationError(
                    f"グループが存在しません: {group_id}",
                    "⚠️ 指定されたグループが見つかりません。"
                )
            
            group_ref.update({
                "member_ids": member_ids,
                "updated_at": firestore.SERVER_TIMESTAMP
            })
            logger.info(f"グループメンバー更新成功: {group_id}, Members={len(member_ids)}")
        except ValidationError:
            raise
        except Exception as e:
            logger.error(f"グループメンバー更新失敗: {e}", exc_info=True)
            raise

    def delete_group(self, workspace_id: str, group_id: str) -> None:
        """
        グループを削除します。
        
        Args:
            workspace_id: Slackワークスペースの一意ID
            group_id: グループの一意ID
            
        Note:
            v2.0では未実装（v2.1で追加予定）
        """
        raise NotImplementedError("グループ削除機能はv2.1で実装予定です。")
