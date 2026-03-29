# FamilyChatAuthorizer パッケージング & デプロイ手順

## 外部依存ライブラリについて

PyJWT と cryptography は Lambda Python ランタイムに含まれていないため、
コードと一緒にZIP化してアップロードする必要があります。

## パッケージング手順（ローカルPC / CloudShell）

```bash
# 1. 作業ディレクトリを作成
mkdir authorizer_pkg && cd authorizer_pkg

# 2. lambda_function.py をコピー
cp /path/to/lambda_function.py .

# 3. 依存ライブラリをカレントディレクトリにインストール
pip install \
  "PyJWT[crypto]==2.8.0" \
  "cryptography==42.0.5" \
  "requests==2.31.0" \
  --target . \
  --platform manylinux2014_x86_64 \
  --only-binary=:all:

# 4. ZIPに固める
zip -r ../authorizer.zip .

# 5. Lambdaにアップロード
cd ..
aws lambda update-function-code \
  --function-name FamilyChatAuthorizer \
  --zip-file fileb://authorizer.zip
```

## 環境変数

| キー                  | 値の例                        |
|-----------------------|-------------------------------|
| COGNITO_USER_POOL_ID  | ap-northeast-1_xxxxxxxxx      |
| COGNITO_APP_CLIENT_ID | 1a2b3c4d5e6f（クライアントID） |
| AWS_REGION_NAME       | ap-northeast-1                |

## Lambda設定

| 項目       | 値         |
|------------|------------|
| ランタイム | Python 3.12 |
| ハンドラ   | lambda_function.lambda_handler |
| タイムアウト | 5秒       |
| メモリ     | 128 MB     |

## FamilyChatHandler 側の変更点

$connect ハンドラで userId をクエリパラメータではなく
オーソライザーのコンテキストから取得するよう変更します。

```python
# 変更前（クエリパラメータから取得）
qs = event.get("queryStringParameters") or {}
user_id = qs.get("userId", "unknown")

# 変更後（オーソライザーのコンテキストから取得）
user_id = (
    event.get("requestContext", {})
         .get("authorizer", {})
         .get("userId", "unknown")
)
```

## テスト用イベント（Lambdaコンソール）

### 正常系（有効なトークン）
```json
{
  "methodArn": "arn:aws:execute-api:ap-northeast-1:123456789012:abcdef1234/prod/$connect",
  "queryStringParameters": {
    "token": "<CognitoのIDトークン>"
  }
}
```

期待されるレスポンス: Effect=Allow、context.userId にユーザー名が入る

### 異常系（トークンなし）
```json
{
  "methodArn": "arn:aws:execute-api:ap-northeast-1:123456789012:abcdef1234/prod/$connect",
  "queryStringParameters": {}
}
```

期待されるレスポンス: Effect=Deny
