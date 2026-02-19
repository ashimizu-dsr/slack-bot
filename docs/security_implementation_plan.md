# セキュリティ対策実装プラン

## Context
Cloud Run 上の Slack Bot に外部から不正アクセスできる HTTP エンドポイントが存在するため、
OIDC トークン検証とドメイン制限を実装する。

---

## 実装順序（依存関係順）

```
1. resources/shared/errors.py      → DomainNotAllowedError を追加
2. resources/shared/auth.py        → 新規作成（verify_oidc_token 関数）
3. requirements.txt                → google-auth, requests を明示追加
4. resources/main.py               → 全3エンドポイントに適用
```

---

## Step 1: `resources/shared/errors.py`

既存の `AuthorizationError` クラスの直後に以下を追加。

```python
class DomainNotAllowedError(AttendanceBotError):
    """
    許可されていないメールドメインからの OAuth インストール試行時に発生。
    例: ALLOWED_DOMAIN="example.com" だがユーザーが "user@other.com" の場合
    """
    pass
```

---

## Step 2: `resources/shared/auth.py`（新規作成）

```python
"""共通認証モジュール - Cloud Run エンドポイント用 OIDC トークン検証"""
import logging
import google.auth.transport.requests
import google.oauth2.id_token
from resources.constants import APP_ENV
from resources.shared.errors import AuthorizationError

logger = logging.getLogger(__name__)

_AUDIENCE_MAP = {
    "production": "https://slack-kintai-bot-prod-478434513686.asia-northeast1.run.app",
    "develop":    "https://slack-kintai-bot-dev-478434513686.asia-northeast1.run.app",
}

def verify_oidc_token(request) -> None:
    """
    Authorization: Bearer <token> ヘッダーの OIDC トークンを Google 公開鍵で検証する。
    失敗時は AuthorizationError を raise。
    """
    auth_header = request.headers.get("Authorization", "")
    if not auth_header or not auth_header.startswith("Bearer "):
        raise AuthorizationError("Missing or malformed Authorization header")

    token = auth_header[len("Bearer "):]
    audience = _AUDIENCE_MAP.get(APP_ENV, _AUDIENCE_MAP["develop"])

    try:
        transport = google.auth.transport.requests.Request()
        google.oauth2.id_token.verify_oauth2_token(token, transport, audience=audience)
        logger.info(f"[OIDC] Token verified (audience={audience})")
    except Exception as e:
        logger.warning(f"[OIDC] Token verification failed: {e}")
        raise AuthorizationError(f"OIDC token verification failed: {e}")
```

---

## Step 3: `requirements.txt`

以下を追記（`google-cloud-*` の推移的依存に含まれるが明示化する）：

```
# 認証（OIDC トークン検証用）
google-auth>=2.20.0
requests>=2.31.0
```

---

## Step 4: `resources/main.py` の変更

### 4-A: インポート追加（モジュール先頭付近）

```python
from resources.shared.auth import verify_oidc_token
from resources.shared.errors import AuthorizationError, DomainNotAllowedError
from slack_bolt.oauth.callback_options import CallbackOptions, FailureArgs
from slack_bolt.response import BoltResponse
```

### 4-B: `custom_failure_handler` 関数を追加

`OAuthSettings` 初期化コードの直前に関数を定義：

```python
def custom_failure_handler(args: FailureArgs) -> BoltResponse:
    """OAuth フロー失敗時のレスポンスをカスタマイズする"""
    error = args.error
    # slack-bolt のバージョンによっては原因例外が __cause__ に格納される
    root_cause = getattr(error, "__cause__", None) or getattr(error, "__context__", None) or error

    if isinstance(root_cause, DomainNotAllowedError) or isinstance(error, DomainNotAllowedError):
        status = 403
        title = "インストール拒否"
        message = root_cause.user_message if isinstance(root_cause, DomainNotAllowedError) else error.user_message
    else:
        status = 500
        title = "インストールエラー"
        message = "予期しないエラーが発生しました。管理者に連絡してください。"
        logger.error(f"[OAuthFailure] Unexpected error: {error}", exc_info=True)

    html = f"""<!DOCTYPE html>
<html lang="ja"><head><meta charset="UTF-8"><title>{title}</title></head>
<body><h1>{title}</h1><p>{message}</p></body></html>"""

    return BoltResponse(
        status=status,
        headers={"Content-Type": "text/html; charset=utf-8"},
        body=html
    )
```

