"""
宿泊施設の価格監視スクリプト。楽天トラベルAPI(公式・無料、2026年2月以降の新API)を利用する。

2つのモードに対応:
  1. WATCH_HOTELS  : 特定ホテルを継続ウォッチ
     固定の日程ではなく、「今日からNヶ月後〜Mヶ月間」の範囲内にある
     土日・祝日をすべて候補として自動生成し、その中で最安の1泊を探す。
     (例: 今日から1ヶ月後を起点に、2ヶ月間の範囲の土日祝を全部チェック)
  2. CONDITION_SEARCHES : 駅・地点から徒歩圏内(半径検索)にある宿の最安値を探す
     (駅名はNominatim(OpenStreetMap)でジオコーディングして座標に変換する)

事前準備:
  1. 楽天ウェブサービスに登録し、アプリを作成する（2026年2月以降の新形式）
     https://webservice.rakuten.co.jp/
     - 「許可されたWebサイト」欄には適当なドメイン(例: github.com)を入力
     - 発行される applicationId と accessKey の両方を控える
  2. GitHub Actions の secrets に登録する
     - RAKUTEN_APP_ID
     - RAKUTEN_ACCESS_KEY
     - (許可されたWebサイトをgithub.com以外にした場合のみ RAKUTEN_ALLOWED_SITE も登録)
  3. WATCH_HOTELS / CONDITION_SEARCHES を編集して監視したい内容を設定する
"""
import datetime
import os
import sys
import time

import jpholiday
import requests
from dateutil.relativedelta import relativedelta

sys.path.append(os.path.join(os.path.dirname(__file__)))
from common.line_notify import send_line_message  # noqa: E402
from common.storage import load_json, save_json  # noqa: E402

HISTORY_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "hotel_history.json")

VACANT_HOTEL_SEARCH_URL = "https://openapi.rakuten.co.jp/engine/api/Travel/VacantHotelSearch/20170426"

# API呼び出しの間隔(秒)。連続アクセスによる一時制限を避けるため間を空ける
REQUEST_INTERVAL_SEC = 1.0

# --- モード1: 特定ホテルを継続ウォッチ(土日祝の中で最安の1泊を探す) ---
WATCH_HOTELS = [
    {
        "id": "watch_hotel",
        "label": "旅館 菊屋（静岡伊豆）",
        "hotel_no": "7491",       # 監視したいホテルの楽天トラベルhotelNo
        "adult_num": 2,
        "start_offset_months": 1,  # 今日から何ヶ月後を起点にするか
        "span_months": 1,          # 起点から何ヶ月間を範囲にするか
        "include_holidays": True,  # 祝日も候補に含めるか
        "squeeze_condition": [],   # 例: ["onsen"] 温泉プランのみに絞る場合
        "threshold_price": 30000,  # 1泊あたりこの価格以下になったら通知
    },
]

# --- モード2: 駅・地点からの徒歩圏内で条件に合う宿の最安を探す(固定日程) ---
CONDITION_SEARCHES = [
    {
        "id": "condition_example_area",
        "label": "北海道 (2026-08-28 2泊)",
        "station_query": "すすきの 札幌市",  # 基準にする駅名・地名(ジオコーディングされる)
        "walk_minutes": 10,        # 徒歩何分圏内を検索するか(分速80m換算)
        "checkin": "2026-08-28",
        "checkout": "2026-08-30",
        "adult_num": 4,
        "hits": 10,             # キーワードで拾う候補ホテル数(最大15)
        "squeeze_condition": ["daiyoku", "kinen", "internet"],  # 例: 温泉のみ。複数可: ["onsen","breakfast"]
        "threshold_price": 10000,   # 1泊あたりこの価格以下の宿が見つかったら通知
    },
]


NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
# Nominatimの利用規約上、必ず識別可能なUser-Agentを送る必要がある
NOMINATIM_HEADERS = {"User-Agent": "price-watcher-personal-use/1.0"}


def geocode_place(query: str) -> tuple:
    """駅名・地名から緯度経度(世界測地系, 度)を取得する。見つからなければNoneを返す。"""
    resp = requests.get(
        NOMINATIM_URL,
        params={"q": query, "format": "json", "limit": 1, "countrycodes": "jp"},
        headers=NOMINATIM_HEADERS,
        timeout=15,
    )
    resp.raise_for_status()
    results = resp.json()
    if not results:
        return None
    return float(results[0]["lat"]), float(results[0]["lon"])


