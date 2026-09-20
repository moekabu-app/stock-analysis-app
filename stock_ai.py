import yfinance as yf
import pandas as pd
import numpy as np
import os
from datetime import datetime, timedelta
from pathlib import Path


# Streamlit Cloudから呼び出す時は、ExcelやCSVを保存しない。
# 計算結果の標準出力だけを親アプリへ返す。
if os.getenv("STOCK_AI_SKIP_EXPORT") == "1":
    def _skip_export(*args, **kwargs):
        return None

    pd.DataFrame.to_excel = _skip_export
    pd.DataFrame.to_csv = _skip_export

# =========================================================
# 基本設定
# =========================================================
MAX_SIMILAR = 30
YEARS = 5

ZONE_WIDTH_PERCENT = 0.35
MAIN_ZONE_MIN_SCORE = 5
MAIN_ZONE_MAX_ATR = 1.5


# =========================================================
# 共通関数
# =========================================================
def flatten_columns(df):
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    return df


def weighted_rate(similar, condition):
    total_weight = similar["重み"].sum()

    if total_weight <= 0:
        return 0.0

    target_weight = similar.loc[
        condition,
        "重み"
    ].sum()

    return target_weight / total_weight * 100


def market_score(change):

    if change is None or pd.isna(change):
        return 0

    if change >= 1.0:
        return 2

    elif change >= 0.30:
        return 1

    elif change <= -1.0:
        return -2

    elif change <= -0.30:
        return -1

    return 0


def environment_text(score):

    if score >= 2:
        return "↑ 強い追い風"

    elif score == 1:
        return "↑ 追い風"

    elif score <= -2:
        return "↓ 強い逆風"

    elif score == -1:
        return "↓ 逆風"

    return "→ 中立"


# =========================================================
# 地合いデータ取得
# =========================================================
def get_market_daily(symbol, name):

    try:
        df = yf.download(
            symbol,
            period="10d",
            interval="1d",
            auto_adjust=False,
            progress=False,
            repair=True
        )

        df = flatten_columns(df)

        if df.empty or "Close" not in df.columns:
            return None

        df = df.dropna(subset=["Close"])

        if len(df) < 2:
            return None

        latest = float(df["Close"].iloc[-1])
        previous = float(df["Close"].iloc[-2])

        change = (
            (latest - previous)
            / previous * 100
        )

        latest_date = df.index[-1]

        return {
            "name": name,
            "symbol": symbol,
            "date": latest_date,
            "close": latest,
            "previous": previous,
            "change": change
        }

    except Exception:
        return None


def get_futures_price():

    try:
        df = yf.download(
            "NKD=F",
            period="5d",
            interval="1h",
            auto_adjust=False,
            progress=False,
            repair=True
        )

        df = flatten_columns(df)

        if df.empty or "Close" not in df.columns:
            return None

        df = df.dropna(subset=["Close"])

        if len(df) == 0:
            return None

        return {
            "price": float(df["Close"].iloc[-1]),
            "date": df.index[-1]
        }

    except Exception:
        return None


# =========================================================
# 銘柄入力
# =========================================================
code = input(
    "銘柄コードを入力してください: "
).strip()

if code.upper().endswith(".T"):
    code = code[:-2]

ticker = code + ".T"


# =========================================================
# 会社名取得
# =========================================================
company_name = "会社名取得不可"

try:
    ticker_object = yf.Ticker(ticker)
    info = ticker_object.info

    company_name = (
        info.get("shortName")
        or info.get("longName")
        or "会社名取得不可"
    )

except Exception:
    company_name = "会社名取得不可"


print()
print("株価データを取得しています...")
print("銘柄コード :", code)
print("会社名     :", company_name)
print("取得銘柄   :", ticker)


# =========================================================
# 個別株データ取得
# =========================================================
today = datetime.now()

end_date = today + timedelta(days=1)
start_date = today - timedelta(
    days=365 * YEARS + 30
)

data = yf.download(
    ticker,
    start=start_date.strftime("%Y-%m-%d"),
    end=end_date.strftime("%Y-%m-%d"),
    interval="1d",
    auto_adjust=False,
    progress=False,
    repair=True
)

data = flatten_columns(data)

if data.empty:
    print("\n株価データを取得できませんでした。")
    input("\nEnterキーを押して終了してください。")
    exit()

data = data.dropna(
    subset=[
        "Open",
        "High",
        "Low",
        "Close",
        "Volume"
    ]
)


# =========================================================
# テクニカル指標
# =========================================================
data["5日移動平均"] = (
    data["Close"].rolling(5).mean()
)

data["25日移動平均"] = (
    data["Close"].rolling(25).mean()
)

data["5日乖離率"] = (
    (
        data["Close"]
        - data["5日移動平均"]
    )
    / data["5日移動平均"]
    * 100
)

data["25日乖離率"] = (
    (
        data["Close"]
        - data["25日移動平均"]
    )
    / data["25日移動平均"]
    * 100
)


# RSI
delta = data["Close"].diff()

gain = delta.clip(lower=0)
loss = -delta.clip(upper=0)

avg_gain = gain.rolling(14).mean()
avg_loss = loss.rolling(14).mean()

rs = avg_gain / avg_loss

data["RSI"] = (
    100
    - (
        100
        / (1 + rs)
    )
)


# 出来高
data["5日平均出来高"] = (
    data["Volume"].rolling(5).mean()
)

data["出来高倍率"] = (
    data["Volume"]
    / data["5日平均出来高"]
)


# ATR
previous_close = data["Close"].shift(1)

tr1 = data["High"] - data["Low"]
tr2 = (
    data["High"]
    - previous_close
).abs()

tr3 = (
    data["Low"]
    - previous_close
).abs()

true_range = pd.concat(
    [
        tr1,
        tr2,
        tr3
    ],
    axis=1
).max(axis=1)

data["ATR14"] = (
    true_range
    .rolling(14)
    .mean()
)

