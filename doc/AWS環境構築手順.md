# AWS環境構築手順

## S3

- S3バケットを作成する
    - バケット名：一意な名称
    - パブリックアクセス：`すべてブロック`
- HTMLファイルをバケットにアップロードする
    - ファイル名：`index.html`
    - メタデータ
        - タイプ：`システム定義`
        - キー：`Cache-Control`
        - 値：`no-cache`

### CloudFront

- ディストリビューションを作成する
    - プラン：`無料`
    - ディストリビューション名：`FamilyChat`
    - ドメイン：`なし`
    - オリジンタイプ：`Amazon S3`
    - S3オリジン：作成したS3バケット
    - デフォルトルートオブジェクト：`index.html`
    - エラーページ：403/404 → `/index.html`

## Cognito

- ユーザプールを新規作成する
    - ユーザプール名：`Family Chat User Pool`
    - アプリケーションタイプ：シングルページアプリケーション
    - 自己登録：`無効化`
    - サインイン識別子のオプション：ユーザ名
    - サインアップのための必須属性：メールアドレス
- アプリクライアントを追加する
    - クライアント名：`FamliyChat`
    - 許可されているコールバックURL：CloudFrontのURL
    - 許可されているサインアウトURL：CloudFrontのURL
    - OAuth2.0付与タイプ：認証コード付与
    - OpenID Connectのスコープ：OpenID
- ユーザプールに人数分のユーザを追加する

※アプリケーションがクライアントシークレットに対応していないためシングルページアプリケーションを選択した

## DynamoDB

### メッセージデータ管理用テーブル

- メッセージデータ管理用テーブルを新規作成する
    - テーブル名：`FamilyChatMessages`
    - パーテーションキー：`roomId`（string）
    - ソートキー：`createdAt`（string）

### 接続管理用テーブル

- 接続管理用テーブルを新規作成する
    - テーブル名：`FamilyChatConnections`
    - パーテーションキー：`userId`（string）
- 属性を追加する
    - `userId`
    - `ttl`
- TTLを有効化する
    - TTL属性名：`ttl`

## IAM

- Lambda用で使用する共通のIAMロールを作成する
    - ロール名：`FamilyChatLambdaRole`
    - 信頼されたエンティティタイプ：`Lambda`
    - アタッチするポリシー
        - `AmazonDynamoDBFullAccess`
        - `AWSLambdaBasicExecutionRole`

- API Gatewayのオーソライザ用のLambdaのIAMロールを作成する
    - ロール名：`FamilyChatAuthorizerLambdaRole`
    - 信頼されたエンティティタイプ：`Lambda`
    - アタッチするポリシー
        - インラインポリシー
        ```json
        {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Sid": "VisualEditor0",
                    "Effect": "Allow",
                    "Action": "cognito-idp:GetUser",
                    "Resource": "*"
                }
            ]
        }
        ```
    
## LINE Developrs

- LINE Developersにログイン
- プロバイダを作成
- チャンネルを作成
    - チャンネルの種類：`Messaging API`
- `Channel secret`を控えておく
- チャンネルアクセストークン（長期）を発行し、控えておく

## Lambda

### メッセージ受信ハンドラ

- Lambda関数を作成する
    - 関数名：`FamilyChatHandler`
    - ロール：`FamilyChatLambdaRole`
    - 環境変数
        |キー|値|
        |:--|:--|
        |`CONNECTIONS_TABLE`|`FamilyChatConnections`|
        |`MESSAGES_TABLE`|`FamilyChatMessages`|
        |`LINE_FUNCTION_NAME`|`FamilyLineNotify`|
        |`ROOM_ID`|`family`|
        |`API_GW_ENDPOINT`|`http`で始まり`/prod`で終わるAPI Gatewayのエンドポイント|
    - ライムアウト：`10秒`

### LINE通知ハンドラ

- Lambda関数を作成する
    - 関数名：`FamilyChatHandler`
    - ロール：`FamilyChatLambdaRole`
    - 環境変数
        |キー|値|
        |:--|:--|
        |`LINE_CHANNEL_ACCESS_TOKEN`|LINE Developersから取得|