def walk_minutes_to_radius_km(minutes: float) -> float:
    """徒歩分数を検索半径(km)に変換する(分速80m換算)。APIの仕様上0.1~3.0kmに収める。"""
    km = round(minutes * 0.08, 1)
    return min(max(km, 0.1), 3.0)


def _rakuten_headers() -> dict:
    referer = os.environ.get("RAKUTEN_ALLOWED_SITE", "https://github.com")
    return {"Referer": referer, "Origin": referer}


def _rakuten_auth_params() -> dict:
    return {
        "applicationId": os.environ["RAKUTEN_APP_ID"],
        "accessKey": os.environ["RAKUTEN_ACCESS_KEY"],
    }


def generate_candidate_dates(start_offset_months: int, span_months: int, include_holidays: bool) -> list:
    """「起点からspan_months間」の範囲内にある土日(・祝日)の日付リストを生成する。"""
    today = datetime.date.today()
    start = today + relativedelta(months=start_offset_months)
    end = start + relativedelta(months=span_months)

    dates = []
    d = start
    while d <= end:
        is_weekend = d.weekday() >= 5  # 5=土, 6=日
        is_holiday = include_holidays and jpholiday.is_holiday(d)
        if is_weekend or is_holiday:
            dates.append(d)
        d += datetime.timedelta(days=1)
    return dates


def fetch_price_for_date(hotel_no: str, adult_num: int, checkin: datetime.date, squeeze_condition: list = None) -> int:
    """指定ホテル・指定チェックイン日(1泊)の最安プラン価格を取得する。空室なしならNoneを返す。"""
    checkout = checkin + datetime.timedelta(days=1)
    params = {
        **_rakuten_auth_params(),
        "format": "json",
        "hotelNo": hotel_no,
        "checkinDate": checkin.isoformat(),
        "checkoutDate": checkout.isoformat(),
        "adultNum": adult_num,
    }
    if squeeze_condition:
        params["squeezeCondition"] = ",".join(squeeze_condition)
    resp = requests.get(VACANT_HOTEL_SEARCH_URL, params=params, headers=_rakuten_headers(), timeout=30)
    resp.raise_for_status()
    data = resp.json()

    hotels = data.get("hotels", [])
    if not hotels:
        return None

    min_charge = None
    for h in hotels:
        for room in h.get("hotel", [])[1:]:  # [0]はホテル基本情報なのでスキップ
            for plan in room.get("roomInfo", []):
                charge = plan.get("dailyCharge", {}).get("total")
                if charge is not None and (min_charge is None or charge < min_charge):
                    min_charge = charge
    return min_charge


def find_cheapest_in_range(hotel: dict) -> dict:
    """設定された日程範囲内の候補日をすべてチェックし、最安の日程を返す。"""
    candidates = generate_candidate_dates(
        hotel.get("start_offset_months", 1),
        hotel.get("span_months", 2),
        hotel.get("include_holidays", True),
    )
    print(f"[INFO] {hotel['label']}: 候補日 {len(candidates)}件をチェックします")

    best = None
    for i, d in enumerate(candidates):
        try:
            price = fetch_price_for_date(
                hotel["hotel_no"], hotel["adult_num"], d, hotel.get("squeeze_condition")
            )
        except Exception as e:
            print(f"[ERROR] {hotel['label']} {d} の取得に失敗: {e}")
            price = None

        if price is not None:
            print(f"[DEBUG] {hotel['label']} {d}({['月','火','水','木','金','土','日'][d.weekday()]}): {price}円")
            if best is None or price < best["price"]:
                best = {"price": int(price), "date": d.isoformat()}

        if i < len(candidates) - 1:
            time.sleep(REQUEST_INTERVAL_SEC)

    return best


