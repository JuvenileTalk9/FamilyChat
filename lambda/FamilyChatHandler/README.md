# FamilyChatHandler デプロイ手順

## ファイル構成

```
lambda_family_chat_handler/
├── lambda_function.py   # Lambda本体
└── requirements.txt     # 外部依存（現在は空）
```

## 1. Lambdaコンソールでの設定

| 項目 | 値 |
|------|-----|
| ランタイム | Python 3.12 |
| ハンドラ | lambda_function.lambda_handler |
| タイムアウト | 10秒 |
| メモリ | 128 MB（十分）|

## 2. 環境変数

| キー | 値の例 |
|------|--------|
| CONNECTIONS_TABLE | FamilyChatConnections |
| MESSAGES_TABLE | FamilyChatMessages |
| LINE_FUNCTION_NAME | FamilyLineNotify |
| ROOM_ID | family |
| API_GW_ENDPOINT | https://xxxxxxxxxx.execute-api.ap-northeast-1.amazonaws.com/prod |

## 3. IAMロールに必要な権限

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "dynamodb:PutItem",
        "dynamodb:GetItem",
        "dynamodb:DeleteItem",
        "dynamodb:Query",
        "dynamodb:Scan"
      ],
      "Resource": [
        "arn:aws:dynamodb:ap-northeast-1:*:table/FamilyChatConnections",
        "arn:aws:dynamodb:ap-northeast-1:*:table/FamilyChatMessages"
      ]
    },
    {
      "Effect": "Allow",
      "Action": "lambda:InvokeFunction",
      "Resource": "arn:aws:lambda:ap-northeast-1:*:function:FamilyLineNotify"
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

## 4. ZIPでアップロードする場合

```bash
zip lambda.zip lambda_function.py
aws lambda update-function-code \
  --function-name FamilyChatHandler \
  --zip-file fileb://lambda.zip
```

## 5. API Gateway WebSocket ルート設定

| ルートキー | 統合 Lambda |
|-----------|------------|
| $connect | FamilyChatHandler |
| $disconnect | FamilyChatHandler |
| sendMessage | FamilyChatHandler |
| $default | FamilyChatHandler |

## 6. Webアプリ側のメッセージ送信フォーマット

```json
{
  "action": "sendMessage",
  "text": "こんにちは！",
  "isStamp": false
}
```

$connect 時のURLにuserIdをクエリパラメータで渡す:
```
wss://xxx.execute-api.ap-northeast-1.amazonaws.com/prod?userId=child&token=<CognitoIdToken>
```
