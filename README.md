# Slack勤怠管理Bot

**バージョン**: v2.23 (OAuth マルチテナント対応版)  
**最終更新**: 2026-01-24

AI解析機能を備えた、Slack向けの勤怠管理Botです。自然言語での勤怠連絡を自動的に解析し、構造化されたデータとして保存・集計します。

**⭐️ NEW**: OAuth フローを実装し、複数のワークスペースに同時配布できるマルチテナント対応を完了しました！ワンクリックで任意のワークスペースにインストール可能です。

## 主な機能

### 1. AI自動解析による勤怠記録

- Slackメッセージを自然言語で解析（OpenAI API使用）
- 「遅刻します」「有給休暇を取ります」などの表現を自動認識
- ステータス（出勤、遅刻、欠勤、有給休暇など）と備考を抽出
- 打ち消し線によるステータス変更にも対応

### 2. 動的グループ管理（v2.22）

- 課やチームなどのグループを動的に作成・管理
- **グローバルショートカット**: ⚡「レポート設定」からアクセス
- **一覧表示UI**: グループを見やすい一覧形式で表示
- **オーバーフローメニュー**: `...` メニューから個別に編集・削除
- **views.push**: モーダルスタックによるスムーズな画面遷移
- **個別編集**: 各グループを独立したモーダルで編集
- 管理者（レポート受信者）の設定
- Slackモーダルでの直感的な設定UI

### 3. 日次レポート自動配信

- Cloud Schedulerによる定時実行（毎朝9:00推奨）
- グループごとに勤怠状況を集約
- 管理者にDMまたは指定チャンネルに送信

### 4. 履歴表示・編集機能

- ユーザーごとの勤怠履歴をモーダルで表示
- 月別フィルタリング機能
- 過去の記録の編集・削除が可能

### 5. OAuth マルチテナント対応（v2.23）🆕

- **ワンクリックインストール**: Add to Slack ボタンから簡単にインストール
- **OAuth Flow の完全実装**: 自動的に bot_token を Firestore に保存
- **複数ワークスペース対応**: 無制限のワークスペースに同時配布可能
- **独立動作**: 各ワークスペースのデータを完全に分離
- **動的トークン管理**: リクエストごとに適切な bot_token を自動取得

## アーキテクチャ

### 技術スタック

- **プラットフォーム**: Google Cloud Run / Cloud Functions
- **フレームワーク**: Slack Bolt for Python
- **データベース**: Google Cloud Firestore
- **AI**: OpenAI API (GPT-4o-mini)
- **言語**: Python 3.11+

### ディレクトリ構成

```
slack-attendance-bot/
├── resources/
│   ├── clients/            # 外部API クライアント
│   │   └── slack_client.py       # Slack Web API ラッパー
│   ├── handlers/           # Slack イベントのルーティング
│   │   └── oauth_handler.py      # OAuth 処理（参考用）
│   ├── listeners/          # Slack イベントリスナー
│   │   ├── __init__.py           # リスナー登録
│   │   ├── attendance_listener.py # 勤怠操作
│   │   ├── admin_listener.py     # 管理機能
│   │   └── system_listener.py    # システムイベント
│   ├── services/           # ビジネスロジック
│   │   ├── attendance_service.py # 勤怠データ管理
│   │   ├── nlp_service.py        # AI 解析
│   │   ├── notification_service.py # 通知送信
│   │   ├── group_service.py      # グループ管理
│   │   ├── workspace_service.py  # ワークスペース設定
│   │   └── report_service.py     # レポート生成
│   ├── templates/          # UI（Block Kit）テンプレート
│   │   ├── cards.py              # メッセージカード
│   │   └── modals.py             # モーダル UI
│   ├── shared/             # 共通ユーティリティ
│   │   ├── db.py                 # Firestore 操作
│   │   ├── errors.py             # エラー定義
│   │   ├── setup_logger.py       # ロガー設定
│   │   └── utils.py              # ユーティリティ関数
│   ├── constants.py        # 定数定義
│   └── main.py            # エントリポイント（OAuth 対応）
├── docs/                   # ドキュメント
│   ├── oauth_setup_guide.md      # OAuth セットアップガイド（v2.23）🆕
│   ├── spec_v2.22.md             # v2.22 設計書
│   └── ...                       # その他仕様書
├── deploy.sh               # デプロイスクリプト（Linux/Mac）🆕
├── deploy.ps1              # デプロイスクリプト（Windows）🆕
├── env.sample              # 環境変数サンプル 🆕
├── requirements.txt        # Python 依存関係（OAuth 対応）
└── README.md              # このファイル
```