data["ATR率"] = (
    data["ATR14"]
    / data["Close"]
    * 100
)


# 移動平均変化率
data["5日線変化率"] = (
    data["5日移動平均"]
    .pct_change()
    * 100
)

data["25日線変化率"] = (
    data["25日移動平均"]
    .pct_change()
    * 100
)


# 当日騰落率
data["当日騰落率"] = (
    data["Close"]
    .pct_change()
    * 100
)


# =========================================================
# 翌日結果
# =========================================================
data["翌日始値"] = (
    data["Open"].shift(-1)
)

data["翌日高値"] = (
    data["High"].shift(-1)
)

data["翌日安値"] = (
    data["Low"].shift(-1)
)

data["翌日終値"] = (
    data["Close"].shift(-1)
)

data["翌日終値騰落率"] = (
    (
        data["翌日終値"]
        - data["Close"]
    )
    / data["Close"]
    * 100
)

data["翌日寄付後最大上昇率"] = (
    (
        data["翌日高値"]
        - data["翌日始値"]
    )
    / data["翌日始値"]
    * 100
)

data["翌日寄付後最大下落率"] = (
    (
        data["翌日安値"]
        - data["翌日始値"]
    )
    / data["翌日始値"]
    * 100
)

data["翌日GU_GD率"] = (
    (
        data["翌日始値"]
        - data["Close"]
    )
    / data["Close"]
    * 100
)


# =========================================================
# 最新値
# =========================================================
latest = data.iloc[-1]
latest_date = data.index[-1]

close_price = float(latest["Close"])
high_price = float(latest["High"])
low_price = float(latest["Low"])

ma5 = float(latest["5日移動平均"])
ma25 = float(latest["25日移動平均"])

rsi = float(latest["RSI"])
atr = float(latest["ATR14"])

volume_ratio = float(
    latest["出来高倍率"]
)


# =========================================================
# 高値安値
# =========================================================
five_day_high = float(
    data["High"].iloc[-5:].max()
)

five_day_low = float(
    data["Low"].iloc[-5:].min()
)

twenty_day_high = float(
    data["High"].iloc[-20:].max()
)

twenty_day_low = float(
    data["Low"].iloc[-20:].min()
)


# =========================================================
# 類似局面
# =========================================================
features = [
    "RSI",
    "5日乖離率",
    "25日乖離率",
    "出来高倍率",
    "ATR率",
    "5日線変化率",
    "25日線変化率",
    "当日騰落率"
]

history = data.iloc[:-1].copy()

history = history.dropna(
    subset=features + [
        "翌日終値騰落率",
        "翌日寄付後最大上昇率",
        "翌日寄付後最大下落率",
        "翌日GU_GD率"
    ]
)

if len(history) == 0:
    print("\n類似局面を計算できませんでした。")
    input("\nEnterキーを押して終了してください。")
    exit()


distance = pd.Series(
    0.0,
    index=history.index
)

for feature in features:

    std = history[feature].std()

    if pd.isna(std) or std == 0:
        continue

    difference = (
        history[feature]
        - latest[feature]
    ) / std

    distance += difference ** 2


history["類似距離"] = np.sqrt(
    distance
)

history = history.sort_values(
    "類似距離"
)

similar = history.head(
    MAX_SIMILAR
).copy()

similar["類似度"] = (
    100
    / (
        1
        + similar["類似距離"]
    )
)

similar["重み"] = (
    similar["類似度"]
    / 100
) ** 2

similar_count = len(similar)


# =========================================================
# 翌日分類
# =========================================================
similar["翌日分類"] = np.where(
    similar["翌日終値騰落率"] >= 0.5,
    "上昇",
    np.where(
        similar["翌日終値騰落率"] <= -0.5,
        "下落",
        "もみ合い"
    )
)


weighted_close_up = weighted_rate(
    similar,
    similar["翌日分類"] == "上昇"
)

weighted_close_range = weighted_rate(
    similar,
    similar["翌日分類"] == "もみ合い"
)

weighted_close_down = weighted_rate(
    similar,
    similar["翌日分類"] == "下落"
)


weighted_average_close = np.average(
    similar["翌日終値騰落率"],
    weights=similar["重み"]
)

median_close = float(
    similar["翌日終値騰落率"]
    .median()
)


# =========================================================
# 場中値幅
# =========================================================
weighted_up_1 = weighted_rate(
    similar,
    similar[
        "翌日寄付後最大上昇率"
    ] >= 1.0
)

weighted_up_2 = weighted_rate(
    similar,
    similar[
        "翌日寄付後最大上昇率"
    ] >= 2.0
)

weighted_down_1 = weighted_rate(
    similar,
    similar[
        "翌日寄付後最大下落率"
    ] <= -1.0
)

weighted_down_2 = weighted_rate(
    similar,
    similar[
        "翌日寄付後最大下落率"
    ] <= -2.0
)


average_intraday_up = np.average(
    similar[
        "翌日寄付後最大上昇率"
    ],
    weights=similar["重み"]
)

average_intraday_down = np.average(
    similar[
        "翌日寄付後最大下落率"
    ],
    weights=similar["重み"]
)

average_gap = np.average(
    similar["翌日GU_GD率"],
    weights=similar["重み"]
)


median_intraday_up = float(
    similar[
        "翌日寄付後最大上昇率"
    ].median()
)

median_intraday_down = float(
    similar[
        "翌日寄付後最大下落率"
    ].median()
)

median_gap = float(
    similar[
        "翌日GU_GD率"
    ].median()
)


# =========================================================
# 類似度
# =========================================================
highest_similarity = float(
    similar["類似度"].max()
)

average_similarity = float(
    similar["類似度"].mean()
)


# =========================================================
# 方向スコア
# =========================================================
direction_score = 0

close_difference = (
    weighted_close_up
    - weighted_close_down
)


if close_difference >= 20:
    direction_score += 2

