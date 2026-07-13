# price-watcher

航空券・宿泊費が安くなったらLINEに通知するボット。GitHub Actionsで定期実行する。

## 構成

- `scripts/flight_watch.py` … 航空券の価格監視（複数路線対応、RapidAPI経由のSkyscanner系集約APIを利用）
- `scripts/hotel_watch.py` … 宿泊費の価格監視（楽天トラベルAPI利用、特定ホテルのウォッチ＋エリア条件検索の両対応）
- `scripts/common/` … LINE通知・履歴保存の共通処理
- `data/*.json` … これまでの最安値の履歴（ワークフローが自動でcommitして更新）
- `.github/workflows/` … 定期実行の設定（cronスケジュール）

## セットアップ手順

### 1. リポジトリを作成してこの内容をpush

このリポジトリの内容をそのまま自分のGitHubリポジトリにpushする。

### 2. LINE Messaging APIの準備（両方で共通）

LINE Notifyは2025年3月に終了しているため、Messaging APIを使う。
userIdの取得が難しいため、本スクリプトは「Broadcast(友だち全員に送信)」方式を採用しており、
**userIdの取得は不要**。

1. [LINE Developers](https://developers.line.biz/) でプロバイダー・チャネル(Messaging API)を作成
2. 「チャネルアクセストークン（長期）」を発行
3. 作成した公式アカウントを自分のLINEで友だち追加
   （このアカウントの友だちが自分だけの状態を保つこと。他の人にも
    追加されるとその人にもメッセージが届いてしまうので注意）
4. GitHubリポジトリの Settings → Secrets and variables → Actions で以下を登録
   - `LINE_CHANNEL_ACCESS_TOKEN`

無料枠は月200通程度。個人の価格アラート用途なら通常十分収まる。

### 3. 航空券監視の準備

1. [RapidAPI](https://rapidapi.com/) でアカウント作成
2. Skyscanner系の集約API（例: "Sky Scrapper" など）を検索してSubscribe（無料プランあり）
3. ダッシュボードで `x-rapidapi-key` と対象APIのホスト名を確認
4. GitHub Secretsに登録
   - `RAPIDAPI_KEY`
   - `RAPIDAPI_HOST`
5. `scripts/flight_watch.py` の `ROUTES` に監視したい路線・日程・閾値を追加

⚠️ 提供元によってレスポンスのJSON構造が異なることがある。初回実行時はActionsのログに出る
`[DEBUG] ... raw response keys` を見て、`parse` 部分（`fetch_cheapest_price`関数）を
実際のレスポンス構造に合わせて調整すること。

### 4. 宿泊監視の準備

1. [楽天ウェブサービス](https://webservice.rakuten.co.jp/) でアプリID発行（無料）
2. GitHub Secretsに登録
   - `RAKUTEN_APP_ID`
3. `scripts/hotel_watch.py` を編集
   - 特定ホテルを継続ウォッチしたい → `WATCH_HOTELS` に `hotel_no` と日程・閾値を追加
     （hotel_noは楽天トラベルのホテルページや検索結果から確認できる）
   - エリア条件で最安宿を探したい → `CONDITION_SEARCHES` にキーワード・日程・閾値を追加

### 5. 動作確認

各ワークフローは `workflow_dispatch` に対応しているので、GitHubの Actions タブから
手動実行して、LINEに通知が届くか・エラーが出ないかを確認できる。
定期実行のcron間隔は `.github/workflows/*.yml` 内で調整可能。

## 通知条件

各路線・宿ごとに以下のどちらかに該当すると通知する。

- 過去の履歴上の最安値を更新したとき
- 設定した `threshold_price`（閾値）以下になったとき

## 注意事項

- スクレイピング/API利用は各サービスの利用規約の範囲内で行うこと
- 無料APIプランのリクエスト数上限に注意し、路線数・実行頻度を調整すること
- 価格は変動が激しいため、これは参考情報であり実際の予約前に必ず公式サイトで最終確認すること
