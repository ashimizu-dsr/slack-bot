# Pub/Sub リファクタリング完了ガイド

## 概要

Slack勤怠管理BotにPub/Subを導入し、Cloud Runのタイムアウト制約（3秒以内の応答）を回避するためのリファクタリングを完了しました。

## 変更点

### 1. Listenerクラスの改善 (`resources/listeners/Listener.py`)

**新しいメソッド名:**
- `handle_sync()`: Slackイベントを受け取り、3秒以内に応答を返す（同期処理）
- `handle_async()`: Pub/Subから戻ってきた後の重い処理（非同期処理）

**特徴:**
- 1つのイベント（URL）に対する責務が1つのクラスに集約
- 受付側と実行側のコードが同じファイル内に記述
- 機能ごとの見通しが良い

### 2. 各リスナーのリファクタリング

**AttendanceListener** (`resources/listeners/attendance_listener.py`):
- メッセージからのAI解析による勤怠登録
- 修正・削除ボタン押下
- 履歴表示

**AdminListener** (`resources/listeners/admin_listener.py`):
- レポート設定ショートカット
- グループ追加・編集・削除
- **＠付きユーザー名問題を解決**（先頭の＠マークを除去）

**SystemListener** (`resources/listeners/system_listener.py`):
- Bot参加イベント
- ヘルスチェック

### 3. main.pyの統合

**変更内容:**
- `register_all_listeners()`が`listener_map`を返すように変更
- Pub/Subエンドポイント(`/pubsub/interactions`)で`action_type`に基づいて適切なリスナーを呼び出し
- 古いディスパッチャーコードを削除し、シンプルな構造に

### 4. 削除されたファイル

- `resources/shared/pubsub_utils.py` - Listenerクラスに統合
- 古い`attendance_listener.py`, `admin_listener.py`, `system_listener.py` - 新バージョンに置き換え

## 環境変数の設定

Pub/Subを有効にするために、以下の環境変数を設定してください:

```bash
# GCPプロジェクトID
GOOGLE_CLOUD_PROJECT=your-project-id

# Pub/SubトピックID
PUBSUB_TOPIC_ID=slack-attendance-topic
```

## Pub/Subの仕組み

```
┌─────────────────────────────────────────────────────────────────┐
│ 1. Slackイベント受信 (同期処理 - 3秒以内)                       │
├─────────────────────────────────────────────────────────────────┤
│ POST /slack/events                                               │
│   ↓                                                              │
│ Listener.handle_sync()                                           │
│   - ack()で即座に応答                                            │
│   - publish_to_worker()でPub/Subに投げる                         │
│   - Slackに200 OKを返す (3秒以内)                                │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│ 2. Pub/Sub経由で非同期処理実行                                   │
├─────────────────────────────────────────────────────────────────┤
│ POST /pubsub/interactions                                        │
│   ↓                                                              │
│ listener_map[action_type].handle_async()                         │
│   - AI解析、DB操作、外部API呼び出し                              │
│   - Slackへの通知送信                                            │
│   - 時間制限なし                                                 │
└─────────────────────────────────────────────────────────────────┘
```

## Pub/Subトピックとサブスクリプションのセットアップ

### 1. トピックの作成

```bash
gcloud pubsub topics create slack-attendance-topic \
  --project=your-project-id
```

### 2. サブスクリプションの作成

```bash
gcloud pubsub subscriptions create slack-attendance-subscription \
  --topic=slack-attendance-topic \
  --push-endpoint=https://your-cloud-run-url/pubsub/interactions \
  --ack-deadline=600 \
  --project=your-project-id
```

### 3. Cloud Runにデプロイ

```bash
gcloud run deploy slack-attendance-bot \
  --source . \
  --region asia-northeast1 \
  --set-env-vars GOOGLE_CLOUD_PROJECT=your-project-id,PUBSUB_TOPIC_ID=slack-attendance-topic \
  --allow-unauthenticated
```

## テスト方法

### 1. 同期処理のテスト

Slackで勤怠メッセージを送信:
```
おはようございます。今日は出勤です。
```

→ 3秒以内に応答が返る（リアクションなど）

### 2. 非同期処理のテスト

ログを確認:
```bash
gcloud logging read "resource.type=cloud_run_revision AND resource.labels.service_name=slack-attendance-bot" \
  --limit 50 \
  --format json
```

→ "Pub/Sub: Dispatching to AttendanceListener"というログが表示される

## トラブルシューティング

### Pub/Subが動作しない場合

1. 環境変数が正しく設定されているか確認:
   ```bash
   gcloud run services describe slack-attendance-bot --region asia-northeast1 --format="value(spec.template.spec.containers[0].env)"
   ```

2. Pub/Subトピックが存在するか確認:
   ```bash
   gcloud pubsub topics list --project=your-project-id
   ```

3. サブスクリプションが正しく設定されているか確認:
   ```bash
   gcloud pubsub subscriptions describe slack-attendance-subscription --project=your-project-id
   ```

### ユーザー名に＠が付く問題

AdminListenerとattendance_listenerの両方で、ユーザー名取得時に先頭の＠マークを除去するようになりました:

```python
# 先頭の＠マークを除去
if name and name.startswith("@"):
    name = name[1:]
```

## まとめ

このリファクタリングにより:
- ✅ Cloud Runの3秒タイムアウト制約を回避
- ✅ 同期処理と非同期処理が1つのクラスに集約され、保守性が向上
- ✅ ユーザー名の＠付き問題を解決
- ✅ コードの見通しが良くなり、機能追加が容易に

すべての処理がPub/Sub対応になり、重い処理を含むイベントでも安定して動作するようになりました。