### 4-C: `OAuthSettings` に `callback_options` を追加

```python
oauth_settings = OAuthSettings(
    client_id=client_id,
    client_secret=client_secret,
    scopes=[...],
    installation_store=FirestoreInstallationStore(db_client),
    callback_options=CallbackOptions(failure=custom_failure_handler),  # ← 追加
)
```

### 4-D: `FirestoreInstallationStore.save()` の冒頭にドメインチェックを追加

`team_id = installation.team_id` の前に挿入：

```python
def save(self, installation: Installation) -> None:
    # ── ドメインチェック ──
    allowed_domain = os.environ.get("ALLOWED_DOMAIN", "").strip()
    if allowed_domain:
        try:
            check_client = WebClient(token=installation.bot_token)
            user_info = check_client.users_info(user=installation.user_id)
            email = user_info.get("user", {}).get("profile", {}).get("email", "")
            if not email:
                raise DomainNotAllowedError(
                    f"Email not available for user={installation.user_id}",
                    user_message="メールアドレスを取得できなかったため、インストールを拒否しました。"
                )
            domain = email.split("@")[-1].lower()
            if domain != allowed_domain.lower():
                raise DomainNotAllowedError(
                    f"Domain '{domain}' not allowed (allowed='{allowed_domain}')",
                    user_message=f"ドメイン '{domain}' はインストールが許可されていません。"
                )
            logger.info(f"[DomainCheck] OK: {domain}")
        except DomainNotAllowedError:
            raise  # custom_failure_handler で捕捉させる
        except Exception as e:
            logger.error(f"[DomainCheck] Unexpected error: {e}", exc_info=True)
            raise

    # 以下、既存の Firestore 書き込み処理（変更なし）
    try:
        team_id = installation.team_id
        ...
```

### 4-E: `/job/report` エンドポイントの変更

```python
if path == "/job/report":
    # POST のみ受け付ける
    if request.method != "POST":
        return {"status": "error", "message": "Method Not Allowed"}, 405

    # OIDC トークン検証
    try:
        verify_oidc_token(request)
    except AuthorizationError as e:
        logger.warning(f"[/job/report] Unauthorized: {e}")
        return {"status": "error", "message": "Unauthorized"}, 401

    # 既存の処理（変更なし）
    try:
        ...
```

### 4-F: `/pubsub/interactions` エンドポイントの変更

```python
if path == "/pubsub/interactions":
    # OIDC トークン検証
    try:
        verify_oidc_token(request)
    except AuthorizationError as e:
        logger.warning(f"[/pubsub/interactions] Unauthorized: {e}")
        return {"status": "error", "message": "Unauthorized"}, 401

    # 既存の Pub/Sub 処理（変更なし）
    try:
        ...
```

---

## 変更ファイルのまとめ

| ファイル | 変更 | 主な内容 |
|---|---|---|
| `resources/shared/errors.py` | 追加 | `DomainNotAllowedError` クラス |
| `resources/shared/auth.py` | **新規作成** | `verify_oidc_token()` 関数 |
| `requirements.txt` | 追加 | `google-auth>=2.20.0`, `requests>=2.31.0` |
| `resources/main.py` | 変更 | インポート追加、`custom_failure_handler`、`CallbackOptions`、`save()` ドメインチェック、2エンドポイントの OIDC 検証 |

---

## 動作確認方法

### ① ② OIDC 検証
- Cloud Scheduler / Pub/Sub のサービスアカウントにトークンが付与された状態でリクエスト → 正常処理される
- トークンなしで `curl -X POST /job/report` → 401 が返る
- `GET /job/report` にアクセス → 405 が返る

### ③ ドメイン制限
- `ALLOWED_DOMAIN` に許可するドメインを設定した状態で別ドメインのユーザーがインストール試行 → 403 の HTML ページが表示される
- 同じドメインのユーザーがインストール → 正常にインストール完了
- `ALLOWED_DOMAIN` 未設定 → チェックをスキップして通常インストール
