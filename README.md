# FamilyChat

子供（iPad Webアプリ）と親（LINE）をリアルタイムでつなぐ家族専用プライベートチャットアプリ。

## 特徴

- AWS（API Gateway WebSocket・Lambda・DynamoDB・Cognito・CloudFront）によるフルサーバーレス構成
- API Gateway WebSocket によるリアルタイム双方向通信。メッセージが即座に届く
- 月間コストはほぼ無料
- 子どもはカラフルな吹き出しとスタンプで直感的にメッセージを送れ、親はLINEグループで受け取り・返信できる
- Cognito認証により家族以外はアクセスできない設計
- スタンプ（絵文字30種類）対応

## サンプル画面

![サンプル画面](https://github.com/JuvenileTalk9/FamilyChat/blob/main/img/sample.png)