elif close_difference >= 10:
    direction_score += 1

elif close_difference <= -20:
    direction_score -= 2

elif close_difference <= -10:
    direction_score -= 1


if weighted_average_close >= 1.0:
    direction_score += 2

elif weighted_average_close >= 0.30:
    direction_score += 1

elif weighted_average_close <= -1.0:
    direction_score -= 2

elif weighted_average_close <= -0.30:
    direction_score -= 1


if median_close >= 0.50:
    direction_score += 1

elif median_close <= -0.50:
    direction_score -= 1


up_excursion = average_intraday_up
down_excursion = abs(
    average_intraday_down
)

if (
    down_excursion > 0
    and
    up_excursion
    >= down_excursion * 1.30
):
    direction_score += 1

elif (
    up_excursion > 0
    and
    down_excursion
    >= up_excursion * 1.30
):
    direction_score -= 1


# =========================================================
# 方向判定
# =========================================================
if direction_score >= 5:

    if average_similarity >= 35:
        direction_outlook = "上方向優勢"
    else:
        direction_outlook = "やや上方向優勢"

elif direction_score >= 2:

    direction_outlook = "やや上方向優勢"

elif direction_score <= -5:

    if average_similarity >= 35:
        direction_outlook = "下方向優勢"
    else:
        direction_outlook = "やや下方向優勢"

elif direction_score <= -2:

    direction_outlook = "やや下方向優勢"

else:

    direction_outlook = "方向感は中立"


# =========================================================
# 値動き傾向
# =========================================================
intraday_difference = (
    weighted_up_1
    - weighted_down_1
)


if (
    weighted_up_1 >= 60
    and
    weighted_down_1 >= 60
):

    if intraday_difference >= 12:

        volatility_outlook = (
            "値幅拡大・上振れ寄り"
        )

    elif intraday_difference <= -12:

        volatility_outlook = (
            "値幅拡大・下振れ寄り"
        )

    else:

        volatility_outlook = (
            "値幅拡大・上下警戒"
        )


elif (
    weighted_up_2 >= 40
    or
    weighted_down_2 >= 40
    or
    weighted_up_1 >= 65
    or
    weighted_down_1 >= 65
):

    if intraday_difference >= 10:

        volatility_outlook = (
            "値幅大きめ・上振れ寄り"
        )

    elif intraday_difference <= -10:

        volatility_outlook = (
            "値幅大きめ・下振れ寄り"
        )

    else:

        volatility_outlook = (
            "大きな値動きに注意"
        )

else:

    volatility_outlook = "通常の値動き"


# =========================================================
# 予測信頼度
# =========================================================
data_quality_score = 0

if similar_count >= 25:
    data_quality_score += 1

if average_similarity >= 45:
    data_quality_score += 1

if highest_similarity >= 60:
    data_quality_score += 1


direction_strength = abs(
    weighted_close_up
    - weighted_close_down
)

direction_consistency = 0


if direction_strength >= 30:
    direction_consistency += 2

elif direction_strength >= 15:
    direction_consistency += 1


if (
    weighted_average_close > 0
    and median_close > 0
):
    direction_consistency += 1

elif (
    weighted_average_close < 0
    and median_close < 0
):
    direction_consistency += 1


both_sides_active = (
    weighted_up_1 >= 60
    and weighted_down_1 >= 60
)


confidence_score = (
    data_quality_score
    + direction_consistency
)

if both_sides_active:
    confidence_score -= 1


if confidence_score >= 5:
    confidence = "高"

elif confidence_score >= 3:
    confidence = "中"

else:
    confidence = "低"


# =========================================================
# 類似局面価格
# =========================================================
similar_up_price = (
    close_price
    * (
        1
        + average_intraday_up / 100
    )
)

similar_down_price = (
    close_price
    * (
        1
        + average_intraday_down / 100
    )
)


# =========================================================
# ATR価格
# =========================================================
atr_upper_1 = close_price + atr * 0.5
atr_upper_2 = close_price + atr

atr_lower_1 = close_price - atr * 0.5
atr_lower_2 = close_price - atr


# =========================================================
# 分岐候補
# =========================================================
price_candidates = []


def add_candidate(
    price,
    score,
    label,
    direction,
    category
):

    price_candidates.append({
        "price": float(price),
        "score": int(score),
        "label": label,
        "direction": direction,
        "category": category
    })


add_candidate(
    high_price, 3,
    "前日高値",
    "up", "near"
)

add_candidate(
    five_day_high, 3,
    "5日高値",
    "up", "short"
)

add_candidate(
    twenty_day_high, 2,
    "20日高値",
    "up", "major"
)

add_candidate(
    atr_upper_1, 2,
    "ATR上側第1",
    "up", "atr"
)

add_candidate(
    atr_upper_2, 2,
    "ATR上側第2",
    "up", "atr"
)

add_candidate(
    similar_up_price, 3,
    "類似局面上振れ",
    "up", "similar"
)


add_candidate(
    low_price, 3,
    "前日安値",
    "down", "near"
)

add_candidate(
    five_day_low, 3,
    "5日安値",
    "down", "short"
)

add_candidate(
    twenty_day_low, 2,
    "20日安値",
    "down", "major"
)

add_candidate(
    atr_lower_1, 2,
    "ATR下側第1",
    "down", "atr"
)

add_candidate(
    atr_lower_2, 2,
    "ATR下側第2",
    "down", "atr"
)

add_candidate(
    similar_down_price, 3,
    "類似局面下振れ",
    "down", "similar"
)


up_candidates = [
    c
    for c in price_candidates
    if (
        c["direction"] == "up"
        and
        c["price"] > close_price
    )
]

down_candidates = [
    c
    for c in price_candidates
    if (
        c["direction"] == "down"
        and
        c["price"] < close_price
    )
]