### LINE通知ハンドラ

- Lambda関数を作成する
    - 関数名：`FamilyChatHandler`
    - ロール：`FamilyChatLambdaRole`
    - 環境変数
        |キー|値|
        |:--|:--|
        |`LINE_CHANNEL_ACCESS_TOKEN`|LINE Developersから取得|

### LINE受信ハンドラ

- Lambda関数を作成する
    - 関数名：`FamliyLineWebhook`
    - ロール：`FamilyChatLambdaRole`
    - 環境変数
        |キー|値|
        |:--|:--|
        |`LINE_CHANNEL_SECRET`|チャンネル基本設定のチャンネルシークレット|
        |`CONNECTIONS_TABLE`|`FamilyChatConnections`|
        |`MESSAGES_TABLE`|`FamilyChatMessages`|
        |`API_GW_ENDPOINT`|`http`で始まり`/prod`で終わるAPI Gatewayのエンドポイント|
        |`ROOM_ID`|`family`|
        |`CHILD_CONNECTION_USER`|`child`|
    - ライムアウト：`10秒`

### オーソライザ

- Lambda関数を作成する
    - 関数名：`FamilyChatAuthorizer`
    - ロール：`FamilyChatAuthorizerLambdaRole`
    - 環境変数
        |キー|値|
        |:--|:--|
        |`COGNITO_USER_POOL_ID`|CognitoユーザプールID|
        |`COGNITO_APP_CLIENT_ID`|CognitoアプリクライアントのクライアントID|
        |`AWS_REGION_NAME`|`ap-northeast-1`|
     ライムアウト：`5秒`
- 関数と外部依存ライブラリをzipに固めてアップロード
    ```bash
    # 1. 作業ディレクトリを作成
    mkdir authorizer_pkg && cd authorizer_pkg

    # 2. lambda_function.py をコピー
    cp /path/to/lambda_function.py .

    # 3. 依存ライブラリをカレントディレクトリにインストール
    pip install \
    "PyJWT[crypto]" \
    "cryptography" \
    "requests" \
    --target . \
    --platform manylinux2014_x86_64 \
    --python-version 3.14 \
    --implementation cp \
    --only-binary=:all:

    # 4. ZIPに固める
    zip -r ../authorizer.zip .
    ```

## CloudWatch Logs

- ロググループを作成する
    - ロググループ名：`/aws/apigateway/family-chat`
    - 保持期間：`1か月`
- ロググループを作成する
    - ロググループ名：`/aws/apigateway/family-chat-linewebhook`
    - 保持期間：`1か月`

## API Gateway

### iPad端末と通信するWebSocket API

- API Gatewayを作成する 
    - APIタイプ：`WebSocket API`
    - API名：`FamilyChatWS`
    - ルート選択式：`request.body.action`
    - ルート（すべて統合は`FamilyChatHandler`）
        - `$connect`
        - `$disconnect`
        - `$default`
        - `sendMessage`
    - ステージ名：`prod`
- オーソライザを作成する
    - オーソライザ名：`FamilyChatAuthorizer`
    - Lambda関数：作成した`FamilyChatAuthorizer`関数
    - IDソースタイプ：クエリ文字列
    - キー：`token`
- ログとトレースを編集
    - ログ：`エラーと情報ログ`
    - カスタムのアクセスログ：`有効`
    - アクセスログの送信先ARN：ロググループ

※JavaScriptとWebSocketAPIはカスタムヘッダを付与できないため、トークンをクエリパラメータで渡している

```js
// 例
const ws = new WebSocket(`wss://xxx.amazonaws.com/prod?token=${idToken}`);
```

### LINEと通信するHTTP API

- API Gatewayを作成する 
    - APIタイプ：`HTTP API`
    - API名：`FamilyChatLineWebhook`
    - 統合：`FamliyLineWebhook`
    - ルート
        - メソッド：`POST`
        - リソースパス：`/line-webhook`
        - 統合ターゲット：`FamliyLineWebhook`
    - ステージ名：`prod`

## メモ

- Lambdaに渡されるユーザ名がcognitoのログインユーザ担ってる