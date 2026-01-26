# 構造化ログ実装ガイド

## 概要

このドキュメントは、Slack勤怠管理BotにおけるJSON構造化ログの実装について説明します。

構造化ログを導入することで、以下のメリットが得られます：
- GCP Cloud Loggingでの検索・集計が容易
- OpenAI APIコストの自動追跡
- AI解析失敗の分析が可能

## 実装内容

### 1. 共通ロガーの拡張 (`resources/shared/setup_logger.py`)

以下の3つの関数を追加しました：

#### `log_structured()`
汎用的な構造化ログ出力関数。JSON形式でログを出力します。

```python
log_structured(
    logger, "warning", "何か問題が発生",
    team_id="T123",
    user_id="U456",
    custom_field="value"
)
```

#### `log_ai_parse_failure()`
AI解析の失敗を記録する専用関数。

```python
log_ai_parse_failure(
    logger, "T123", "C456", "U789",
    "おはようございます",
    "勤怠データが含まれていない"
)
```

**出力例：**
```json
{
  "message": "[AI_PARSE_FAILURE]",
  "team_id": "T123",
  "channel_id": "C456",
  "user_id": "U789",
  "text": "おはようございます",
  "reason": "勤怠データが含まれていない"
}
```

#### `log_openai_cost()`
OpenAI APIのコストデータを記録する専用関数。

```python
log_openai_cost(
    logger, 150, 50, 200, "gpt-4o-mini",
    team_id="T123", user_id="U456"
)
```

**出力例：**
```json
{
  "message": "[COST_LOG]",
  "prompt_tokens": 150,
  "completion_tokens": 50,
  "total_tokens": 200,
  "model": "gpt-4o-mini",
  "team_id": "T123",
  "user_id": "U456"
}
```

### 2. NLPサービスの更新 (`resources/services/nlp_service.py`)

#### 変更点
- `extract_attendance_from_text()` に `team_id` と `user_id` パラメータを追加
- OpenAI APIレスポンスから `usage` データを取得
- `log_openai_cost()` を呼び出してコストログを出力

#### 使用例
```python
extraction = extract_attendance_from_text(
    text="明日は在宅勤務です",
    team_id="T123",
    user_id="U456"
)
```

### 3. 勤怠リスナーの更新 (`resources/listeners/attendance_listener.py`)

#### 変更点
- `log_ai_parse_failure` をインポート
- AI解析失敗時に構造化ログを出力
- `extract_attendance_from_text()` 呼び出し時に `team_id` と `user_id` を渡す

## GCP Cloud Loggingでの活用方法

### 1. AI解析失敗の一覧を表示

**ログビューア検索クエリ：**
```
jsonPayload.message="[AI_PARSE_FAILURE]"
```

**高度なフィルタ（特定ユーザーの失敗のみ）：**
```
jsonPayload.message="[AI_PARSE_FAILURE]"
jsonPayload.user_id="U123456"
```

### 2. OpenAI APIコストの一覧を表示

**ログビューア検索クエリ：**
```
jsonPayload.message="[COST_LOG]"
```

**特定ワークスペースのコストのみ：**
```
jsonPayload.message="[COST_LOG]"
jsonPayload.team_id="T123456"
```

### 3. ログベースの指標（Log-based Metrics）の設定

GCPのログベースの指標を使うと、トークン数を自動集計できます。

#### 手順

1. **Cloud Logging > ログベースの指標** に移動
2. **指標を作成** をクリック
3. 以下のように設定：

**指標タイプ：** カウンタ

**ログフィルタ：**
```
jsonPayload.message="[COST_LOG]"
```

**指標フィールドの抽出：**
- フィールド名: `total_tokens`
- フィールドパス: `jsonPayload.total_tokens`
- フィールドタイプ: `整数`

4. **作成** をクリック

これで、Cloud Monitoringのメトリクスエクスプローラーで「今日の消費トークン数合計」がグラフで表示されます。

### 4. 日次レポートの自動生成（発展形）

BigQueryにログをエクスポートすることで、SQLクエリで詳細なレポートを生成できます。

#### ログシンク設定
1. **Cloud Logging > ログルーター** に移動
2. **シンクを作成** をクリック
3. 宛先サービス: BigQuery
4. フィルタ: `jsonPayload.message="[COST_LOG]"` または `jsonPayload.message="[AI_PARSE_FAILURE]"`

#### SQLクエリ例（日次コスト集計）

```sql
SELECT
  DATE(timestamp) as date,
  JSON_VALUE(jsonPayload, '$.team_id') as workspace_id,
  JSON_VALUE(jsonPayload, '$.model') as model,
  SUM(CAST(JSON_VALUE(jsonPayload, '$.total_tokens') AS INT64)) as total_tokens,
  COUNT(*) as api_calls
FROM
  `project.dataset.stdout`
WHERE
  JSON_VALUE(jsonPayload, '$.message') = '[COST_LOG]'
  AND DATE(timestamp) >= CURRENT_DATE() - 7
GROUP BY
  date, workspace_id, model
ORDER BY
  date DESC, total_tokens DESC
```

このクエリで、過去7日間のワークスペース別・モデル別のトークン数とAPI呼び出し回数が取得できます。

## トークン単価からコストを計算

gpt-4o-mini (2026年1月時点)の料金：
- Input: $0.150 per 1M tokens
- Output: $0.600 per 1M tokens

**コスト計算式：**
```
cost = (prompt_tokens × 0.150 + completion_tokens × 0.600) / 1,000,000
```

BigQueryで計算する場合：

```sql
SELECT
  DATE(timestamp) as date,
  JSON_VALUE(jsonPayload, '$.team_id') as workspace_id,
  ROUND(
    (SUM(CAST(JSON_VALUE(jsonPayload, '$.prompt_tokens') AS INT64)) * 0.150 
    + SUM(CAST(JSON_VALUE(jsonPayload, '$.completion_tokens') AS INT64)) * 0.600) 
    / 1000000, 
    4
  ) as total_cost_usd
FROM
  `project.dataset.stdout`
WHERE
  JSON_VALUE(jsonPayload, '$.message') = '[COST_LOG]'
  AND DATE(timestamp) = CURRENT_DATE()
GROUP BY
  date, workspace_id
ORDER BY
  total_cost_usd DESC
```

## 注意事項

### パフォーマンスへの影響
- JSON形式のログはテキストログより若干サイズが大きくなります
- しかし、構造化されているため検索・集計が高速です
- Cloud Loggingの料金は、取り込まれたログのデータ量に基づいています（2026年1月時点で最初の50GB/月は無料）

### プライバシー
- ユーザーの発言内容（`text`）は最大200文字に切り詰められます
- 個人情報を含む可能性があるため、ログの保持期間やアクセス権限を適切に設定してください

### 後方互換性
- 既存のテキストログも引き続き出力されます
- 構造化ログは追加的に出力されるため、既存のログ監視には影響ありません

## まとめ

構造化ログの導入により、以下が可能になりました：

1. ✅ AI解析失敗の発言を一覧表示・分析
2. ✅ OpenAI APIコストの日次/月次集計
3. ✅ ワークスペース別・ユーザー別のコスト追跡
4. ✅ Cloud Monitoringでのリアルタイムグラフ表示
5. ✅ BigQueryを使った詳細なレポート作成

これにより、Botの運用状況やコストを可視化し、最適化のための意思決定がしやすくなります。