# =========================================================
# ゾーン作成
# =========================================================
def create_zones(candidates):

    if not candidates:
        return []

    candidates = sorted(
        candidates,
        key=lambda x: x["price"]
    )

    zone_width = (
        close_price
        * ZONE_WIDTH_PERCENT
        / 100
    )

    zones = []

    current_zone = {
        "prices": [
            candidates[0]["price"]
        ],
        "score": candidates[0]["score"],
        "labels": [
            candidates[0]["label"]
        ]
    }

    for candidate in candidates[1:]:

        center = np.mean(
            current_zone["prices"]
        )

        if abs(
            candidate["price"]
            - center
        ) <= zone_width:

            current_zone[
                "prices"
            ].append(
                candidate["price"]
            )

            current_zone[
                "score"
            ] += candidate["score"]

            current_zone[
                "labels"
            ].append(
                candidate["label"]
            )

        else:

            zones.append(
                current_zone
            )

            current_zone = {
                "prices": [
                    candidate["price"]
                ],
                "score": candidate["score"],
                "labels": [
                    candidate["label"]
                ]
            }

    zones.append(
        current_zone
    )


    for zone in zones:

        zone["center"] = float(
            np.mean(
                zone["prices"]
            )
        )

        zone["min"] = float(
            min(zone["prices"])
        )

        zone["max"] = float(
            max(zone["prices"])
        )

        zone["distance"] = abs(
            zone["center"]
            - close_price
        )

        if atr > 0:

            zone["atr_distance"] = (
                zone["distance"]
                / atr
            )

        else:
            zone["atr_distance"] = 999

    return zones


up_zones = create_zones(
    up_candidates
)

down_zones = create_zones(
    down_candidates
)


# =========================================================
# 第1分岐
# =========================================================
def select_first_zone(zones):

    if not zones:
        return None

    meaningful = [
        z
        for z in zones
        if z["score"] >= 3
    ]

    if not meaningful:
        meaningful = zones

    return min(
        meaningful,
        key=lambda z: z["distance"]
    )


first_up_zone = select_first_zone(
    up_zones
)

first_down_zone = select_first_zone(
    down_zones
)


# =========================================================
# 本命分岐
# =========================================================
def select_main_zone(
    zones,
    first_zone
):

    if not zones:
        return None

    candidates = [
        z
        for z in zones
        if (
            z is not first_zone
            and
            z["score"] >= MAIN_ZONE_MIN_SCORE
            and
            z["atr_distance"] <= MAIN_ZONE_MAX_ATR
        )
    ]

    if not candidates:
        return None

    return sorted(
        candidates,
        key=lambda z: (
            -z["score"],
            z["distance"]
        )
    )[0]


main_up_zone = select_main_zone(
    up_zones,
    first_up_zone
)

main_down_zone = select_main_zone(
    down_zones,
    first_down_zone
)


# =========================================================
# 途中警戒
# =========================================================
def select_middle_zone(
    zones,
    first_zone,
    main_zone
):

    if (
        first_zone is None
        or
        main_zone is None
    ):
        return None

    low_point = min(
        first_zone["center"],
        main_zone["center"]
    )

    high_point = max(
        first_zone["center"],
        main_zone["center"]
    )

    candidates = [
        z
        for z in zones
        if (
            z is not first_zone
            and
            z is not main_zone
            and
            z["score"] >= 3
            and
            low_point < z["center"] < high_point
        )
    ]

    if not candidates:
        return None

    return sorted(
        candidates,
        key=lambda z: (
            -z["score"],
            z["distance"]
        )
    )[0]


middle_up_zone = select_middle_zone(
    up_zones,
    first_up_zone,
    main_up_zone
)

middle_down_zone = select_middle_zone(
    down_zones,
    first_down_zone,
    main_down_zone
)


# =========================================================
# 表示用
# =========================================================
def zone_text(zone):

    if zone is None:
        return "明確な別候補なし"

    if (
        zone["max"]
        - zone["min"]
        < 1.0
    ):
        return (
            f"{zone['center']:.0f}"
            "円付近"
        )

    return (
        f"{zone['min']:.0f}"
        f"〜{zone['max']:.0f}"
        "円"
    )


def zone_reason(zone):

    if zone is None:
        return "-"

    return " / ".join(
        zone["labels"]
    )


def zone_importance(zone):

    if zone is None:
        return "-"

    if zone["score"] >= 9:
        return "非常に高い"

    elif zone["score"] >= 6:
        return "高い"

    elif zone["score"] >= 4:
        return "やや高い"

    return "通常"


def major_overlap(
    major_price,
    first_zone,
    main_zone
):

    threshold = (
        close_price
        * ZONE_WIDTH_PERCENT
        / 100
    )

    for zone, name in [
        (first_zone, "第1分岐"),
        (main_zone, "本命分岐")
    ]:

        if zone is None:
            continue

        if abs(
            major_price
            - zone["center"]
        ) <= threshold:

            return name

    return None


major_up_overlap = major_overlap(
    twenty_day_high,
    first_up_zone,
    main_up_zone
)

major_down_overlap = major_overlap(
    twenty_day_low,
    first_down_zone,
    main_down_zone
)


# =========================================================
# トレンド
# =========================================================
if (
    close_price > ma5
    and
    latest["5日線変化率"] > 0
):
    short_trend = "強い"

elif (
    close_price < ma5
    and
    latest["5日線変化率"] < 0
):
    short_trend = "弱い"

else:
    short_trend = "中立"


if (
    close_price > ma25
    and
    latest["25日線変化率"] > 0
):
    medium_trend = "強い"

elif (
    close_price < ma25
    and
    latest["25日線変化率"] < 0
):
    medium_trend = "弱い"

else:
    medium_trend = "中立"


# =========================================================
# 地合い取得
# =========================================================
print()
print("地合いデータを取得しています...")


nikkei = get_market_daily(
    "^N225",
    "日経平均"
)

nasdaq = get_market_daily(
    "^IXIC",
    "NASDAQ"
)

