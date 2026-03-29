# FamilyLineWebhook

## 概要

LINE Messaging API からの Webhook を受信し、
親のメッセージを子供の iPad（WebSocket）に転送する Lambda 関数です。

外部ライブラリ不要のため ZIPパッケージング不要です。
コードをコンソールにそのまま貼り付けてデプロイできます。

## Lambda 設定

| 項目         | 値                             |
|--------------|--------------------------------|
| ランタイム   | Python 3.12                    |
| ハンドラ     | lambda_function.lambda_handler |
| タイムアウト | 10秒                           |
| メモリ       | 128 MB                         |

## 環境変数

| キー                   | 必須 | 値の例                                           |
|------------------------|------|--------------------------------------------------|
| LINE_CHANNEL_SECRET    | ✅   | チャンネル基本設定のチャンネルシークレット        |
| CONNECTIONS_TABLE      | ✅   | FamilyChatConnections                            |
| MESSAGES_TABLE         | ✅   | FamilyChatMessages                               |
| API_GW_ENDPOINT        | ✅   | https://xxxxxxxxxx.execute-api.ap-northeast-1.amazonaws.com/prod |
| ROOM_ID                |      | family（デフォルト）                             |
| CHILD_CONNECTION_USER  |      | child（デフォルト）                              |

## IAM ポリシー

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "dynamodb:PutItem",
        "dynamodb:Scan"
      ],
      "Resource": [
        "arn:aws:dynamodb:ap-northeast-1:*:table/FamilyChatConnections",
        "arn:aws:dynamodb:ap-northeast-1:*:table/FamilyChatMessages"
      ]
    },
    {
      "Effect": "Allow",
      "Action": "execute-api:ManageConnections",
      "Resource": "arn:aws:execute-api:ap-northeast-1:*:*/@connections/*"
    },
    {
      "Effect": "Allow",
      "Action": [
        "logs:CreateLogGroup",
        "logs:CreateLogStream",
        "logs:PutLogEvents"
      ],
      "Resource": "arn:aws:logs:*:*:*"
    }
  ]
}
```

## API Gateway HTTP API の設定

WebSocket API とは別に HTTP API を作成します。

| 項目             | 値                              |
|------------------|---------------------------------|
| API タイプ       | HTTP API                        |
| ルート           | POST /line-webhook              |
| 統合             | FamilyLineWebhook Lambda        |

LINE Developers の Webhook URL に設定する値:
```
https://xxxxxxxxxx.execute-api.ap-northeast-1.amazonaws.com/prod/line-webhook
```

## テスト用イベント

### テキストメッセージ（正常系）

```json
{
  "headers": {
    "x-line-signature": "<正しい署名>"
  },
  "body": "{\"events\":[{\"type\":\"message\",\"source\":{\"type\":\"user\",\"userId\":\"Uxxxxxxxxx\"},\"message\":{\"type\":\"text\",\"text\":\"もうすぐかえるよ！\"}}]}"
}
```

### followイベント（ユーザーID確認）

```json
{
  "headers": {
    "x-line-signature": "<正しい署名>"
  },
  "body": "{\"events\":[{\"type\":\"follow\",\"source\":{\"type\":\"user\",\"userId\":\"Uxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx\"}}]}"
}
```

期待される動作: CloudWatch Logs に userId が出力される

### 署名なし（異常系）

```json
{
  "headers": {},
  "body": "{\"events\":[]}"
}
```

期待される動作: 403 Forbidden が返る

## 署名検証について

LINE は全リクエストに `X-Line-Signature` ヘッダーを付与します。
チャンネルシークレットを鍵とした HMAC-SHA256 の Base64 値です。
検証をスキップすると第三者からの偽リクエストを受け付けてしまうため必須です。

テスト時に正しい署名を生成する方法:
```python
import hashlib, hmac, base64, json

secret = "YOUR_CHANNEL_SECRET"
body = json.dumps({"events": [...]})
sig = base64.b64encode(
    hmac.new(secret.encode(), body.encode(), hashlib.sha256).digest()
).decode()
print(sig)
```