### データモデル

#### Firestore コレクション

##### 1. `workspaces/{team_id}` (v2.23 - OAuth 対応) 🆕

各ワークスペースの OAuth トークンと設定を格納します。

```json
{
  "team_id": "T01234567",
  "team_name": "Example Workspace",
  "bot_token": "xoxb-...",
  "bot_id": "B01234567",
  "bot_user_id": "U01234567",
  "enterprise_id": "",
  "is_enterprise_install": false,
  "report_channel_id": "C01234567",
  "installed_at": "2026-01-24T10:00:00Z",
  "updated_at": "2026-01-24T10:00:00Z"
}
```

##### 2. `attendance/{workspace_id}_{user_id}_{date}`

個々の勤怠記録を格納します（マルチテナント対応）。

```json
{
  "workspace_id": "T01234567",
  "user_id": "U01234567",
  "email": "user@example.com",
  "date": "2026-01-24",
  "status": "late",
  "note": "電車遅延",
  "channel_id": "C01234567",
  "ts": "1705812345.123456",
  "updated_at": "2026-01-24T08:30:00Z"
}
```

##### 3. `groups/{workspace_id}/groups/{group_id}`

動的グループ情報を格納します。

```json
{
  "group_id": "営業1課",
  "name": "営業1課",
  "member_ids": ["U001", "U002", "U003"],
  "created_at": "2026-01-24T10:00:00Z",
  "updated_at": "2026-01-24T10:00:00Z"
}
```

##### 4. `workspace_settings/{workspace_id}`

ワークスペース設定を格納します。

```json
{
  "workspace_id": "T01234567",
  "admin_ids": ["U001", "U002"],
  "report_channel_id": "C01234567",
  "updated_at": "2026-01-24T10:00:00Z"
}
```

## セットアップ

### クイックスタート（推奨）

詳細なセットアップ手順は、**[OAuth セットアップガイド](docs/oauth_setup_guide.md)** を参照してください。

以下は簡易版の手順です。

### 1. 前提条件

- Google Cloud プロジェクト
- Slack App の作成（OAuth 対応）
- OpenAI API キー
- Python 3.11+ または 3.12

### 2. Slack App の OAuth 設定

#### 必要なスコープ（Bot Token Scopes）

- `app_mentions:read` - アプリへのメンションを受信
- `channels:history` - チャンネルのメッセージを読む
- `channels:read` - チャンネル情報を取得
- `chat:write` - メッセージを送信
- `commands` - スラッシュコマンド（オプション）
- `users:read` - ユーザー情報を取得
- `users:read.email` - ユーザーのメールアドレスを取得
- `reactions:write` - リアクションを追加
- `im:history` - DM のメッセージを読む
- `groups:history` - プライベートチャンネルのメッセージを読む

#### OAuth & Permissions の設定

**Redirect URLs** に以下を追加:
```
https://your-app.run.app/slack/oauth_redirect
```

#### Event Subscriptions の設定

**Request URL**:
```
https://your-app.run.app/slack/events
```

**Subscribe to bot events**:
- `message.channels`
- `message.groups`
- `message.im`
- `app_mention`
- `member_joined_channel`

#### Interactivity & Shortcuts の設定

**Request URL**:
```
https://your-app.run.app/slack/events
```

**Global Shortcuts**:
- `open_history_modal`: 履歴を表示
- `open_member_setup_modal`: レポート設定

#### App Distribution の有効化

**Manage Distribution** > **Activate Public Distribution** を有効にしてください。

### 3. 環境変数の設定

`env.sample` を参考に、以下の環境変数を設定してください。

#### 必須

```bash
# Slack OAuth 設定
SLACK_CLIENT_ID=1234567890.1234567890
SLACK_CLIENT_SECRET=abcdef1234567890abcdef1234567890
SLACK_SIGNING_SECRET=abcdef1234567890abcdef1234567890abcdef12
SLACK_APP_ID=A01234567

# OAuth 有効化
ENABLE_OAUTH=true

# OpenAI API
OPENAI_API_KEY=sk-your-openai-api-key

# アプリケーション環境（production または develop）
# 注意: この環境変数は Cloud Run で手動設定してください
APP_ENV=production  # 本番環境の場合
# APP_ENV=develop   # 開発環境の場合
```