sox = get_market_daily(
    "^SOX",
    "SOX"
)

kospi = get_market_daily(
    "^KS11",
    "KOSPI"
)

usd_jpy = get_market_daily(
    "JPY=X",
    "USD/JPY"
)

nikkei_futures = get_futures_price()


# =========================================================
# 日経先物と現物の差
# =========================================================
futures_gap = None
futures_price = None

if (
    nikkei is not None
    and
    nikkei_futures is not None
):

    futures_price = (
        nikkei_futures["price"]
    )

    futures_gap = (
        (
            futures_price
            - nikkei["close"]
        )
        / nikkei["close"]
        * 100
    )


# =========================================================
# 日本株地合い
# 日経平均40％ + 日経先物60％
# =========================================================
japan_market_change = None
japan_market_score = 0

if (
    nikkei is not None
    and
    futures_gap is not None
):

    japan_market_change = (
        nikkei["change"] * 0.4
        + futures_gap * 0.6
    )

elif nikkei is not None:

    japan_market_change = (
        nikkei["change"]
    )

elif futures_gap is not None:

    japan_market_change = (
        futures_gap
    )


if japan_market_change is not None:

    japan_market_score = (
        market_score(
            japan_market_change
        )
    )


# =========================================================
# 米国ハイテク
# =========================================================
us_tech_score = 0

if nasdaq is not None:

    us_tech_score = (
        market_score(
            nasdaq["change"]
        )
    )


# =========================================================
# 半導体地合い
# ※総合点には入れない
# =========================================================
semiconductor_score = 0

if sox is not None:

    semiconductor_score = (
        market_score(
            sox["change"]
        )
    )


# =========================================================
# アジア地合い
# =========================================================
asia_score = 0

if kospi is not None:

    asia_score = (
        market_score(
            kospi["change"]
        )
    )


# =========================================================
# 総合地合い
# 日本株 + NASDAQ + KOSPI
# SOXと為替は別表示
# =========================================================
ground_score = (
    japan_market_score
    + us_tech_score
    + asia_score
)


if ground_score >= 4:
    ground_outlook = "強い追い風"

elif ground_score >= 2:
    ground_outlook = "追い風"

elif ground_score <= -4:
    ground_outlook = "強い逆風"

elif ground_score <= -2:
    ground_outlook = "逆風"

else:
    ground_outlook = "中立"


japan_market_text = (
    environment_text(
        japan_market_score
    )
)

us_tech_text = (
    environment_text(
        us_tech_score
    )
)

semiconductor_text = (
    environment_text(
        semiconductor_score
    )
)

asia_text = (
    environment_text(
        asia_score
    )
)


# =========================================================
# 為替判定
# =========================================================
fx_text = "取得不可"

if usd_jpy is not None:

    fx_change = (
        usd_jpy["change"]
    )

    if fx_change >= 0.50:
        fx_text = "円安方向"

    elif fx_change <= -0.50:
        fx_text = "円高方向"

    else:
        fx_text = "大きな変化なし"


# =========================================================
# 自動解説
# =========================================================
explanation = []


if direction_outlook == "上方向優勢":

    explanation.append(
        f"類似局面では終値上昇が"
        f"{weighted_close_up:.1f}%、"
        f"終値下落が"
        f"{weighted_close_down:.1f}%で、"
        "上方向への偏りが見られます。"
    )

elif direction_outlook == "やや上方向優勢":

    explanation.append(
        f"類似局面では終値上昇が"
        f"{weighted_close_up:.1f}%、"
        f"終値下落が"
        f"{weighted_close_down:.1f}%で、"
        "上方向にやや分があります。"
    )

elif direction_outlook == "下方向優勢":

    explanation.append(
        f"類似局面では終値下落が"
        f"{weighted_close_down:.1f}%、"
        f"終値上昇が"
        f"{weighted_close_up:.1f}%で、"
        "下方向への偏りが見られます。"
    )

elif direction_outlook == "やや下方向優勢":

    explanation.append(
        f"類似局面では終値下落が"
        f"{weighted_close_down:.1f}%、"
        f"終値上昇が"
        f"{weighted_close_up:.1f}%で、"
        "下方向にやや分があります。"
    )

else:

    explanation.append(
        "類似局面では上昇・下落の差が小さく、"
        "方向の偏りはあまり見られません。"
    )


if (
    volatility_outlook
    == "値幅拡大・上下警戒"
):

    explanation.append(
        f"寄付き後に1%以上上昇した割合は"
        f"{weighted_up_1:.1f}%、"
        f"1%以上下落した割合は"
        f"{weighted_down_1:.1f}%で、"
        "場中は上下両方向への振れに注意します。"
    )


elif (
    "上振れ寄り"
    in volatility_outlook
):

    explanation.append(
        f"寄付き後の上振れ率が"
        f"{weighted_up_1:.1f}%、"
        f"下振れ率が"
        f"{weighted_down_1:.1f}%で、"
        "値幅は上方向にやや偏っています。"
    )


elif (
    "下振れ寄り"
    in volatility_outlook
):

    explanation.append(
        f"寄付き後の上振れ率が"
        f"{weighted_up_1:.1f}%、"
        f"下振れ率が"
        f"{weighted_down_1:.1f}%で、"
        "値幅は下方向にやや偏っています。"
    )


if (
    average_intraday_up
    - median_intraday_up
    >= 0.70
):

    explanation.append(
        "上振れ幅は平均値が中央値を大きく上回っています。"
        "一部の大幅上昇が平均を押し上げていますが、"
        f"中央値でも+{median_intraday_up:.2f}%あります。"
    )


if (
    abs(average_intraday_down)
    - abs(median_intraday_down)
    >= 0.70
):

    explanation.append(
        "下振れ幅は平均値が中央値を大きく上回っており、"
        "一部の大幅下落の影響が含まれています。"
    )


