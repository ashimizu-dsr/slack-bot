# Pub/Sub 非同期処理設定ガイド

## 概要

このドキュメントでは、Slack勤怠管理BotでPub/Subを使った非同期処理を有効化する手順を説明します。

## 背景

Cloud Runの3秒タイムアウト制約を回避するため、以下のようなアーキテクチャに対応しています：

```
Slackリクエスト
  → Cloud Run (Producer)
    → 即座にack()を返す
    → Pub/Subトピックにメッセージを送信
  
Pub/Subトピック
  → Cloud Run (Consumer)
    → 実際のビジネスロジックを実行
    → Slack APIを呼び出し
```

## ⚠️ 重要な制約

**Slackの`trigger_id`は発行から3秒間のみ有効です。**

そのため、以下の処理は**非同期化できません**：

- ❌ モーダル表示 (`views.open`, `views.push`)
- ❌ モーダル更新 (`views.update`)

以下の処理のみ非同期化が可能です：

- ✅ DB更新処理
- ✅ メッセージ送信
- ✅ 通知送信

## セットアップ手順

### 1. Pub/Subトピックの作成

```bash
# プロジェクトIDを設定
export PROJECT_ID="your-project-id"

# トピック作成
gcloud pubsub topics create slack-interactions-topic --project=$PROJECT_ID

# サブスクリプション作成（Push型）
gcloud pubsub subscriptions create slack-interactions-sub \
  --topic=slack-interactions-topic \
  --push-endpoint=https://YOUR-CLOUD-RUN-URL/pubsub/interactions \
  --project=$PROJECT_ID
```

### 2. 環境変数の設定

Cloud Runの環境変数に以下を追加：

```bash
ENABLE_PUBSUB=true
GCP_PROJECT_ID=your-project-id
SLACK_INTERACTIONS_TOPIC=slack-interactions-topic
```

### 3. Cloud Runへのデプロイ

```bash
gcloud run deploy slack-attendance-bot \
  --source . \
  --region asia-northeast1 \
  --allow-unauthenticated \
  --set-env-vars ENABLE_PUBSUB=true,GCP_PROJECT_ID=your-project-id
```

### 4. IAM権限の設定

Cloud RunサービスアカウントにPub/Sub権限を付与：

```bash
# サービスアカウントのメールアドレスを取得
export SERVICE_ACCOUNT=$(gcloud run services describe slack-attendance-bot \
  --region=asia-northeast1 \
  --format='value(spec.template.spec.serviceAccountName)')

# Pub/Sub Publisher権限を付与
gcloud projects add-iam-policy-binding $PROJECT_ID \
  --member="serviceAccount:$SERVICE_ACCOUNT" \
  --role="roles/pubsub.publisher"
```

## テスト

### 1. 同期処理モード（デフォルト）

```bash
# ENABLE_PUBSUB=false （または未設定）
curl -X POST https://YOUR-CLOUD-RUN-URL/slack/events \
  -H "Content-Type: application/json" \
  -d '{"type": "url_verification", "challenge": "test"}'
```

### 2. 非同期処理モード

```bash
# ENABLE_PUBSUB=true
# ログを確認して、Pub/Subメッセージが送信されているか確認
gcloud logging read "resource.type=cloud_run_revision" \
  --project=$PROJECT_ID \
  --limit 50
```

## トラブルシューティング

### Pub/Subメッセージが処理されない

**症状**: Slackでボタンを押しても反応がない

**原因**: Pub/Sub Pushエンドポイントが正しく設定されていない

**解決策**:
```bash
# サブスクリプションのPushエンドポイントを確認
gcloud pubsub subscriptions describe slack-interactions-sub

# 必要に応じて更新
gcloud pubsub subscriptions modify slack-interactions-sub \
  --push-endpoint=https://YOUR-CLOUD-RUN-URL/pubsub/interactions
```

### タイムアウトエラー

**症状**: `trigger_id is no longer valid`

**原因**: Pub/Sub経由でモーダル表示を試みている

**解決策**: モーダル表示系は同期処理のまま残す（現在の実装で対応済み）

## アーキテクチャ図

```
┌─────────────────┐
│  Slack User     │
└────────┬────────┘
         │ ボタン押下
         ▼
┌─────────────────────────────────────┐
│  Cloud Run (Producer)               │
│  - action_handlers.py               │
│  - ack() を即座に返す                │
│  - dispatcher.dispatch()            │
└────────┬────────────────────────────┘
         │ Pub/Sub Publish
         ▼
┌─────────────────────────────────────┐
│  Pub/Sub Topic                      │
│  (slack-interactions-topic)         │
└────────┬────────────────────────────┘
         │ Push Subscription
         ▼
┌─────────────────────────────────────┐
│  Cloud Run (Consumer)               │
│  /pubsub/interactions               │
│  - interaction_processor.py         │
│  - 実際の処理を実行                  │
│  - Slack API呼び出し                │
└─────────────────────────────────────┘
```

## 現在の実装状況

### 非同期化済み

- ✅ 勤怠削除処理 (`delete_attendance_confirm`)

### 非同期化対象外（trigger_id制約のため）

- ❌ モーダル表示全般
- ❌ 履歴フィルタ更新

### 今後の拡張候補

- 📋 レポート送信処理
- 📋 通知送信処理
- 📋 バッチ更新処理

## 参考リンク

- [Google Cloud Pub/Sub Documentation](https://cloud.google.com/pubsub/docs)
- [Slack API: trigger_id](https://api.slack.com/interactivity/handling#modal_responses)
- [Cloud Run: Processing Pub/Sub messages](https://cloud.google.com/run/docs/tutorials/pubsub)
