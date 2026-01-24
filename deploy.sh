#!/bin/bash

# ============================================
# Slack 勤怠管理 Bot - Cloud Run デプロイスクリプト
# ============================================

set -e

# 色設定
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# ============================================
# 設定
# ============================================

PROJECT_ID="your-gcp-project-id"
SERVICE_NAME="slack-attendance-bot"
REGION="asia-northeast1"

# 環境変数（必須）
SLACK_CLIENT_ID="your-client-id"
SLACK_CLIENT_SECRET="your-client-secret"
SLACK_SIGNING_SECRET="your-signing-secret"
SLACK_APP_ID="your-app-id"
OPENAI_API_KEY="your-openai-api-key"

# ============================================
# 関数
# ============================================

print_step() {
    echo -e "${GREEN}[STEP]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

# ============================================
# メイン処理
# ============================================

print_step "Slack 勤怠管理 Bot のデプロイを開始します..."

# 1. GCP プロジェクトの設定
print_step "GCP プロジェクトを設定中: ${PROJECT_ID}"
gcloud config set project ${PROJECT_ID}

# 2. 必要な API の有効化
print_step "必要な API を有効化中..."
gcloud services enable run.googleapis.com
gcloud services enable cloudbuild.googleapis.com
gcloud services enable firestore.googleapis.com

# 3. Cloud Run へのデプロイ
print_step "Cloud Run にデプロイ中..."
gcloud run deploy ${SERVICE_NAME} \
  --source . \
  --region=${REGION} \
  --platform=managed \
  --allow-unauthenticated \
  --memory=512Mi \
  --timeout=60s \
  --min-instances=0 \
  --max-instances=10 \
  --set-env-vars="SLACK_CLIENT_ID=${SLACK_CLIENT_ID}" \
  --set-env-vars="SLACK_CLIENT_SECRET=${SLACK_CLIENT_SECRET}" \
  --set-env-vars="SLACK_SIGNING_SECRET=${SLACK_SIGNING_SECRET}" \
  --set-env-vars="SLACK_APP_ID=${SLACK_APP_ID}" \
  --set-env-vars="ENABLE_OAUTH=true" \
  --set-env-vars="OPENAI_API_KEY=${OPENAI_API_KEY}" \
  --set-env-vars="LOG_LEVEL=INFO" \
  --set-env-vars="ENABLE_PUBSUB=false"

# 4. デプロイ完了
print_step "デプロイが完了しました！"

# 5. サービスURLの取得
SERVICE_URL=$(gcloud run services describe ${SERVICE_NAME} --region=${REGION} --format='value(status.url)')
echo ""
echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}デプロイ完了${NC}"
echo -e "${GREEN}========================================${NC}"
echo ""
echo -e "サービス URL: ${GREEN}${SERVICE_URL}${NC}"
echo ""
echo -e "${YELLOW}次のステップ:${NC}"
echo ""
echo "1. Slack App の OAuth 設定を更新してください:"
echo "   - OAuth & Permissions > Redirect URLs:"
echo -e "     ${GREEN}${SERVICE_URL}/slack/oauth_redirect${NC}"
echo ""
echo "2. Event Subscriptions の Request URL を更新してください:"
echo -e "   ${GREEN}${SERVICE_URL}/slack/events${NC}"
echo ""
echo "3. Interactivity & Shortcuts の Request URL を更新してください:"
echo -e "   ${GREEN}${SERVICE_URL}/slack/events${NC}"
echo ""
echo "4. インストール URL にアクセスしてください:"
echo -e "   ${GREEN}${SERVICE_URL}/slack/install${NC}"
echo ""
echo -e "${GREEN}========================================${NC}"
