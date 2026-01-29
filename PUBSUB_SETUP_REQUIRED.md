# 🚨 Pub/Sub設定が必要です

## 現在の状況

現在、以下の機能が**動作していません**：

- ❌ **Bot参加時の過去ログ遡り処理**
- ❌ **メッセージからの勤怠登録（AI解析）**
- ❌ **削除確認の実行**
- ❌ **レポートコマンド (`/report`)**

**原因**: `PUBSUB_TOPIC_ID`環境変数が未設定のため、Pub/Subが無効化されています。

## 必要な設定手順

### 1. Pub/Subトピックの作成

```bash
# プロジェクトID（your-project-idを実際のプロジェクトIDに置き換え）
export PROJECT_ID="slack-kintai-bot-484306"

# トピック作成
gcloud pubsub topics create slack-attendance-topic --project=$PROJECT_ID
```

### 2. Cloud Run URLの確認

```bash
# デプロイ済みのCloud Run URLを取得
gcloud run services describe slack-attendance-bot \
  --region=asia-northeast1 \
  --format='value(status.url)'
```

出力例: `https://slack-attendance-bot-xxxxx-an.a.run.app`

### 3. Pub/Subサブスクリプションの作成

```bash
# 上記で取得したCloud Run URLを使用
export CLOUD_RUN_URL="https://slack-attendance-bot-xxxxx-an.a.run.app"

# サブスクリプション作成（Push型）
gcloud pubsub subscriptions create slack-attendance-subscription \
  --topic=slack-attendance-topic \
  --push-endpoint=${CLOUD_RUN_URL}/pubsub/interactions \
  --ack-deadline=600 \
  --project=$PROJECT_ID
```

### 4. IAM権限の設定

Cloud RunからPub/Subへの送信権限を付与：

```bash
# サービスアカウントのメールアドレス
export SERVICE_ACCOUNT="cloud-build-deployer-for-kinta@slack-kintai-bot-484306.iam.gserviceaccount.com"

# Pub/Sub Publisher権限を付与
gcloud projects add-iam-policy-binding $PROJECT_ID \
  --member="serviceAccount:$SERVICE_ACCOUNT" \
  --role="roles/pubsub.publisher"
```

Pub/SubからCloud Runへの呼び出し権限を付与：

```bash
# Pub/Subサービスアカウントを取得
export PUBSUB_SERVICE_ACCOUNT="service-${PROJECT_NUMBER}@gcp-sa-pubsub.iam.gserviceaccount.com"

# Cloud Run Invoker権限を付与
gcloud run services add-iam-policy-binding slack-attendance-bot \
  --region=asia-northeast1 \
  --member="serviceAccount:${PUBSUB_SERVICE_ACCOUNT}" \
  --role="roles/run.invoker"
```

**注意**: `PROJECT_NUMBER`は以下のコマンドで取得できます：
```bash
gcloud projects describe $PROJECT_ID --format='value(projectNumber)'
```

### 5. 再デプロイ

`cloudbuild.yaml`に環境変数を追加済みなので、再デプロイします：

```bash
# Cloud Buildを使ってデプロイ
gcloud builds submit --config cloudbuild.yaml \
  --substitutions=_SERVICE_NAME=slack-attendance-bot
```

または、直接デプロイ：

```bash
gcloud run deploy slack-attendance-bot \
  --source . \
  --region asia-northeast1 \
  --service-account=cloud-build-deployer-for-kinta@slack-kintai-bot-484306.iam.gserviceaccount.com \
  --set-env-vars PUBSUB_TOPIC_ID=slack-attendance-topic \
  --memory=1Gi \
  --cpu=1 \
  --timeout=300 \
  --no-cpu-throttling \
  --project=$PROJECT_ID
```

### 6. 動作確認

#### ログでPub/Subの初期化を確認

```bash
gcloud logging read "resource.type=cloud_run_revision AND resource.labels.service_name=slack-attendance-bot AND textPayload=~'Pub/Sub'" \
  --limit 10 \
  --format json
```

以下のログが表示されればOK：
```
AttendanceListener: Pub/Sub Publisher initialized
SystemListener: Pub/Sub Publisher initialized
AdminListener: Pub/Sub Publisher initialized
```

#### Botをチャンネルに招待してテスト

1. Slackで新しいチャンネルを作成
2. Botを招待 (`/invite @勤怠管理Bot`)
3. ログを確認：

```bash
gcloud logging read "resource.type=cloud_run_revision AND textPayload=~'Bot参加'" \
  --limit 20 \
  --format json
```

以下のログが表示されればPub/Subが動作しています：
```
[Bot参加イベント] Bot自身の参加を検知
AttendanceListener: Published to Pub/Sub (message_id=xxx)
Pub/Sub: Dispatching to SystemListener
[過去ログ処理] 開始
```

#### 勤怠メッセージでテスト

Slackで以下のようなメッセージを送信：
```
おはようございます。今日は出勤です。
```

ログを確認：
```bash
gcloud logging read "resource.type=cloud_run_revision AND textPayload=~'Published to Pub/Sub'" \
  --limit 10
```

## トラブルシューティング

### "Pub/Sub disabled (no PUBSUB_TOPIC_ID)"と表示される

**原因**: 環境変数が設定されていない

**解決策**: 
1. `cloudbuild.yaml`に`--set-env-vars`が追加されているか確認
2. 再デプロイを実行

### メッセージがPub/Subに送信されない

**原因**: IAM権限が不足している

**解決策**:
```bash
# 権限を確認
gcloud projects get-iam-policy $PROJECT_ID \
  --flatten="bindings[].members" \
  --filter="bindings.members:serviceAccount:cloud-build-deployer-for-kinta@*"

# roles/pubsub.publisherが含まれているか確認
```

### Pub/Subメッセージが処理されない

**原因**: サブスクリプションのPushエンドポイントが間違っている

**解決策**:
```bash
# サブスクリプションの設定を確認
gcloud pubsub subscriptions describe slack-attendance-subscription

# Push Configが正しいCloud Run URLを指しているか確認
# 必要に応じて更新
gcloud pubsub subscriptions modify slack-attendance-subscription \
  --push-endpoint=https://YOUR-ACTUAL-CLOUD-RUN-URL/pubsub/interactions
```

## まとめ

この設定を完了すると、以下の機能が有効になります：

✅ Bot参加時の過去7日間のメッセージ自動解析  
✅ チャットメッセージからの自動勤怠登録  
✅ 削除処理の非同期実行  
✅ レポートコマンドの実行  

設定後は必ずログを確認し、Pub/Subが正しく動作しているか確認してください。
