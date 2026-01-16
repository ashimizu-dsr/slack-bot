# 1. AWS Lambda専用ではなく、標準的なPythonイメージを使います
FROM python:3.11-slim

# 2. 作業ディレクトリを設定
WORKDIR /app

# 3. まずライブラリをインストール（キャッシュを効かせるため先にコピー）
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 4. プロジェクト全体をコピー（shared, services, resourcesなどがすべて入ります）
COPY . .

# 5. 環境変数 PORT を設定（Cloud Runはデフォルトで8080を使います）
ENV PORT 8080

# 6. 実行コマンド
# functions-framework を使い、resources/main.py 内の slack_bot 関数を起動します
CMD ["functions-framework", "--target=slack_bot", "--source=resources/main.py"]