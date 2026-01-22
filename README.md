# Slack勤怠管理Bot

**バージョン**: v2.22 (レポート設定UI全面刷新版)  
**最終更新**: 2026-01-22

AI解析機能を備えた、Slack向けの勤怠管理Botです。自然言語での勤怠連絡を自動的に解析し、構造化されたデータとして保存・集計します。

## 主な機能

### 1. AI自動解析による勤怠記録

- Slackメッセージを自然言語で解析（OpenAI API使用）
- 「遅刻します」「有給休暇を取ります」などの表現を自動認識
- ステータス（出勤、遅刻、欠勤、有給休暇など）と備考を抽出
- 打ち消し線によるステータス変更にも対応

### 2. 動的グループ管理（v2.22）

- 課やチームなどのグループを動的に作成・管理
- **スラッシュコマンド**: `/report-admin` でレポート設定にアクセス
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
│   ├── handlers/           # Slackイベントのルーティング
│   │   ├── action_handlers.py    # ボタン・ショートカット処理
│   │   ├── modal_handlers.py     # モーダル送信処理
│   │   └── slack_handlers.py     # メッセージ受信処理
│   ├── services/           # ビジネスロジック
│   │   ├── attendance_service.py # 勤怠データ管理
│   │   ├── nlp_service.py        # AI解析
│   │   ├── notification_service.py # 通知送信
│   │   ├── group_service.py      # グループ管理（v2.0）
│   │   └── workspace_service.py  # ワークスペース設定（v2.0）
│   ├── views/              # UI（Block Kit）生成
│   │   └── modal_views.py
│   ├── shared/             # 共通ユーティリティ
│   │   ├── db.py                 # DB操作
│   │   ├── errors.py             # エラー定義
│   │   └── setup_logger.py       # ロガー設定
│   ├── constants.py        # 定数定義
│   └── main.py            # エントリポイント
├── docs/                   # ドキュメント
│   ├── current_spec.md            # v1.0仕様（リファクタ前）
│   ├── spec_v1.1.md               # v1.1仕様（リファクタ後）
│   ├── spec_v2.0.md               # v2.0設計書
│   ├── spec_v2.1.md               # v2.1設計書
│   ├── spec_v2.2.md               # v2.2設計書
│   ├── spec_v2.22.md              # v2.22設計書（最新）
│   ├── v2.0_implementation_summary.md # v2.0実装サマリー
│   ├── v2.1_implementation_summary.md # v2.1実装サマリー
│   ├── v2.2_implementation_summary.md # v2.2実装サマリー
│   └── v2.22_implementation_summary.md # v2.22実装サマリー（最新）
├── requirements.txt        # Python依存関係
└── README.md              # このファイル
```

### データモデル

#### Firestore コレクション

##### 1. `attendance/{workspace_id}/records/{record_id}`

個々の勤怠記録を格納します。

```json
{
  "user_id": "U01234567",
  "email": "user@example.com",
  "date": "2026-01-21",
  "status": "on_time",
  "note": "通常出勤",
  "channel_id": "C01234567",
  "ts": "1705812345.123456",
  "created_at": "2026-01-21T08:30:00Z",
  "updated_at": "2026-01-21T08:30:00Z"
}
```

##### 2. `groups/{workspace_id}/groups/{group_id}` (v2.2)

動的グループ情報を格納します。

**v2.2からの変更**: `group_id` にグループ名を使用（name-as-id方式）

```json
{
  "group_id": "営業1課",  // グループ名そのもの（v2.2から）
  "name": "営業1課",
  "member_ids": ["U001", "U002", "U003"],
  "created_at": "2026-01-21T10:00:00Z",
  "updated_at": "2026-01-21T10:00:00Z"
}
```

##### 3. `workspace_settings/{workspace_id}` (v2.0)

ワークスペース設定を格納します。

```json
{
  "workspace_id": "T01234567",
  "admin_ids": ["U001", "U002"],
  "report_channel_id": "C01234567",
  "updated_at": "2026-01-21T10:00:00Z"
}
```

## セットアップ

### 1. 前提条件

- Google Cloud プロジェクト
- Slack App の作成（Bot Token と Signing Secret）
- OpenAI API キー
- Python 3.11+

### 2. Slack Appの設定

#### 必要なスコープ（Bot Token Scopes）

- `channels:history` - チャンネルのメッセージを読む
- `channels:read` - チャンネル情報を取得
- `chat:write` - メッセージを送信
- `commands` - スラッシュコマンド（オプション）
- `im:write` - DMを送信
- `users:read` - ユーザー情報を取得
- `users:read.email` - ユーザーのメールアドレスを取得

#### イベントサブスクリプション

- `message.channels` - チャンネルのメッセージを受信

#### ショートカット

- **グローバルショートカット**:
  - `open_history_modal`: 履歴を表示
  - `open_member_setup_modal`: 設定（v2.0）

### 3. 環境変数の設定

以下の環境変数をGoogle Cloud Run/Functionsに設定してください。

#### 必須

```bash
SLACK_BOT_TOKEN=xoxb-your-bot-token
SLACK_SIGNING_SECRET=your-signing-secret
OPENAI_API_KEY=sk-your-openai-api-key
```

#### オプション

```bash
# ワークスペースID（マルチテナント対応の場合）
SLACK_WORKSPACE_ID=T01234567

# レポート送信先チャンネルID（未設定の場合は管理者DM）
REPORT_CHANNEL_ID=C01234567

# ログレベル
LOG_LEVEL=INFO
```

### 4. デプロイ

#### Google Cloud Runへのデプロイ

```bash
# 1. Google Cloudにログイン
gcloud auth login

# 2. プロジェクトを設定
gcloud config set project YOUR_PROJECT_ID

# 3. Cloud Runにデプロイ
gcloud run deploy slack-attendance-bot \
  --source . \
  --platform managed \
  --region asia-northeast1 \
  --allow-unauthenticated \
  --set-env-vars SLACK_BOT_TOKEN=xoxb-...,SLACK_SIGNING_SECRET=...,OPENAI_API_KEY=sk-...
```

#### Slack AppのRequest URLを設定

デプロイ後に表示されるURLを、Slack Appの以下の設定に追加してください：

- **Event Subscriptions > Request URL**: `https://your-cloud-run-url/slack/events`
- **Interactivity & Shortcuts > Request URL**: `https://your-cloud-run-url/slack/events`

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

### v2.0 (2026-01-21)

- 動的グループ管理機能を追加
- ワークスペース設定（管理者）機能を追加
- 日次レポートをグループベースに刷新
- 旧方式からの段階的移行をサポート

### v1.1 (リファクタリング版)

- ディレクトリ構造を整理（handlers/services/views/shared）
- マルチテナント対応を強化（`workspace_id`の徹底）
- 日本語docstringとtype hintsを追加
- エラーハンドリングを集約
- 存在しなかったメソッド（`get_specific_date_record`）を追加

### v1.0 (初期版)

- AI自動解析による勤怠記録
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
