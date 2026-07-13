"""
LINE Messaging API でメッセージを送信する共通モジュール。

userIdの取得が難しいため、「Broadcast(友だち全員に送信)」方式を採用している。
このbotの友だちが自分だけであれば、Push方式(userId指定)と実質同じ結果になる。

事前準備:
  1. LINE公式アカウントを作成し、Messaging APIのチャネルを有効化する
  2. 「チャネルアクセストークン(長期)」を発行する
  3. 作成した公式アカウントを自分のLINEで友だち追加する
     (友だちが自分だけの状態を保つこと。他の人にも追加されると
      その人にも通知が届いてしまうので注意)
  4. GitHub Actionsのsecretsに登録する
     - LINE_CHANNEL_ACCESS_TOKEN

  ※ userIdは不要。
"""
import os
import requests

LINE_BROADCAST_URL = "https://api.line.me/v2/bot/message/broadcast"


def send_line_message(text: str) -> None:
    """LINE公式アカウントの友だち全員にテキストメッセージを送信する。"""
    token = os.environ["LINE_CHANNEL_ACCESS_TOKEN"]

    # LINE Messaging APIは1メッセージ最大5000文字だが、通知用途なので念のため切り詰める
    text = text[:4900]

    resp = requests.post(
        LINE_BROADCAST_URL,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        },
        json={
            "messages": [{"type": "text", "text": text}],
        },
        timeout=15,
    )

    if resp.status_code != 200:
        # 通知自体の失敗はワークフローを止めるほどではないのでログに残すだけにする
        print(f"[LINE通知エラー] status={resp.status_code} body={resp.text}")
    else:
        print("[LINE通知] 送信成功")