if (
    average_gap * median_gap < 0
    and
    abs(
        average_gap - median_gap
    ) >= 0.40
):

    explanation.append(
        "寄付きのGU/GDは平均値と中央値の方向が異なり、"
        "寄付き傾向にはばらつきがあります。"
    )


if rsi <= 20:

    explanation.append(
        f"RSIは{rsi:.1f}とかなり低い水準です。"
        "短期の弱さが続く可能性と、"
        "売られ過ぎからの反発の両方に注意します。"
    )

elif rsi <= 30:

    explanation.append(
        f"RSIは{rsi:.1f}と低い水準で、"
        "下落継続だけでなく反発にも注意します。"
    )

elif rsi >= 80:

    explanation.append(
        f"RSIは{rsi:.1f}とかなり高い水準です。"
        "上昇基調が続く可能性がある一方、"
        "高値追いには注意が必要です。"
    )

elif rsi >= 70:

    explanation.append(
        f"RSIは{rsi:.1f}と高い水準です。"
        "上昇基調を維持できるか確認しながら、"
        "高値追いには慎重に見ます。"
    )


if (
    short_trend == "強い"
    and
    medium_trend == "強い"
):

    explanation.append(
        "短期・中期とも強く、"
        "トレンドは上方向で一致しています。"
    )

elif (
    short_trend == "弱い"
    and
    medium_trend == "弱い"
):

    explanation.append(
        "短期・中期とも弱く、"
        "戻り局面でも売り圧には注意します。"
    )

elif short_trend != medium_trend:

    explanation.append(
        "短期と中期の方向が一致していないため、"
        "寄付き前の決め打ちは避けたい状態です。"
    )


if average_similarity < 40:

    explanation.append(
        f"平均類似度は{average_similarity:.1f}%で、"
        "今回と非常によく似た過去局面は多くありません。"
        "統計結果は参考度を一段下げて見ます。"
    )


if main_up_zone is not None:

    explanation.append(
        f"上側では{zone_text(main_up_zone)}を"
        "突破して維持できれば、"
        "上方向シナリオを強めます。"
    )

elif first_up_zone is not None:

    explanation.append(
        f"上側では{zone_text(first_up_zone)}が"
        "最初の重要確認帯です。"
    )


if main_down_zone is not None:

    explanation.append(
        f"下側では{zone_text(main_down_zone)}を"
        "割れて戻せない場合、"
        "下方向シナリオを強めます。"
    )

elif first_down_zone is not None:

    explanation.append(
        f"下側では{zone_text(first_down_zone)}が"
        "最初の重要確認帯です。"
    )


# =========================================================
# 地合い解説
# =========================================================
ground_explanation = []


if japan_market_score >= 2:

    ground_explanation.append(
        "日経平均と先物を合わせた日本株地合いは強めで、"
        "寄付き環境には追い風です。"
    )

elif japan_market_score == 1:

    ground_explanation.append(
        "日本株地合いはやや良好で、"
        "寄付き環境には追い風です。"
    )

elif japan_market_score <= -2:

    ground_explanation.append(
        "日経平均と先物を合わせた日本株地合いは弱く、"
        "寄付き環境には逆風です。"
    )

elif japan_market_score == -1:

    ground_explanation.append(
        "日本株地合いはやや弱く、"
        "寄付きには注意が必要です。"
    )


if us_tech_score >= 1:

    ground_explanation.append(
        "NASDAQは上向きで、"
        "米国ハイテク株の地合いは良好です。"
    )

elif us_tech_score <= -1:

    ground_explanation.append(
        "NASDAQは弱く、"
        "米国ハイテク株の地合いには注意します。"
    )


if semiconductor_score >= 2:

    ground_explanation.append(
        "SOXは強く、"
        "半導体株には強い追い風です。"
    )

elif semiconductor_score == 1:

    ground_explanation.append(
        "SOXは上昇しており、"
        "半導体株には追い風です。"
    )

elif semiconductor_score <= -2:

    ground_explanation.append(
        "SOXは大きく下落しており、"
        "半導体株には強い逆風です。"
    )

elif semiconductor_score == -1:

    ground_explanation.append(
        "SOXは弱く、"
        "半導体株には逆風です。"
    )


if asia_score >= 1:

    ground_explanation.append(
        "KOSPIも上向きで、"
        "アジア株の地合いは良好です。"
    )

elif asia_score <= -1:

    ground_explanation.append(
        "KOSPIは弱く、"
        "アジア株の地合いには注意します。"
    )


if usd_jpy is not None:

    if fx_text == "円安方向":

        ground_explanation.append(
            "ドル円は円安方向です。"
            "為替の影響は銘柄ごとに異なるため、"
            "総合地合いには加えていません。"
        )

    elif fx_text == "円高方向":

        ground_explanation.append(
            "ドル円は円高方向です。"
            "為替の影響は銘柄ごとに異なるため、"
            "総合地合いには加えていません。"
        )


# =========================================================
# 保存
# =========================================================
stock_file = f"{code}_株価データ.xlsx"
similar_file = f"{code}_類似局面.xlsx"
zone_file = f"{code}_分岐ライン候補.xlsx"
ai_file = f"{code}_AI学習データ.csv"
ground_file = f"{code}_地合いデータ.csv"


data.to_excel(
    stock_file
)


similar_save = similar.copy()

similar_save.insert(
    0,
    "日付",
    similar_save.index.strftime(
        "%Y-%m-%d"
    )
)

similar_save.to_excel(
    similar_file,
    index=False
)


zone_rows = []

for direction, zones in [
    ("上", up_zones),
    ("下", down_zones)
]:

    for zone in zones:

        zone_rows.append({
            "方向": direction,
            "ゾーン中心": zone["center"],
            "ゾーン下限": zone["min"],
            "ゾーン上限": zone["max"],
            "重要度点数": zone["score"],
            "重要度": zone_importance(
                zone
            ),
            "現在値からの距離": zone["distance"],
            "ATR距離": zone["atr_distance"],
            "根拠": zone_reason(
                zone
            )
        })


