# マルチテナント対応リファクタリング完了報告

## 実施日: 2026-01-24

## 概要
Slack勤怠管理Botを、複数のワークスペースに配布できるマルチテナント構成に対応しました。

---

## 1. Firestore 設計の変更

### 新規コレクション: `workspaces`
各ドキュメントIDは `team_id` で、以下のフィールドを持ちます:

```typescript
{
  team_id: string;            // Slackワークスペースの一意ID
  team_name: string;          // ワークスペース名
  bot_token: string;          // Bot User OAuth Token
  report_channel_id: string;  // レポート送信先チャンネルID（オプション）
  installed_at: Timestamp;    // インストール日時
  updated_at: Timestamp;      // 更新日時
}
```

### 既存コレクション: `attendance`
各ドキュメントに `team_id` フィールドが含まれるため、マルチテナント対応済みです。

---

## 2. インフラ層の修正

### `resources/clients/slack_client.py`
- **追加**: `get_slack_client(team_id)` 関数
  - Firestore の `workspaces` コレクションから `bot_token` を取得
  - `team_id` に基づいて `WebClient` を動的に生成
  - トークンが見つからない場合は `ValueError` を発生

### `resources/shared/db.py`
- **追加**: `get_workspace_config(team_id)` 関数
  - ワークスペース設定を取得
- **追加**: `save_workspace_config(team_id, team_name, bot_token, report_channel_id)` 関数
  - ワークスペース設定を保存（OAuth インストール時に使用）

---

## 3. リスナー層の修正

### `resources/listeners/attendance_listener.py`
- 全てのハンドラーで `body` から `team_id` を取得
- `get_slack_client(team_id)` を使用して動的に `WebClient` を生成
- `NotificationService` を各リクエストで動的に生成
- 以下のハンドラーを修正:
  - `on_incoming_message`: メッセージ受信
  - `on_update_button_clicked`: 修正ボタン
  - `on_delete_button_clicked`: 取消ボタン
  - `on_delete_confirmed`: 削除確認
  - `on_history_shortcut_triggered`: 履歴表示
  - `on_history_filter_changed`: 履歴フィルタ変更

### `resources/listeners/admin_listener.py`
- `on_admin_settings_shortcut`: レポート設定ショートカット
  - `team_id` を取得して動的に `WebClient` を生成

### `resources/listeners/system_listener.py`
- `on_bot_joined_channel`: Bot参加イベント
  - `team_id` を取得して動的に `WebClient` を生成

---

## 4. サービス層の修正

### `resources/services/notification_service.py`
- `send_daily_report()` メソッド:
  - 環境変数 `REPORT_CHANNEL_ID` を削除
  - `get_workspace_config(workspace_id)` から `report_channel_id` を取得
  - `report_channel_id` が未設定の場合は、Bot が参加している全チャンネルに送信
  - `workspace_id` を必須パラメータに変更

### `resources/services/report_service.py`
- **非推奨化**: このファイルは旧バージョンとしてマーク
- マルチテナント対応後は `notification_service.py` を使用するよう警告を追加

---

## 5. アプリ初期化の修正

### `resources/main.py`
- **App 初期化**: `token` パラメータを削除、`signing_secret` のみ使用
  ```python
  app = App(
      signing_secret=os.environ.get("SLACK_SIGNING_SECRET"),
      process_before_response=False
      
  )
  ```

- **サービス初期化**: `NotificationService` の初期化を削除
  - 各リクエストで動的に生成するように変更

- **レポート送信 (`/job/report`)**: 
  - 全ワークスペースをループして、それぞれにレポートを送信
  - `get_slack_client(workspace_id)` を使用して動的に `WebClient` を取得

- **OAuth エンドポイント**:
  - `/oauth/install`: インストールURLを表示
  - `/oauth/callback`: OAuth コールバックを処理し、`workspaces` コレクションに保存

---

## 6. 環境変数の削除

### `resources/constants.py`
以下の環境変数を削除:
- `SLACK_BOT_TOKEN` → `workspaces` コレクションから取得
- `REPORT_CHANNEL_ID` → `workspaces` コレクションから取得
- `SLACK_WORKSPACE_ID` → リクエストの `team_id` から取得

残された環境変数:
- `SLACK_SIGNING_SECRET`: 署名検証用（必須）
- `SLACK_CLIENT_ID`: OAuth用
- `SLACK_CLIENT_SECRET`: OAuth用
- `OPENAI_API_KEY`: AI機能用
- `ENABLE_CHANNEL_NLP`: 機能フラグ
- `ATTENDANCE_CHANNEL_ID`: AI解析対象チャンネル制限（オプション）

---

## 7. OAuth インストールハンドラーの追加

