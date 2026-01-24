# OAuth マルチテナント対応 - セットアップガイド

## 📋 概要

この Slack 勤怠管理 Bot は、OAuth を使用したマルチテナント対応に完全に対応しています。
複数のワークスペースに Bot をインストールし、各ワークスペースごとに独立して動作させることができます。

## 🎯 実装内容

### 1. OAuth Flow の実装

- **FirestoreInstallationStore**: `main.py` に実装
  - `slack_bolt.oauth.installation_store.InstallationStore` を継承
  - インストール情報を Firestore の `workspaces` コレクションに保存
  - `team_id` ごとに `bot_token` を管理

- **エンドポイント**:
  - `/slack/install`: インストールページ（Add to Slack ボタン）
  - `/slack/oauth_redirect`: OAuth コールバック（自動処理）

### 2. マルチテナント対応

- 各リスナーで `team_id` を取得
- `get_slack_client(team_id)` で動的に WebClient を生成
- 全ての DB 操作で `workspace_id` を使用

### 3. 依存関係の最適化

`requirements.txt` を以下のように更新しました:

```txt
slack-bolt==1.18.1
slack-sdk==3.26.2
google-cloud-firestore==2.14.0
functions-framework==3.5.0
flask==3.0.0
openai==1.6.1
python-dotenv==1.0.0
```

## 🚀 セットアップ手順

### ステップ 1: Slack App の設定

1. **Basic Information** にアクセス
   - `App ID` をメモ
   - `Client ID` をメモ
   - `Client Secret` をメモ
   - `Signing Secret` をメモ

2. **OAuth & Permissions** を設定
   - **Redirect URLs** に以下を追加:
     ```
     https://your-app.run.app/slack/oauth_redirect
     ```
   
   - **Bot Token Scopes** に以下を追加:
     - `app_mentions:read`
     - `channels:history`
     - `channels:read`
     - `chat:write`
     - `commands`
     - `users:read`
     - `users:read.email`
     - `reactions:write`
     - `im:history`
     - `groups:history`

3. **Event Subscriptions** を設定
   - **Request URL** を設定:
     ```
     https://your-app.run.app/slack/events
     ```
   
   - **Subscribe to bot events** に以下を追加:
     - `message.channels`
     - `message.groups`
     - `message.im`
     - `app_mention`
     - `member_joined_channel`

4. **Interactivity & Shortcuts** を設定
   - **Request URL** を設定:
     ```
     https://your-app.run.app/slack/events
     ```

5. **App Distribution** を有効化
   - **Manage Distribution** > **Activate Public Distribution**
   - これにより、複数のワークスペースにインストール可能になります

### ステップ 2: Google Cloud Run の設定

1. **環境変数** を設定:

```bash
gcloud run services update slack-attendance-bot \
  --region=asia-northeast1 \
  --set-env-vars="SLACK_CLIENT_ID=1234567890.1234567890" \
  --set-env-vars="SLACK_CLIENT_SECRET=abcdef1234567890abcdef1234567890" \
  --set-env-vars="SLACK_SIGNING_SECRET=abcdef1234567890abcdef1234567890abcdef12" \
  --set-env-vars="SLACK_APP_ID=A01234567" \
  --set-env-vars="ENABLE_OAUTH=true" \
  --set-env-vars="OPENAI_API_KEY=sk-..." \
  --set-env-vars="LOG_LEVEL=INFO"
```

2. **デプロイ**:

```bash
gcloud run deploy slack-attendance-bot \
  --source . \
  --region=asia-northeast1 \
  --platform=managed \
  --allow-unauthenticated \
  --memory=512Mi \
  --timeout=60s
```

### ステップ 3: インストール

1. **インストール URL にアクセス**:
   ```
   https://your-app.run.app/slack/install
   ```

2. **「Add to Slack」ボタン** をクリック

3. **権限を確認** して、ワークスペースにインストール

4. **Firestore で確認**:
   - `workspaces` コレクションに新しいドキュメントが作成されます
   - ドキュメント ID は `team_id`
   - `bot_token` が保存されています

## 📊 Firestore データ構造

### workspaces コレクション

各ワークスペースの設定を保存します。

