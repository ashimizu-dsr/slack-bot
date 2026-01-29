"""
グループ管理サービス

このモジュールは、動的なグループ（課）の管理機能を提供します。
v2.0で追加された機能です。
"""

import os
import uuid
import logging
from typing import List, Dict, Any, Optional
from google.cloud import firestore

from resources.shared.errors import ValidationError
from resources.constants import get_collection_name

logger = logging.getLogger(__name__)


class GroupService:
    """
    グループ（課）を管理するサービスクラス。
    
    Firestoreの groups/{workspace_id}/groups/{group_id} にアクセスし、
    動的なグループ管理機能を提供します。
    """

    def __init__(self):
        """グループサービスの初期化"""
        self.db = firestore.Client(database=os.getenv("FIRESTORE_DB_ID"))

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
                    "admin_ids": ["U100", "U101"],
                    "created_at": "2026-01-21T10:00:00",
                    "updated_at": "2026-01-21T10:00:00"
                },
                ...
            ]
        """
        try:
            groups_ref = self.db.collection(get_collection_name("groups")).document(workspace_id).collection(get_collection_name("groups"))
            docs = groups_ref.stream()
            
            groups = []
            for doc in docs:
                data = doc.to_dict()
                
                # 既存グループにadmin_idsが無い場合は空配列で初期化
                if "admin_ids" not in data:
                    logger.warning(f"グループ '{data.get('name')}' にadmin_idsフィールドがないため空配列で初期化します")
                    data["admin_ids"] = []
                    # Firestoreにも保存
                    try:
                        doc.reference.update({"admin_ids": []})
                        logger.info(f"グループ '{data.get('name')}' にadmin_idsフィールドを追加しました")
                    except Exception as update_error:
                        logger.error(f"admin_idsフィールド追加失敗: {update_error}")
                
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
            group_ref = self.db.collection(get_collection_name("groups")).document(workspace_id)\
                                .collection(get_collection_name("groups")).document(group_id)
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
        admin_ids: List[str] = None,
        created_by: str = None
    ) -> str:
        """
        新しいグループを作成します。
        
        Args:
            workspace_id: Slackワークスペースの一意ID
            name: グループ名
            member_ids: 初期メンバーのユーザーID配列（省略時は空配列）
            admin_ids: 管理者（通知先）のユーザーID配列（省略時は空配列）
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
            group_ref = self.db.collection(get_collection_name("groups")).document(workspace_id)\
                                .collection(get_collection_name("groups")).document(group_id)
            
            data = {
                "group_id": group_id,
                "name": name.strip(),
                "member_ids": member_ids or [],
                "admin_ids": admin_ids or [],
                "created_at": firestore.SERVER_TIMESTAMP,
                "updated_at": firestore.SERVER_TIMESTAMP
            }
            
            if created_by:
                data["created_by"] = created_by
            
            group_ref.set(data)
            logger.info(f"グループ作成成功: {group_id}, Name={name}, Members={len(member_ids or [])}, Admins={len(admin_ids or [])}")
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
            group_ref = self.db.collection(get_collection_name("groups")).document(workspace_id)\
                                .collection(get_collection_name("groups")).document(group_id)
            
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

    def update_group_admins(self, workspace_id: str, group_id: str, admin_ids: List[str]) -> None:
        """
        グループの管理者（通知先）を更新します。
        
        Args:
            workspace_id: Slackワークスペースの一意ID
            group_id: グループの一意ID
            admin_ids: 新しい管理者のユーザーID配列
            
        Raises:
            ValidationError: グループが存在しない場合
        """
        try:
            group_ref = self.db.collection(get_collection_name("groups")).document(workspace_id)\
                                .collection(get_collection_name("groups")).document(group_id)
            
            # 存在確認
            if not group_ref.get().exists:
                raise ValidationError(
                    f"グループが存在しません: {group_id}",
                    "⚠️ 指定されたグループが見つかりません。"
                )
            
            group_ref.update({
                "admin_ids": admin_ids,
                "updated_at": firestore.SERVER_TIMESTAMP
            })
            logger.info(f"グループ管理者更新成功: {group_id}, Admins={len(admin_ids)}")
        except ValidationError:
            raise
        except Exception as e:
            logger.error(f"グループ管理者更新失敗: {e}", exc_info=True)
            raise
    
    def update_group(self, workspace_id: str, group_id: str, name: str, member_ids: List[str], admin_ids: List[str]) -> None:
        """
        グループの名前、メンバー、管理者を一括更新します。
        
        Args:
            workspace_id: Slackワークスペースの一意ID
            group_id: グループの一意ID
            name: 新しいグループ名
            member_ids: 新しいメンバーのユーザーID配列
            admin_ids: 新しい管理者のユーザーID配列
            
        Raises:
            ValidationError: グループが存在しない場合、またはグループ名が空の場合
        """
        if not name or not name.strip():
            raise ValidationError("グループ名が空です", "⚠️ グループ名を入力してください。")
        
        try:
            group_ref = self.db.collection(get_collection_name("groups")).document(workspace_id)\
                                .collection(get_collection_name("groups")).document(group_id)
            
            # 存在確認
            if not group_ref.get().exists:
                raise ValidationError(
                    f"グループが存在しません: {group_id}",
                    "⚠️ 指定されたグループが見つかりません。"
                )
            
            group_ref.update({
                "name": name.strip(),
                "member_ids": member_ids,
                "admin_ids": admin_ids,
                "updated_at": firestore.SERVER_TIMESTAMP
            })
            logger.info(f"グループ更新成功: {group_id}, Name={name}, Members={len(member_ids)}, Admins={len(admin_ids)}, admin_ids={admin_ids}")
        except ValidationError:
            raise
        except Exception as e:
            logger.error(f"グループ更新失敗: {e}", exc_info=True)
            raise

    def update_group_name(self, workspace_id: str, group_id: str, name: str) -> None:
        """
        グループ名を更新します（v2.22で追加）。
        
        Args:
            workspace_id: Slackワークスペースの一意ID
            group_id: グループの一意ID
            name: 新しいグループ名
            
        Raises:
            ValidationError: グループが存在しない場合、またはグループ名が空の場合
        """
        if not name or not name.strip():
            raise ValidationError("グループ名が空です", "⚠️ グループ名を入力してください。")
        
        try:
            group_ref = self.db.collection(get_collection_name("groups")).document(workspace_id)\
                                .collection(get_collection_name("groups")).document(group_id)
            
            # 存在確認
            if not group_ref.get().exists:
                raise ValidationError(
                    f"グループが存在しません: {group_id}",
                    "⚠️ 指定されたグループが見つかりません。"
                )
            
            group_ref.update({
                "name": name.strip(),
                "updated_at": firestore.SERVER_TIMESTAMP
            })
            logger.info(f"グループ名更新成功: {group_id}, Name={name}")
        except ValidationError:
            raise
        except Exception as e:
            logger.error(f"グループ名更新失敗: {e}", exc_info=True)
            raise

    def find_group_by_name(self, workspace_id: str, name: str) -> Optional[Dict[str, Any]]:
        """
        グループ名でグループを検索します（v2.1で追加）。
        
        Args:
            workspace_id: Slackワークスペースの一意ID
            name: グループ名（完全一致、大文字小文字区別あり）
            
        Returns:
            グループ情報の辞書（存在しない場合はNone）
            
        Note:
            - グループ名の前後の空白はトリムしてから検索してください
            - Firestoreインデックスが必要です（初回実行時に自動作成URLが表示されます）
        """
        if not name or not name.strip():
            return None
        
        try:
            groups_ref = self.db.collection(get_collection_name("groups")).document(workspace_id)\
                                .collection(get_collection_name("groups"))
            query = groups_ref.where("name", "==", name.strip()).limit(1)
            docs = list(query.stream())
            
            if not docs:
                logger.info(f"グループが見つかりません: {name}")
                return None
            
            group = docs[0].to_dict()
            logger.info(f"グループ検索成功: {name}, ID={group.get('group_id')}")
            return group
        except Exception as e:
            logger.error(f"グループ検索失敗: {e}", exc_info=True)
            return None

    def delete_group(self, workspace_id: str, group_id: str) -> None:
        """
        グループを削除します（v2.22で実装）。
        
        Args:
            workspace_id: Slackワークスペースの一意ID
            group_id: グループの一意ID（UUID）
            
        Note:
            v2.22で正式実装されました。
        """
        try:
            group_ref = self.db.collection(get_collection_name("groups")).document(workspace_id)\
                                .collection(get_collection_name("groups")).document(group_id)
            
            # 存在確認
            if not group_ref.get().exists:
                logger.warning(f"削除対象グループが存在しません（スキップ）: {group_id}")
                return
            
            group_ref.delete()
            logger.info(f"グループ削除成功: {group_id}")
        except Exception as e:
            logger.error(f"グループ削除失敗: {e}", exc_info=True)
            raise

    # ==========================================
    # v2.2: name-as-id方式のメソッド群
    # ==========================================

    def create_group_with_name_as_id(
        self, 
        workspace_id: str, 
        name: str, 
        member_ids: List[str] = None, 
        admin_ids: List[str] = None,
        created_by: str = None
    ) -> str:
        """
        グループ名をドキュメントIDとして使用してグループを作成します（v2.2で追加）。
        
        Args:
            workspace_id: Slackワークスペースの一意ID
            name: グループ名（ドキュメントIDとして使用）
            member_ids: 初期メンバーのユーザーID配列
            admin_ids: 管理者（通知先）のユーザーID配列（省略時は空配列）
            created_by: 作成者のユーザーID（省略可能）
            
        Returns:
            作成されたグループのgroup_id（= sanitized name）
            
        Raises:
            ValidationError: グループ名が空、または既に存在する場合
            
        Note:
            - v2.1のcreate_group()とは異なり、UUIDを生成せずnameをそのまま使用
            - グループ名はsanitize_group_name()でサニタイズされます
            - 重複チェックは呼び出し側で行うことを推奨
        """
        from resources.shared.utils import sanitize_group_name
        
        if not name or not name.strip():
            raise ValidationError("グループ名が空です", "⚠️ グループ名を入力してください。")
        
        sanitized_name = sanitize_group_name(name.strip())
        
        if not sanitized_name:
            raise ValidationError("グループ名が無効です", "⚠️ 有効なグループ名を入力してください。")
        
        try:
            group_ref = self.db.collection(get_collection_name("groups")).document(workspace_id)\
                                .collection(get_collection_name("groups")).document(sanitized_name)
            
            # 既存チェック
            if group_ref.get().exists:
                raise ValidationError(
                    f"グループ名が既に存在します: {sanitized_name}",
                    f"⚠️ グループ名「{sanitized_name}」は既に存在します。"
                )
            
            data = {
                "group_id": sanitized_name,
                "name": sanitized_name,
                "member_ids": member_ids or [],
                "admin_ids": admin_ids or [],
                "created_at": firestore.SERVER_TIMESTAMP,
                "updated_at": firestore.SERVER_TIMESTAMP
            }
            
            if created_by:
                data["created_by"] = created_by
            
            group_ref.set(data)
            logger.info(f"グループ作成成功(v2.2): {sanitized_name}, Members={len(member_ids or [])}, Admins={len(admin_ids or [])}")
            return sanitized_name
        except ValidationError:
            raise
        except Exception as e:
            logger.error(f"グループ作成失敗(v2.2): {e}", exc_info=True)
            raise

    def update_group_with_name_as_id(
        self, 
        workspace_id: str, 
        group_id: str, 
        member_ids: List[str],
        admin_ids: List[str] = None
    ) -> None:
        """
        グループのメンバーと管理者を更新します（v2.2版、group_id = グループ名）。
        
        Args:
            workspace_id: Slackワークスペースの一意ID
            group_id: グループ名（ドキュメントID）
            member_ids: 新しいメンバーのユーザーID配列
            admin_ids: 新しい管理者のユーザーID配列（省略時は更新しない）
            
        Raises:
            ValidationError: グループが存在しない場合
        """
        try:
            group_ref = self.db.collection(get_collection_name("groups")).document(workspace_id)\
                                .collection(get_collection_name("groups")).document(group_id)
            
            # 存在確認
            if not group_ref.get().exists:
                raise ValidationError(
                    f"グループが存在しません: {group_id}",
                    "⚠️ 指定されたグループが見つかりません。"
                )
            
            update_data = {
                "member_ids": member_ids,
                "updated_at": firestore.SERVER_TIMESTAMP
            }
            
            if admin_ids is not None:
                update_data["admin_ids"] = admin_ids
            
            group_ref.update(update_data)
            logger.info(f"グループ更新成功(v2.2): {group_id}, Members={len(member_ids)}, Admins={len(admin_ids) if admin_ids is not None else 'unchanged'}")
        except ValidationError:
            raise
        except Exception as e:
            logger.error(f"グループ更新失敗(v2.2): {e}", exc_info=True)
            raise

    def delete_group_with_name_as_id(self, workspace_id: str, group_id: str) -> None:
        """
        グループを削除します（v2.2で実装）。
        
        Args:
            workspace_id: Slackワークスペースの一意ID
            group_id: グループ名（ドキュメントID）
            
        Raises:
            ValidationError: グループが存在しない場合
        """
        try:
            group_ref = self.db.collection(get_collection_name("groups")).document(workspace_id)\
                                .collection(get_collection_name("groups")).document(group_id)
            
            # 存在確認
            if not group_ref.get().exists:
                logger.warning(f"削除対象グループが存在しません（スキップ）: {group_id}")
                return
            
            group_ref.delete()
            logger.info(f"グループ削除成功(v2.2): {group_id}")
        except Exception as e:
            logger.error(f"グループ削除失敗(v2.2): {e}", exc_info=True)
            raise