zone_df = pd.DataFrame(
    zone_rows
)

zone_df.to_excel(
    zone_file,
    index=False
)


ai_columns = [
    "Open",
    "High",
    "Low",
    "Close",
    "Volume",
    "RSI",
    "5日乖離率",
    "25日乖離率",
    "出来高倍率",
    "ATR率",
    "5日線変化率",
    "25日線変化率",
    "当日騰落率",
    "翌日終値騰落率",
    "翌日寄付後最大上昇率",
    "翌日寄付後最大下落率",
    "翌日GU_GD率"
]

data[
    ai_columns
].to_csv(
    ai_file,
    encoding="utf-8-sig"
)


ground_rows = []


def append_market_row(item):

    if item is None:
        return

    ground_rows.append({
        "指標": item["name"],
        "シンボル": item["symbol"],
        "データ日": str(
            item["date"]
        ),
        "終値": item["close"],
        "前回値": item["previous"],
        "騰落率": item["change"]
    })


append_market_row(
    nikkei
)

append_market_row(
    nasdaq
)

append_market_row(
    sox
)

append_market_row(
    kospi
)

append_market_row(
    usd_jpy
)


if futures_price is not None:

    ground_rows.append({
        "指標": "日経先物(CME参考)",
        "シンボル": "NKD=F",
        "データ日": str(
            nikkei_futures["date"]
        ),
        "終値": futures_price,
        "前回値":
            nikkei["close"]
            if nikkei is not None
            else np.nan,
        "騰落率": futures_gap
    })


ground_df = pd.DataFrame(
    ground_rows
)

ground_df.to_csv(
    ground_file,
    index=False,
    encoding="utf-8-sig"
)


# =========================================================
# 表示
# =========================================================
print()
print("======================================")
print(f" {code} {company_name} 今日のデイトレ展望")
print("======================================")

print(
    "最新データ日  :",
    latest_date.strftime(
        "%Y-%m-%d"
    )
)

print(
    f"前日終値      : "
    f"{close_price:.2f} 円"
)


# =========================================================
# 今日の結論
# =========================================================
print()
print("======================================")
print("           今日の結論")
print("======================================")

print(
    "値動き傾向     :",
    volatility_outlook
)

print(
    "方向性         :",
    direction_outlook
)

print(
    "予測信頼度     :",
    confidence
)


# =========================================================
# 重要価格
# =========================================================
print()
print("======================================")
print("         今日の重要価格")
print("======================================")


print()
print("【上方向】")

if first_up_zone is not None:

    print(
        "① 最初の分岐   :",
        zone_text(
            first_up_zone
        )
    )

    print(
        "   重要度      :",
        zone_importance(
            first_up_zone
        )
    )

    print(
        "   根拠        :",
        zone_reason(
            first_up_zone
        )
    )


if middle_up_zone is not None:

    print()
    print(
        "   途中警戒    :",
        zone_text(
            middle_up_zone
        )
    )

    print(
        "   根拠        :",
        zone_reason(
            middle_up_zone
        )
    )


print()

if main_up_zone is not None:

    print(
        "② 本命分岐    :",
        zone_text(
            main_up_zone
        )
    )

    print(
        "   重要度      :",
        zone_importance(
            main_up_zone
        )
    )

    print(
        "   根拠        :",
        zone_reason(
            main_up_zone
        )
    )

    print(
        "   → 突破して維持なら"
    )

    print(
        "     上方向シナリオを強める"
    )

else:

    print(
        "② 本命分岐    : "
        "明確な別候補なし"
    )


print()

if major_up_overlap is None:

    print(
        f"③ 大きな節目  : "
        f"{twenty_day_high:.0f}円付近"
    )

    print(
        "   根拠        : 20日高値"
    )

else:

    print(
        "③ 大きな節目  : "
        f"{major_up_overlap}に統合"
    )


print()
print("【下方向】")

if first_down_zone is not None:

    print(
        "① 最初の分岐   :",
        zone_text(
            first_down_zone
        )
    )

    print(
        "   重要度      :",
        zone_importance(
            first_down_zone
        )
    )

    print(
        "   根拠        :",
        zone_reason(
            first_down_zone
        )
    )


if middle_down_zone is not None:

    print()
    print(
        "   途中警戒    :",
        zone_text(
            middle_down_zone
        )
    )

    print(
        "   根拠        :",
        zone_reason(
            middle_down_zone
        )
    )


print()

if main_down_zone is not None:

    print(
        "② 本命分岐    :",
        zone_text(
            main_down_zone
        )
    )

    print(
        "   重要度      :",
        zone_importance(
            main_down_zone
        )
    )

    print(
        "   根拠        :",
        zone_reason(
            main_down_zone
        )
    )

    print(
        "   → 割れて戻せなければ"
    )

    print(
        "     下方向シナリオを強める"
    )

else:

    print(
        "② 本命分岐    : "
        "明確な別候補なし"
    )


print()

if major_down_overlap is None:

    print(
        f"③ 大きな節目  : "
        f"{twenty_day_low:.0f}円付近"
    )

    print(
        "   根拠        : 20日安値"
    )

else:

    print(
        "③ 大きな節目  : "
        f"{major_down_overlap}に統合"
    )


# =========================================================
# 今日の解説
# =========================================================
print()
print("======================================")
print("           今日の解説")
print("======================================")

for text in explanation:

    print()
    print(text)


# =========================================================
# 地合い
# =========================================================
print()
print("======================================")
print("           今日の地合い")
print("======================================")

print(
    "日本株地合い     :",
    japan_market_text
)

print(
    "米国ハイテク     :",
    us_tech_text
)

print(
    "半導体地合い     :",
    semiconductor_text
)

print(
    "アジア地合い     :",
    asia_text
)

print(
    "為替環境         :",
    fx_text
)