**重要**: `APP_ENV`は各Cloud Runサービスで**個別に手動設定**してください。
`cloudbuild.yaml`では設定されないため、以下のコマンドで設定する必要があります：

```bash
# 本番環境（slack-kintai-bot-prod）の場合
gcloud run services update slack-kintai-bot-prod \
  --region=asia-northeast1 \
  --set-env-vars APP_ENV=production

# 開発環境（slack-kintai-bot-dev）の場合
gcloud run services update slack-kintai-bot-dev \
  --region=asia-northeast1 \
  --set-env-vars APP_ENV=develop
```

これにより、本番環境は`production`データベース、開発環境は`develop`データベースに接続します。

#### オプション

```bash
# Pub/Sub 機能の有効/無効（デフォルト: false）
ENABLE_PUBSUB=false

# ログレベル
LOG_LEVEL=INFO
```

#### 削除された環境変数（OAuth 対応により不要）

以下の環境変数は使用されなくなりました:
- ~~`SLACK_BOT_TOKEN`~~ → OAuth で自動取得、Firestore に保存
- ~~`REPORT_CHANNEL_ID`~~ → Firestore の `workspaces` コレクションから取得
- ~~`SLACK_WORKSPACE_ID`~~ → リクエストの `team_id` から自動取得
- ~~`OAUTH_REDIRECT_URI`~~ → `/slack/oauth_redirect` に固定

### 4. デプロイ

#### 方法 1: デプロイスクリプトを使用（推奨）

1. `deploy.ps1`（Windows）または `deploy.sh`（Linux/Mac）を編集
2. 環境変数を設定
3. スクリプトを実行

**Windows:**
```powershell
.\deploy.ps1
```

**Linux/Mac:**
```bash
chmod +x deploy.sh
./deploy.sh
```

#### 方法 2: 手動デプロイ

```bash
# 1. Google Cloud にログイン
gcloud auth login

# 2. プロジェクトを設定
gcloud config set project YOUR_PROJECT_ID

# 3. 必要な API を有効化
gcloud services enable run.googleapis.com
gcloud services enable cloudbuild.googleapis.com
gcloud services enable firestore.googleapis.com

# 4. Cloud Run にデプロイ
gcloud run deploy slack-attendance-bot \
  --source . \
  --region asia-northeast1 \
  --platform managed \
  --allow-unauthenticated \
  --memory=512Mi \
  --timeout=60s \
  --set-env-vars="SLACK_CLIENT_ID=..." \
  --set-env-vars="SLACK_CLIENT_SECRET=..." \
  --set-env-vars="SLACK_SIGNING_SECRET=..." \
  --set-env-vars="SLACK_APP_ID=..." \
  --set-env-vars="ENABLE_OAUTH=true" \
  --set-env-vars="OPENAI_API_KEY=sk-..." \
  --set-env-vars="LOG_LEVEL=INFO"
```

#### インストール手順

1. **Slack App の設定を更新**
   - デプロイ後の URL を、Slack App の各設定に追加
   - Redirect URLs: `https://your-app.run.app/slack/oauth_redirect`
   - Request URL: `https://your-app.run.app/slack/events`

2. **インストール URL にアクセス**
   ```
   https://your-app.run.app/slack/install
   ```

3. **「Add to Slack」ボタンをクリック**
   - ワークスペースを選択
   - 権限を確認してインストール

4. **Firestore で確認**
   - `workspaces` コレクションに新しいドキュメントが作成されます
   - ドキュメント ID は `team_id`
   - `bot_token` が自動的に保存されます

#### 複数ワークスペースへのインストール

同じインストール URL を使用して、任意の数のワークスペースにインストールできます。
各ワークスペースのトークンは Firestore に独立して保存されます。

### 5. Cloud Schedulerの設定（日次レポート）

```bash
gcloud scheduler jobs create http daily-report-job \
  --schedule="0 9 * * *" \
  --uri="https://your-cloud-run-url/job/report" \
  --http-method=GET \
  --time-zone="Asia/Tokyo" \
  --location=asia-northeast1
```

## 使い方

### 勤怠連絡の投稿

Botが参加しているチャンネルで、自然言語でメッセージを送信してください。