```
workspaces/
  {team_id}/
    team_id: "T01234567"
    team_name: "Example Workspace"
    bot_token: "xoxb-..."
    bot_id: "B01234567"
    bot_user_id: "U01234567"
    enterprise_id: ""
    is_enterprise_install: false
    report_channel_id: "C01234567"  # レポート送信先
    installed_at: Timestamp
    updated_at: Timestamp
```

### attendance コレクション

各勤怠レコードを保存します（マルチテナント対応）。

```
attendance/
  {workspace_id}_{user_id}_{date}/
    workspace_id: "T01234567"
    user_id: "U01234567"
    email: "user@example.com"
    date: "2026-01-24"
    status: "late"
    note: "電車遅延"
    channel_id: "C01234567"
    ts: "1234567890.123456"
    updated_at: Timestamp
```

## 🔍 トラブルシューティング

### インポートエラーが発生する

**原因**: ライブラリがインストールされていない

**解決策**:
```bash
pip install -r requirements.txt
```

### OAuth コールバックが失敗する

**原因**: Redirect URL が正しく設定されていない

**解決策**:
1. Slack App の **OAuth & Permissions** を確認
2. Redirect URLs に正確な URL を追加:
   ```
   https://your-app.run.app/slack/oauth_redirect
   ```

### bot_token が見つからない

**原因**: ワークスペースがインストールされていない

**解決策**:
1. `/slack/install` にアクセスしてインストール
2. Firestore の `workspaces` コレクションを確認

### 複数ワークスペースで動作しない

**原因**: リスナーが `team_id` を取得していない

**解決策**:
- 全リスナーで以下のパターンを使用:
```python
team_id = body.get("team_id") or event.get("team")
dynamic_client = get_slack_client(team_id)
```

## 🎓 コードの解説

### FirestoreInstallationStore

```python
class FirestoreInstallationStore(InstallationStore):
    def save(self, installation: Installation) -> None:
        # インストール情報を Firestore に保存
        team_id = installation.team_id
        self.db.collection("workspaces").document(team_id).set({
            "bot_token": installation.bot_token,
            "team_name": installation.team_name,
            # ...
        })
    
    def find_installation(self, *, team_id: str, ...) -> Installation:
        # Firestore からインストール情報を取得
        doc = self.db.collection("workspaces").document(team_id).get()
        return Installation(bot_token=data["bot_token"], ...)
```

### マルチテナント対応のリスナー

```python
@app.event("message")
def on_incoming_message(event, client, ack, body):
    ack()
    
    # team_id を取得
    team_id = body.get("team_id") or event.get("team")
    
    # 動的に WebClient を生成
    dynamic_client = get_slack_client(team_id)
    
    # 動的に NotificationService を生成
    notification_service = NotificationService(dynamic_client, attendance_service)
    
    # 処理を実行
    execute_attendance_from_message(event, dynamic_client, ...)
```

## ✅ チェックリスト

- [ ] Slack App の OAuth 設定が完了している
- [ ] Redirect URL が正しく設定されている
- [ ] Bot Token Scopes が全て追加されている
- [ ] App Distribution が有効化されている
- [ ] Cloud Run の環境変数が設定されている
- [ ] `ENABLE_OAUTH=true` が設定されている
- [ ] Firestore が有効化されている
- [ ] `/slack/install` にアクセスできる
- [ ] インストール後、Firestore に `workspaces` ドキュメントが作成される
- [ ] 複数ワークスペースでメッセージを受け取れる

## 📝 まとめ

このセットアップにより、以下が実現されます:

1. ✅ **OAuth Flow の完全実装**
   - インストール URL からの導入
   - 自動的な `bot_token` の保存

2. ✅ **マルチテナント対応**
   - 複数ワークスペースでの独立動作
   - `team_id` ベースのデータ分離

3. ✅ **プロダクションレディ**
   - クリーンなコード構造
   - 適切なエラーハンドリング
   - 詳細なログ出力

4. ✅ **スケーラブル**
   - Firestore によるデータ管理
   - Cloud Run による自動スケーリング

---

**作成日**: 2026年1月24日
**バージョン**: 2.0 (OAuth対応)