def fetch_condition_cheapest(search: dict) -> dict:
    """
    駅・地点から徒歩圏内(半径検索)にある宿の中で、実際の指定日程での最安値を探す。

    駅名などをNominatim(OpenStreetMap)でジオコーディングして緯度経度を求め、
    その座標を中心にVacantHotelSearchの半径検索(searchRadius)で絞り込む。
    """
    coords = geocode_place(search["station_query"])
    if coords is None:
        print(f"[WARN] {search['label']}: 「{search['station_query']}」の座標が見つかりませんでした")
        return None
    lat, lng = coords
    radius_km = walk_minutes_to_radius_km(search["walk_minutes"])

    params = {
        **_rakuten_auth_params(),
        "format": "json",
        "latitude": lat,
        "longitude": lng,
        "searchRadius": radius_km,
        "datumType": 1,  # 1=世界測地系(度)。Nominatimの座標をそのまま使うため
        "checkinDate": search["checkin"],
        "checkoutDate": search["checkout"],
        "adultNum": search["adult_num"],
        "hits": min(search.get("hits", 30), 30),
    }
    squeeze = search.get("squeeze_condition")
    if squeeze:
        params["squeezeCondition"] = ",".join(squeeze)

    resp = requests.get(VACANT_HOTEL_SEARCH_URL, params=params, headers=_rakuten_headers(), timeout=30)
    resp.raise_for_status()
    hotels = resp.json().get("hotels", [])
    if not hotels:
        return None  # 半径内・この日程・この条件では空室なし

    best = None
    for h in hotels:
        basic = h.get("hotel", [{}])[0].get("hotelBasicInfo", {})
        min_charge = None
        for room in h.get("hotel", [])[1:]:
            for plan in room.get("roomInfo", []):
                charge = plan.get("dailyCharge", {}).get("total")
                if charge is not None and (min_charge is None or charge < min_charge):
                    min_charge = charge
        if min_charge is None:
            continue
        if best is None or min_charge < best["price"]:
            best = {
                "price": int(min_charge),
                "name": basic.get("hotelName", ""),
                "url": basic.get("hotelInformationUrl", ""),
            }
    return best


def main():
    history = load_json(HISTORY_PATH)
    updated = False

    # --- モード1: 特定ホテルウォッチ(日程範囲内の最安) ---
    for hotel in WATCH_HOTELS:
        try:
            best = find_cheapest_in_range(hotel)
        except Exception as e:
            print(f"[ERROR] {hotel['label']} の検索に失敗しました: {e}")
            continue

        if best is None:
            print(f"[INFO] {hotel['label']}: 該当期間内に空室なし、または取得失敗")
            continue

        price, best_date = best["price"], best["date"]
        prev = history.get(hotel["id"], {})
        prev_min = prev.get("min_price")
        print(f"[INFO] {hotel['label']}: 現在の最安 {price}円({best_date}) / 過去最安 {prev_min}円")

        is_new_low = prev_min is None or price < prev_min
        is_under_threshold = price <= hotel["threshold_price"]

        if is_under_threshold:
            reason = "🔥目標金額達成"

        elif is_new_low:
            reason = "🎉最安値更新"

        else:
            reason = "📊今回の最安値"

        message = (
            f"【宿・ウォッチ】{hotel['label']}\n"
            f"{reason}\n"
            f"価格: {price:,}円/泊\n"
            f"過去最安: {prev_min if prev_min is not None else '-'}円\n"
            f"日程: {best_date}"
        )

        send_line_message(message)

        if is_new_low:
            history[hotel["id"]] = {"min_price": price, "date": best_date}
            updated = True

    # --- モード2: 条件検索 ---
    for search in CONDITION_SEARCHES:
        try:
            best = fetch_condition_cheapest(search)
        except Exception as e:
            print(f"[ERROR] {search['label']} の検索に失敗しました: {e}")
            continue

        if best is None:
            print(f"[INFO] {search['label']}: 該当宿なし")
            continue

        price = best["price"]
        prev = history.get(search["id"], {})
        prev_min = prev.get("min_price")
        print(f"[INFO] {search['label']}: 最安 {price}円({best['name']}) / 過去最安 {prev_min}円")

        is_new_low = prev_min is None or price < prev_min
        is_under_threshold = price <= search["threshold_price"]

        if is_under_threshold:
            reason = "🔥目標金額達成"

        elif is_new_low:
            reason = "🎉最安値更新"

        else:
            reason = "📊今回の最安値"

        message = (
            f"【宿・条件検索】{search['label']}\n"
            f"{reason}\n"
            f"価格: {price:,}円/泊\n"
            f"過去最安: {prev_min if prev_min is not None else '-'}円\n"
            f"{best['name']}\n"
            f"{best['url']}"
        )

        send_line_message(message)

        if is_new_low:
            history[search["id"]] = {"min_price": price}
            updated = True

    if updated:
        save_json(HISTORY_PATH, history)
        print("[INFO] 履歴を更新しました")
    else:
        print("[INFO] 履歴の更新はありません")


if __name__ == "__main__":
    main()