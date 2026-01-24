# ============================================
# Slack 勤怠管理 Bot - Cloud Run デプロイスクリプト (Windows)
# ============================================

# ============================================
# 設定
# ============================================

$PROJECT_ID = "your-gcp-project-id"
$SERVICE_NAME = "slack-attendance-bot"
$REGION = "asia-northeast1"

# 環境変数（必須）
$SLACK_CLIENT_ID = "your-client-id"
$SLACK_CLIENT_SECRET = "your-client-secret"
$SLACK_SIGNING_SECRET = "your-signing-secret"
$SLACK_APP_ID = "your-app-id"
$OPENAI_API_KEY = "your-openai-api-key"

# ============================================
# 関数
# ============================================

function Print-Step {
    param([string]$Message)
    Write-Host "[STEP] $Message" -ForegroundColor Green
}

function Print-Error {
    param([string]$Message)
    Write-Host "[ERROR] $Message" -ForegroundColor Red
}

function Print-Warning {
    param([string]$Message)
    Write-Host "[WARNING] $Message" -ForegroundColor Yellow
}

# ============================================
# メイン処理
# ============================================

Print-Step "Slack 勤怠管理 Bot のデプロイを開始します..."

# 1. GCP プロジェクトの設定
Print-Step "GCP プロジェクトを設定中: $PROJECT_ID"
gcloud config set project $PROJECT_ID

# 2. 必要な API の有効化
Print-Step "必要な API を有効化中..."
gcloud services enable run.googleapis.com
gcloud services enable cloudbuild.googleapis.com
gcloud services enable firestore.googleapis.com

# 3. Cloud Run へのデプロイ
Print-Step "Cloud Run にデプロイ中..."
gcloud run deploy $SERVICE_NAME `
  --source . `
  --region=$REGION `
  --platform=managed `
  --allow-unauthenticated `
  --memory=512Mi `
  --timeout=60s `
  --min-instances=0 `
  --max-instances=10 `
  --set-env-vars="SLACK_CLIENT_ID=$SLACK_CLIENT_ID" `
  --set-env-vars="SLACK_CLIENT_SECRET=$SLACK_CLIENT_SECRET" `
  --set-env-vars="SLACK_SIGNING_SECRET=$SLACK_SIGNING_SECRET" `
  --set-env-vars="SLACK_APP_ID=$SLACK_APP_ID" `
  --set-env-vars="ENABLE_OAUTH=true" `
  --set-env-vars="OPENAI_API_KEY=$OPENAI_API_KEY" `
  --set-env-vars="LOG_LEVEL=INFO" `
  --set-env-vars="ENABLE_PUBSUB=false"

# 4. デプロイ完了
Print-Step "デプロイが完了しました！"

# 5. サービスURLの取得
$SERVICE_URL = gcloud run services describe $SERVICE_NAME --region=$REGION --format='value(status.url)'

Write-Host ""
Write-Host "========================================" -ForegroundColor Green
Write-Host "デプロイ完了" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host ""
Write-Host "サービス URL: " -NoNewline
Write-Host "$SERVICE_URL" -ForegroundColor Green
Write-Host ""
Write-Host "次のステップ:" -ForegroundColor Yellow
Write-Host ""
Write-Host "1. Slack App の OAuth 設定を更新してください:"
Write-Host "   - OAuth & Permissions > Redirect URLs:"
Write-Host "     $SERVICE_URL/slack/oauth_redirect" -ForegroundColor Green
Write-Host ""
Write-Host "2. Event Subscriptions の Request URL を更新してください:"
Write-Host "   $SERVICE_URL/slack/events" -ForegroundColor Green
Write-Host ""
Write-Host "3. Interactivity & Shortcuts の Request URL を更新してください:"
Write-Host "   $SERVICE_URL/slack/events" -ForegroundColor Green
Write-Host ""
Write-Host "4. インストール URL にアクセスしてください:"
Write-Host "   $SERVICE_URL/slack/install" -ForegroundColor Green
Write-Host ""
Write-Host "========================================" -ForegroundColor Green
