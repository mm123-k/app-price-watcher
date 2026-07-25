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
        "id": "tokyo_yamaguchi",
        "label": "東京 → 山口",
        "origin_sky_ids": ["HND", "NRT"],  # 出発地のSky ID (IATAコードでOKな場合が多い)
        "dest_sky_ids": ["UBJ"],           # 目的地のSky ID
        "start_offset_months": 2,
        "span_months": 1,
        "trip_type": "weekend",       # 土日・祝日検索
        "origin_entity_id": "",       # 提供元APIの仕様に応じて必要なら埋める
        "dest_entity_id": "",
        "cabin_class": "economy",
        "currency": "JPY",
        "market": "JP",
        "threshold_price": 20000,     # この価格以下になったら通知
    },
]

import datetime
from dateutil.relativedelta import relativedelta
import jpholiday


def generate_candidate_trips(start_offset_months, span_months):
    today = datetime.date.today()

    start = today + relativedelta(months=start_offset_months)
    end = start + relativedelta(months=span_months)

    trips = []

    d = start
    while d <= end:

        # 土曜日
        if d.weekday() == 5:

            # 月曜が祝日なら3連休
            monday = d + datetime.timedelta(days=2)

            if jpholiday.is_holiday(monday):
                trips.append({
                    "depart": d.isoformat(),
                    "return": monday.isoformat(),
                })
            else:
                trips.append({
                    "depart": d.isoformat(),
                    "return": (d + datetime.timedelta(days=1)).isoformat(),
                })

        d += datetime.timedelta(days=1)

    return trips

import datetime
import jpholiday

WEEKDAYS = ["月", "火", "水", "木", "金", "土", "日"]


def format_date(date_str):
    d = datetime.date.fromisoformat(date_str)
    weekday = WEEKDAYS[d.weekday()]

    if jpholiday.is_holiday(d):
        return f"{d:%m/%d}({weekday}・祝)"

    return f"{d:%m/%d}({weekday})"

def fetch_cheapest_price(origin, dest, depart, return_date, route):
    """指定路線の最安値を取得する。戻り値: {"price": int, "url": str} または None"""
    host = os.environ["RAPIDAPI_HOST"]
    key = os.environ["RAPIDAPI_KEY"]

    params = {
        "originSkyId": origin,
        "destinationSkyId": dest,
        "date": depart,
        "returnDate": return_date,

        "cabinClass": route["cabin_class"],
        "adults": "1",
        "currency": route["currency"],
        "market": route["market"],
    }
    if route.get("origin_entity_id"):
        params["originEntityId"] = route["origin_entity_id"]
    if route.get("dest_entity_id"):
        params["destinationEntityId"] = route["dest_entity_id"]

    resp = requests.get(
        f"https://{host}/api/v2/flights/searchFlights",
        headers={"x-rapidapi-key": key, "x-rapidapi-host": host},
        params=params,
        timeout=30,
    )
    print(resp.status_code)
    print(resp.text)
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
            trips = generate_candidate_trips(
                route["start_offset_months"],
                route["span_months"],
            )

            results = []

            for trip in trips:
                for origin in route["origin_sky_ids"]:
                    for dest in route["dest_sky_ids"]:

                        try:
                            result = fetch_cheapest_price(
                                origin,
                                dest,
                                trip["depart"],
                                trip["return"],
                                route,
                            )

                            if result:
                                result["origin"] = origin
                                result["dest"] = dest
                                result["depart"] = trip["depart"]
                                result["return"] = trip["return"]
                                results.append(result)

                        except Exception as e:
                            print(f"[WARN] {origin}->{dest} {trip['depart']} の取得失敗: {e}")

            if not results:
                print(f"[WARN] {route['label']} は候補が見つかりませんでした")
                continue

            result = min(results, key=lambda x: x["price"])

            price = result["price"]
            url = result["url"]

            prev = history.get(route["id"], {})
            prev_min = prev.get("min_price")

            print(
                f"[INFO] {route['label']}: "
                f"{result['origin']}→{result['dest']} "
                f"{result['depart']}〜{result['return']} "
                f"{price}円 / 過去最安 {prev_min}"
            )

            is_new_low = prev_min is None or price < prev_min
            is_under_threshold = price <= route["threshold_price"]

            if is_under_threshold:
                reason = "🔥目標価格達成"
            elif is_new_low:
                reason = "🎉最安値更新"
            else:
                reason = "📊今回の最安値"

            message = (
                f"【航空券】{route['label']}\n"
                f"{reason}\n"
                f"{result['origin']}→{result['dest']}\n"
                f"{format_date(result['depart'])} ～ {format_date(result['return'])}\n"
                f"{price:,}円\n"
                f"過去最安: {prev_min if prev_min is not None else '-'}円"
            )

            if url:
                message += f"\n{url}"

            send_line_message(message)

            if is_new_low:
                history[route["id"]] = {
                    "min_price": price,
                    "url": url,
                    "origin": result["origin"],
                    "dest": result["dest"],
                    "depart": result["depart"],
                    "return": result["return"],
                }
                updated = True

        except Exception as e:
            print(f"[ERROR] {route['label']} の検索に失敗しました: {e}")
            continue

    if updated:
        save_json(HISTORY_PATH, history)
        print("[INFO] 履歴を更新しました")
    else:
        print("[INFO] 履歴の更新はありません")


if __name__ == "__main__":
    main()
