"""
Repository基底クラス

Firestoreクライアントを注入で受け取り、環境に応じたコレクション参照を提供します。
"""

from google.cloud import firestore
from resources.constants import get_collection_name


class BaseRepository:
    """全Repositoryの基底クラス"""

    def __init__(self, db: firestore.Client):
        self._db = db

    def _collection(self, base_name: str):
        """環境に応じたFirestoreコレクション参照を返す"""
        return self._db.collection(get_collection_name(base_name))