**例**:
- 「おはようございます」「出勤します」 → **通常出勤**
- 「遅刻します」「10分遅れます」 → **遅刻**
- 「体調不良で休みます」「欠勤します」 → **欠勤**
- 「有給休暇を取ります」「年休です」 → **有給休暇**
- 「午前休を取ります」 → **午前休**
- 「早退します」 → **早退**
- 「直行します」「営業先から出勤」 → **直行**
- 「直帰します」 → **直帰**

AIが自動的に解析し、勤怠カードを投稿します。

### 勤怠記録の編集

勤怠カードの「✏️ 修正」ボタンをクリックして、モーダルから編集できます。

### 勤怠記録の削除

勤怠カードの「🗑️ 取消」ボタンをクリックして、削除確認後に削除できます。

### 履歴の表示

グローバルショートカット「履歴を表示」から、自分または他のユーザーの勤怠履歴を表示できます。

### 設定（v2.0）

グローバルショートカット「設定」から、以下の設定ができます：

1. **管理者の設定**: レポート受信者を選択
2. **グループの管理**: 課やチームを作成し、メンバーを割り当て

## バージョン履歴

### v2.23 (2026-01-24) - OAuth マルチテナント対応 🆕

- **OAuth Flow の完全実装**
  - `FirestoreInstallationStore` を実装
  - インストール URL（`/slack/install`）を追加
  - 自動コールバック処理（`/slack/oauth_redirect`）
- **マルチテナント対応の完成**
  - 全リスナーで `team_id` ベースの動的 WebClient 生成
  - `get_slack_client(team_id)` による動的トークン取得
  - ワークスペースごとに独立したデータ管理
- **依存関係の最適化**
  - `requirements.txt` を OAuth 対応版に更新
  - 不要な依存関係を削除
- **ドキュメント整備**
  - OAuth セットアップガイドを追加
  - デプロイスクリプト（Windows/Linux）を追加
  - 環境変数サンプルファイルを追加
- **コード品質の向上**
  - デバッグ用 print 文を削除
  - 不完全な try-except を整理
  - プロダクションレディなコードに清書

### v2.22 (2026-01-23)

- 動的グループ管理 UI の改善
- レポート設定モーダルの一覧表示対応
- オーバーフローメニューによる個別編集・削除

### v2.0 (2026-01-21)

- 動的グループ管理機能を追加
- ワークスペース設定（管理者）機能を追加
- 日次レポートをグループベースに刷新
- 旧方式からの段階的移行をサポート

### v1.1 (リファクタリング版)

- ディレクトリ構造を整理（handlers/services/views/shared）
- マルチテナント対応を強化（`workspace_id`の徹底）
- 日本語 docstring と type hints を追加
- エラーハンドリングを集約

### v1.0 (初期版)

- AI 自動解析による勤怠記録
- 日次レポート機能
- 履歴表示・編集機能
- 固定セクション構造（1課〜8課）

## トラブルシューティング

### メッセージが解析されない

- OpenAI APIキーが正しく設定されているか確認
- Cloud Logsでエラーメッセージを確認
- メッセージの内容が曖昧な場合、より明確な表現に変更

### 日次レポートが送信されない

- Cloud Schedulerが正常に動作しているか確認
- `SLACK_WORKSPACE_ID`が設定されているか確認
- 管理者が設定されているか確認（v2.2）
- グループが正しく作成されているか確認（v2.2）

### モーダルが表示されない

- Slack Appのスコープが正しく設定されているか確認
- Request URLが正しく設定されているか確認

## 開発

### ローカル開発

```bash
# 1. 依存関係のインストール
pip install -r requirements.txt

# 2. 環境変数を設定
export SLACK_BOT_TOKEN=xoxb-...
export SLACK_SIGNING_SECRET=...
export OPENAI_API_KEY=sk-...

# 3. ローカルサーバーを起動（Cloud Functions Frameworkを使用）
functions-framework --target=slack_bot --debug
```

### テスト

テスト項目は `docs/v2.0_implementation_summary.md` を参照してください。

## ライセンス

このプロジェクトは MIT License の下で公開されています。

## 貢献

プルリクエストを歓迎します。大きな変更を加える場合は、まずissueを開いて変更内容について議論してください。

## サポート

問題が発生した場合は、GitHubのissueを作成してください。

## 作者

- 初期開発: a_shimizu
- リファクタリング・v2.0実装: AI Assistant (Claude Sonnet 4.5)

## 謝辞

- Slack Bolt Framework
- OpenAI API
- Google Cloud Platform