### `resources/handlers/oauth_handler.py`
- **追加**: `handle_oauth_redirect(request)` 関数
  - OAuth コールバックを処理
  - `bot_token` を取得して `workspaces` コレクションに保存

- **追加**: `get_oauth_install_url()` 関数
  - OAuth インストールURL を生成

---

## 8. 必要な環境変数（更新後）

### 必須
- `SLACK_SIGNING_SECRET`: リクエスト署名検証用
- `SLACK_CLIENT_ID`: OAuth用
- `SLACK_CLIENT_SECRET`: OAuth用
- `OAUTH_REDIRECT_URI`: OAuth リダイレクト先（例: `https://your-app.run.app/oauth/callback`）
- `OPENAI_API_KEY`: AI機能用

### オプション
- `ENABLE_CHANNEL_NLP`: AI解析の有効/無効（デフォルト: `true`）
- `ATTENDANCE_CHANNEL_ID`: AI解析対象チャンネルの制限
- `ENABLE_PUBSUB`: Pub/Sub機能の有効/無効

---

## 9. デプロイ手順

### Cloud Run へのデプロイ
```bash
gcloud run deploy slack-attendance-bot \
  --source . \
  --platform managed \
  --region asia-northeast1 \
  --allow-unauthenticated \
  --set-env-vars SLACK_SIGNING_SECRET=xxx,SLACK_CLIENT_ID=xxx,SLACK_CLIENT_SECRET=xxx,OAUTH_REDIRECT_URI=https://your-app.run.app/oauth/callback,OPENAI_API_KEY=sk-xxx
```

### Firestore の設定
1. `workspaces` コレクションを作成（自動作成されます）
2. 複合インデックスを作成:
   - `attendance` コレクション: `(workspace_id, date)`
   - `attendance` コレクション: `(workspace_id, user_id)`

### OAuth インストール
1. ブラウザで `https://your-app.run.app/oauth/install` にアクセス
2. 「Add to Slack」ボタンをクリック
3. ワークスペースを選択してインストール
4. リダイレクト後、`workspaces` コレクションにトークンが保存される

---

## 10. 動作確認

### 確認項目
- [ ] OAuth インストールが成功する
- [ ] `workspaces` コレクションにトークンが保存される
- [ ] メッセージから勤怠記録が登録される
- [ ] 修正・削除ボタンが動作する
- [ ] 履歴表示が動作する
- [ ] レポート設定が動作する
- [ ] 日次レポートが送信される（全ワークスペース）

---

## 11. 既存データの移行

既存の勤怠データ（`attendance` コレクション）は、すでに `workspace_id` フィールドを持っているため、移行は不要です。

ただし、以下の作業が必要です:
1. 既存のワークスペースの `bot_token` を `workspaces` コレクションに手動で登録
2. 既存の環境変数（`SLACK_BOT_TOKEN` など）を削除

### 手動登録の例（Firestore コンソールから）
```
コレクション: workspaces
ドキュメントID: T01234567 (既存の team_id)
フィールド:
  - team_id: T01234567
  - team_name: "既存のワークスペース名"
  - bot_token: "xoxb-..."
  - report_channel_id: "C01234567"
  - installed_at: (現在時刻)
  - updated_at: (現在時刻)
```

---

## 12. 注意事項

### セキュリティ
- `bot_token` は Firestore に保存されるため、Firestore のアクセス権限を適切に設定してください
- OAuth リダイレクトURLは、Slack App の設定と一致させてください

### パフォーマンス
- 各リクエストで `get_slack_client()` を呼び出すため、Firestore への読み取りが増加します
- 必要に応じてキャッシュを実装してください（例: Redis）

### 制限事項
- Pub/Sub 機能は現時点ではマルチテナント対応が不完全です
- 旧バージョンの `report_service.py` は使用しないでください

---

## 13. 今後の改善案

1. **キャッシュの実装**: `bot_token` を Redis にキャッシュしてパフォーマンスを向上
2. **OAuth フロー の改善**: State パラメータの検証を追加
3. **アンインストール処理**: アプリがアンインストールされたときに `workspaces` コレクションから削除
4. **管理画面**: インストール済みワークスペースの一覧表示
5. **Pub/Sub のマルチテナント対応**: `team_id` を Pub/Sub メッセージに含める

---

## 14. まとめ

マルチテナント対応が完了しました。これにより、以下のメリットが得られます:

✅ 複数のワークスペースに同時配布可能  
✅ 各ワークスペースのトークンを Firestore で管理  
✅ 環境変数への依存を最小化  
✅ OAuth インストールフローをサポート  
✅ ワークスペースごとに独立した設定を保持  

次のステップは、Slack App Directory への公開準備です。
