"""
航空券の価格監視スクリプト。

複数路線を横断して価格を取得するため、Skyscanner公式APIの代わりに
RapidAPI上の集約API(例: Sky Scrapper API等)を利用する構成にしてある。
Skyscanner公式APIは旅行事業者向けパートナー限定で個人利用不可のため。

事前準備:
  1. RapidAPIでアカウントを作成し、Skyscanner系の集約API(例: Sky Scrapper API)を
     Subscribeする(無料枠あり)
  2. 発行された x-rapidapi-key を GitHub Actions の secrets に
     RAPIDAPI_KEY として登録する
  3. 使用するAPIのホスト名を RAPIDAPI_HOST として登録する
     (例: "sky-scrapper.p.rapidapi.com" ※提供元によって異なるので
      RapidAPIのダッシュボードで確認すること)
  4. ROUTES に監視したい路線を追加する

注意:
  RapidAPI上の集約APIはSkyscanner本体のAPIとは別の非公式ラッパーであり、
  提供元によってレスポンスの形式(JSONのキー名など)が異なる。
  parse_cheapest_price() は代表的な形式を想定した実装なので、
  実際に叩いてみて構造が違う場合はここを調整すること。
  (実行時にデバッグ用のrawレスポンスをprintしているので、
   最初の数回はActionsのログで形を確認するとよい)
"""
import os
import sys

import requests

sys.path.append(os.path.join(os.path.dirname(__file__)))
from common.line_notify import send_line_message  # noqa: E402
from common.storage import load_json, save_json  # noqa: E402

HISTORY_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "flight_history.json")

# ここに監視したい路線を追加していく
ROUTES = [
    {
        "id": "haneda_itami_0801",
        "label": "羽田 → 伊丹 (2026-08-01)",
        "origin_sky_id": "HND",       # 出発地のSky ID (IATAコードでOKな場合が多い)
        "dest_sky_id": "ITM",         # 到着地のSky ID
        "origin_entity_id": "",       # 提供元APIの仕様に応じて必要なら埋める
        "dest_entity_id": "",
        "date": "2026-08-01",
        "return_date": None,          # 往復なら "2026-08-05" のように指定
        "cabin_class": "economy",
        "currency": "JPY",
        "market": "JP",
        "threshold_price": 15000,     # この価格以下になったら通知
    },
]


def fetch_cheapest_price(route: dict) -> dict:
    """指定路線の最安値を取得する。戻り値: {"price": int, "url": str} または None"""
    host = os.environ["RAPIDAPI_HOST"]
    key = os.environ["RAPIDAPI_KEY"]

    params = {
        "originSkyId": route["origin_sky_id"],
        "destinationSkyId": route["dest_sky_id"],
        "date": route["date"],
        "cabinClass": route["cabin_class"],
        "adults": "1",
        "currency": route["currency"],
        "market": route["market"],
    }
    if route.get("origin_entity_id"):
        params["originEntityId"] = route["origin_entity_id"]
    if route.get("dest_entity_id"):
        params["destinationEntityId"] = route["dest_entity_id"]
    if route.get("return_date"):
        params["returnDate"] = route["return_date"]

    resp = requests.get(
        f"https://{host}/api/v1/flights/searchFlights",
        headers={"x-rapidapi-key": key, "x-rapidapi-host": host},
        params=params,
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()

    # デバッグ用：最初の設定時はここでレスポンス形式を確認する
    print(f"[DEBUG] {route['label']} raw response keys: {list(data.keys())}")

    itineraries = (
        data.get("data", {}).get("itineraries")
        or data.get("itineraries")
        or []
    )
    if not itineraries:
        return None

    cheapest = min(
        itineraries,
        key=lambda it: it.get("price", {}).get("raw", float("inf")),
    )
    price = cheapest.get("price", {}).get("raw")
    url = cheapest.get("deeplink") or cheapest.get("url") or ""
    if price is None:
        return None
    return {"price": int(price), "url": url}


def main():
    history = load_json(HISTORY_PATH)
    updated = False

    for route in ROUTES:
        try:
            result = fetch_cheapest_price(route)
        except Exception as e:
            print(f"[ERROR] {route['label']} の価格取得に失敗しました: {e}")
            continue

        if result is None:
            print(f"[WARN] {route['label']} は候補が見つかりませんでした")
            continue

        price, url = result["price"], result["url"]
        prev = history.get(route["id"], {})
        prev_min = prev.get("min_price")

        print(f"[INFO] {route['label']}: 現在 {price}円 / 過去最安 {prev_min}円")

        is_new_low = prev_min is None or price < prev_min
        is_under_threshold = price <= route["threshold_price"]

        if is_new_low or is_under_threshold:
            reason = "過去最安値を更新" if is_new_low else "閾値以下"
            message = (
                f"【航空券】{route['label']}\n"
                f"{reason}: {price:,}円\n"
                f"{url if url else ''}"
            ).strip()
            send_line_message(message)

        if is_new_low:
            history[route["id"]] = {"min_price": price, "url": url}
            updated = True

    if updated:
        save_json(HISTORY_PATH, history)
        print("[INFO] 履歴を更新しました")
    else:
        print("[INFO] 履歴の更新はありません")


if __name__ == "__main__":
    main()
