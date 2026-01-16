# 1. 軽量なPythonイメージを使用
FROM python:3.12-slim

# 2. コンテナ内の作業ディレクトリを設定
WORKDIR /app

# 3. 依存関係ファイルを先にコピー（効率的なビルドのため）
COPY requirements.txt .

# 4. ライブラリのインストール
RUN pip install --no-cache-dir -r requirements.txt

# 5. ソースコードをコピー（resourcesフォルダごとコピー）
COPY . .

# 6. 環境変数の設定（Pythonの出力をリアルタイムで見るため）
ENV PYTHONUNBUFFERED=1

# 7. 実行コマンド（--host=0.0.0.0 が必須）
# --host=0.0.0.0 が含まれているか確認
CMD ["functions-framework", "--target=slack_bot", "--source=/resources/main.py", "--port=8080", "--host=0.0.0.0"]