print()

print(
    "総合地合い       :",
    ground_outlook
)


print()
print("【参考データ】")


if nikkei is not None:

    print(
        f"日経平均         : "
        f"{nikkei['change']:+.2f}%"
    )


if futures_gap is not None:

    print(
        f"日経先物(CME)    : "
        f"{futures_gap:+.2f}%"
    )


if nasdaq is not None:

    print(
        f"NASDAQ           : "
        f"{nasdaq['change']:+.2f}%"
    )


if sox is not None:

    print(
        f"SOX              : "
        f"{sox['change']:+.2f}%"
    )


if kospi is not None:

    print(
        f"KOSPI            : "
        f"{kospi['change']:+.2f}%"
    )


if usd_jpy is not None:

    print(
        f"USD/JPY          : "
        f"{usd_jpy['close']:.2f}  "
        f"{usd_jpy['change']:+.2f}%"
    )


print()
print("【地合い解説】")

if ground_explanation:

    for text in ground_explanation:

        print()
        print(text)

else:

    print()
    print(
        "地合いに大きな偏りは"
        "確認できません。"
    )


print()
print(
    "※総合地合いは"
    "日本株・NASDAQ・KOSPIで判定しています。"
)

print(
    "※SOXは半導体地合いとして別表示し、"
    "総合点には加えていません。"
)

print(
    "※USD/JPYも銘柄ごとの影響が異なるため、"
    "総合点には加えていません。"
)

print(
    "※地合い判定は個別銘柄の予測スコアには"
    "混ぜていません。"
)


# =========================================================
# 類似局面統計
# =========================================================
print()
print("======================================")
print("         類似局面の統計")
print("======================================")

print(
    f"比較対象       : "
    f"{len(history)}日"
)

print(
    f"採用局面       : "
    f"{similar_count}件"
)

print(
    f"最高類似度     : "
    f"{highest_similarity:.1f}%"
)

print(
    f"平均類似度     : "
    f"{average_similarity:.1f}%"
)

print()

print(
    f"終値上昇       : "
    f"{weighted_close_up:.1f}%"
)

print(
    f"終値もみ合い   : "
    f"{weighted_close_range:.1f}%"
)

print(
    f"終値下落       : "
    f"{weighted_close_down:.1f}%"
)

print(
    f"平均終値騰落率 : "
    f"{weighted_average_close:+.2f}%"
)

print(
    f"中央値騰落率   : "
    f"{median_close:+.2f}%"
)

print()

print(
    f"寄付後 +1%以上: "
    f"{weighted_up_1:.1f}%"
)

print(
    f"寄付後 +2%以上: "
    f"{weighted_up_2:.1f}%"
)

print(
    f"寄付後 -1%以上: "
    f"{weighted_down_1:.1f}%"
)

print(
    f"寄付後 -2%以上: "
    f"{weighted_down_2:.1f}%"
)

print()

print(
    f"平均上振れ     : "
    f"{average_intraday_up:+.2f}%"
)

print(
    f"中央値上振れ   : "
    f"{median_intraday_up:+.2f}%"
)

print(
    f"平均下振れ     : "
    f"{average_intraday_down:+.2f}%"
)

print(
    f"中央値下振れ   : "
    f"{median_intraday_down:+.2f}%"
)

print()

print(
    f"平均GU/GD      : "
    f"{average_gap:+.2f}%"
)

print(
    f"中央値GU/GD    : "
    f"{median_gap:+.2f}%"
)


# =========================================================
# 現在状態
# =========================================================
print()
print("======================================")
print("           現在の状態")
print("======================================")

print(
    "短期           :",
    short_trend
)

print(
    "中期           :",
    medium_trend
)

print(
    f"RSI            : "
    f"{rsi:.1f}"
)

print(
    f"出来高倍率     : "
    f"{volume_ratio:.2f}倍"
)

print(
    f"ATR14          : "
    f"{atr:.2f}円"
)


# =========================================================
# 最も似た過去5局面
# =========================================================
print()
print("======================================")
print("        最も似た過去5局面")
print("======================================")

top_five = similar.head(5)

for rank, (
    date,
    row
) in enumerate(
    top_five.iterrows(),
    start=1
):

    print(
        f"{rank}位 "
        f"{date.strftime('%Y-%m-%d')}  "
        f"類似 {row['類似度']:.1f}%  "
        f"終値"
        f"{row['翌日終値騰落率']:+.2f}%  "
        f"↑"
        f"{row['翌日寄付後最大上昇率']:+.2f}%  "
        f"↓"
        f"{row['翌日寄付後最大下落率']:+.2f}%"
    )


# =========================================================
# 注意
# =========================================================
print()
print("======================================")
print("              注意")
print("======================================")

print(
    "個別銘柄分析と地合い分析は"
    "別々に判定しています。"
)

print(
    "地合いが逆風でも個別分析の"
    "方向判定は自動変更しません。"
)

print()

print(
    "平均値は一部の大幅変動に"
    "影響されるため、"
    "中央値も確認してください。"
)

print()

print(
    "分岐価格は1円単位ではなく、"
    "価格帯として判断してください。"
)

print(
    "複数の価格候補が重なった場合は、"
    "1つの重要ゾーンとして統合します。"
)

print()

print(
    "※日足では当日の高値と安値の"
    "発生順序は分かりません。"
)

print(
    "※5分足導入後に、"
    "場中の順序・突破後の維持・"
    "エントリータイミングを分析します。"
)


# =========================================================
# 保存ファイル
# =========================================================
print()
print("保存ファイル")

print(
    "株価データ     :",
    stock_file
)

print(
    "類似局面       :",
    similar_file
)

print(
    "分岐ライン候補 :",
    zone_file
)

print(
    "AI学習データ   :",
    ai_file
)

print(
    "地合いデータ   :",
    ground_file
)

print("======================================")

input(
    "\nEnterキーを押して終了してください。"
)
