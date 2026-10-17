import streamlit as st
import yfinance as yf
import pandas as pd
import numpy as np
import os
import requests
import hmac
import html
import re
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
from email.utils import parsedate_to_datetime
from zoneinfo import ZoneInfo
from urllib.parse import quote


# =========================================================
# ページ設定
# =========================================================
st.set_page_config(
    page_title="カブグルマン★★★",
    page_icon="🔍",
    layout="centered"
)


def get_secret_section(section_name):
    """ローカルにsecrets.tomlがない場合でも安全に動かす。"""
    try:
        return st.secrets.get(section_name, {})
    except Exception:
        return {}


def require_cloud_login():
    """クラウド公開時だけ利用者制限を有効にする。"""
    app_settings = get_secret_section("app")
    require_login = bool(app_settings.get("require_login", False))

    # 自宅PCでの従来どおりの実行は、認証設定なしで利用できる。
    if not require_login:
        return

    login_method = str(app_settings.get("login_method", "google")).strip().lower()

    # 少人数での共有用。Google OAuthの設定なしで、合言葉を知る人だけを通す。
    if login_method == "passcode":
        expected_passcode = str(app_settings.get("access_passcode", ""))
        if not expected_passcode:
            st.error("合言葉が設定されていないため、アプリを開始できません。")
            st.stop()

        if st.session_state.get("cloud_access_granted", False):
            if st.sidebar.button("ログアウト"):
                st.session_state.cloud_access_granted = False
                st.rerun()
            return

        st.markdown('<p style="font-size:1.4rem; font-weight:700; white-space:nowrap;">🔒 カブグルマン★★★</p>', unsafe_allow_html=True)
        st.write("利用を許可された方専用です。")
        entered_passcode = st.text_input("合言葉", type="password") or ""
        if st.button("enter"):
            if hmac.compare_digest(
                str(entered_passcode).encode("utf-8"),
                expected_passcode.encode("utf-8"),
            ):
                st.session_state.cloud_access_granted = True
                st.rerun()
            else:
                st.error("合言葉が違います。")
        st.stop()

    auth_settings = get_secret_section("auth")
    required_auth_keys = (
        "redirect_uri",
        "cookie_secret",
        "client_id",
        "client_secret",
        "server_metadata_url",
    )

    if not all(auth_settings.get(key) for key in required_auth_keys):
        st.error("ログイン設定が不足しているため、アプリを開始できません。")
        st.stop()

    if not st.user.is_logged_in:
        st.markdown('<p style="font-size:1.4rem; font-weight:700; white-space:nowrap;">🔒 カブグルマン★★★</p>', unsafe_allow_html=True)
        st.write("このアプリは利用を許可された方専用です。")
        if st.button("Googleでログイン"):
            st.login()
        st.stop()

    # Streamlitの版によっては st.user が dict 風でも .get() を持たない。
    # 添字アクセスに統一し、メール情報がない場合だけ許可しない。
    try:
        user_email = str(st.user["email"]).strip().lower()
    except (KeyError, TypeError, AttributeError):
        user_email = ""
    allowed_emails = {
        str(email).strip().lower()
        for email in app_settings.get("allowed_emails", [])
    }

    if not user_email or user_email not in allowed_emails:
        st.markdown('<p style="font-size:1.4rem; font-weight:700; white-space:nowrap;">🔒 カブグルマン★★★</p>', unsafe_allow_html=True)
        st.error("このGoogleアカウントには利用許可がありません。")
        if st.button("ログアウト"):
            st.logout()
        st.stop()

    try:
        user_name = str(st.user["name"]).strip()
    except (KeyError, TypeError, AttributeError):
        user_name = user_email
    st.sidebar.caption(f"ログイン中：{user_name or user_email}")
    if st.sidebar.button("ログアウト"):
        st.logout()


require_cloud_login()


# =========================================================
# セッション状態
# =========================================================
if "helper_result" not in st.session_state:
    st.session_state.helper_result = None

if "helper_code" not in st.session_state:
    st.session_state.helper_code = ""

if "helper_company" not in st.session_state:
    st.session_state.helper_company = ""

if "helper_date" not in st.session_state:
    st.session_state.helper_date = ""

if "helper_styles" not in st.session_state:
    st.session_state.helper_styles = []


def reset_analysis():
    st.session_state.helper_result = None
    st.session_state.helper_code = ""
    st.session_state.helper_company = ""
    st.session_state.helper_date = ""
    st.session_state.helper_styles = []
    st.session_state.stock_code_input = ""


def submit_stock_code():
    """銘柄コード欄でEnterを押した時に、分析ボタンと同じ処理を起動する。"""
    st.session_state.stock_code_enter_submit = True


# =========================================================
# 株価展望（旧 stock_ai.py をクラウドから呼び出す）
# =========================================================
def find_outlook_value(text, label):
    pattern = rf"{re.escape(label)}\s*:\s*(.+)"
    match = re.search(pattern, text)
    return match.group(1).strip() if match else "-"


def compact_outlook_price(value):
    return value.replace("円付近", "").replace("円", "").strip()


def parse_stock_outlook(output):
    direction = find_outlook_value(output, "方向性")
    volatility = find_outlook_value(output, "値動き傾向")
    confidence = find_outlook_value(output, "予測信頼度")
    market = {
        "japan": find_outlook_value(output, "日本株地合い"),
        "us_tech": find_outlook_value(output, "米国ハイテク"),
        "semiconductor": find_outlook_value(output, "半導体地合い"),
        "asia": find_outlook_value(output, "アジア地合い"),
        "fx": find_outlook_value(output, "為替環境"),
        "total": find_outlook_value(output, "総合地合い"),
    }

    zones = {
        "up": {"first": "-", "middle": "-", "main": "-", "major": "-"},
        "down": {"first": "-", "middle": "-", "main": "-", "major": "-"},
    }
    current_direction = None
    for line in output.splitlines():
        stripped = line.strip()
        if stripped == "【上方向】":
            current_direction = "up"
            continue
        if stripped == "【下方向】":
            current_direction = "down"
            continue
        if current_direction not in zones:
            continue
        value = line.split(":", 1)[-1].strip() if ":" in line else "-"
        if "① 最初の分岐" in line:
            zones[current_direction]["first"] = value
        elif "途中警戒" in line:
            zones[current_direction]["middle"] = value
        elif "② 本命分岐" in line:
            zones[current_direction]["main"] = value
        elif "③ 大きな節目" in line:
            zones[current_direction]["major"] = value

    detail_lines = output.splitlines()
    detail_start = next(
        (
            index
            for index, line in enumerate(detail_lines)
            if "今日のデイトレ展望" in line
        ),
        0,
    )
    return {
        "direction": direction,
        "volatility": volatility,
        "confidence": confidence,
        "market": market,
        "zones": zones,
        "detail": "\n".join(detail_lines[detail_start:]),
    }


def run_stock_outlook(code, stock_data=None, current_snapshot=None):
    script_path = Path(__file__).parent / "stock_ai.py"
    if not script_path.exists():
        return {
            "error": "株価展望エンジンが見つかりません。stock_ai.py を同じ場所へ追加してください。"
        }

    environment = os.environ.copy()
    environment["STOCK_AI_SKIP_EXPORT"] = "1"
    try:
        with tempfile.TemporaryDirectory() as temp_dir:
            # 個別銘柄の株価は helper 側ですでに取得済み。
            # 同じYahooデータを stock_ai.py が取り直さないよう、
            # 一時CSVで受け渡して二重取得を防ぐ。
            if stock_data is not None and not stock_data.empty:
                input_csv = Path(temp_dir) / "stock_outlook_input.csv"
                stock_data.to_csv(input_csv, encoding="utf-8-sig")
                environment["STOCK_AI_INPUT_CSV"] = str(input_csv)
            if current_snapshot:
                import json
                serializable = {}
                for key, value in current_snapshot.items():
                    if isinstance(value, (pd.Timestamp,)):
                        serializable[key] = value.isoformat()
                    else:
                        try:
                            serializable[key] = value.item() if hasattr(value, "item") else value
                        except Exception:
                            serializable[key] = str(value)
                environment["STOCK_AI_CURRENT_SNAPSHOT"] = json.dumps(serializable, ensure_ascii=False)

            completed = subprocess.run(
                [sys.executable, str(script_path)],
                # 最初は銘柄コード、最後は旧プログラムの終了確認用Enter。
                input=f"{code}\n\n",
                text=True,
                capture_output=True,
                encoding="utf-8",
                errors="replace",
                cwd=temp_dir,
                env=environment,
                timeout=180,
            )
    except subprocess.TimeoutExpired:
        return {"error": "株価展望の計算に時間がかかりすぎました。もう一度お試しください。"}
    except Exception as exc:
        return {"error": f"株価展望を開始できませんでした：{exc}"}

    output = completed.stdout or ""
    error_output = completed.stderr or ""
    if not output.strip():
        return {
            "error": "株価展望エンジンが結果を返せませんでした。",
            "detail": error_output[-2000:] or "エラー詳細を取得できませんでした。",
        }
    if "株価データを取得できませんでした" in output:
        return {"error": "株価展望用の株価データを取得できませんでした。"}

    result = parse_stock_outlook(output)
    if completed.returncode != 0:
        result["warning"] = "一部の参考データを取得できず、表示を簡略化している可能性があります。"
    return result


# =========================================================
# 共通処理
# =========================================================
def normalize_code(code):
    code = str(code).strip().upper()

    if code.endswith(".T"):
        code = code[:-2]

    return code


def get_company_name(ticker):
    try:
        info = yf.Ticker(ticker).info
        return (
            info.get("shortName")
            or info.get("longName")
            or ticker
        )
    except Exception:
        return ticker


def download_stock_data(code):
    ticker = f"{code}.T"

    data = yf.download(
        ticker,
        period="5y",
        interval="1d",
        auto_adjust=False,
        progress=False
    )

    if data is None or data.empty:
        return None

    if isinstance(data.columns, pd.MultiIndex):
        data.columns = data.columns.get_level_values(0)

    required = ["Open", "High", "Low", "Close", "Volume"]

    for col in required:
        if col not in data.columns:
            return None

    data = data[required].copy()
    data = data.dropna()

    if len(data) < 80:
        return None

    return data


def _download_yf(symbol, period, interval, prepost=False, timeout=20):
    """Yahoo Finance取得。download失敗時はTicker.historyへ1回だけフォールバック。"""
    try:
        data = yf.download(
            symbol,
            period=period,
            interval=interval,
            auto_adjust=False,
            progress=False,
            repair=True,
            prepost=prepost,
            threads=False,
            timeout=timeout,
        )
    except Exception:
        data = None

    if data is None or data.empty:
        try:
            data = yf.Ticker(symbol).history(
                period=period,
                interval=interval,
                auto_adjust=False,
                prepost=prepost,
                timeout=timeout,
            )
        except Exception:
            data = None

    if data is None or data.empty:
        return None

    if isinstance(data.columns, pd.MultiIndex):
        data.columns = data.columns.get_level_values(0)

    return data


def _as_market_symbols(symbol):
    """単一シンボル/候補シンボルのどちらでも扱えるようにする。"""
    if isinstance(symbol, (list, tuple)):
        return list(symbol)
    return [symbol]


def _timestamp_to_jst(value):
    """取得時刻を表示用のJSTへ変換する。"""
    if value is None:
        return None
    try:
        ts = pd.Timestamp(value)
        if ts.tzinfo is None:
            ts = ts.tz_localize("UTC")
        return ts.tz_convert(ZoneInfo("Asia/Tokyo"))
    except Exception:
        return None


def _daily_market_asof(symbol, value):
    """日足しか取れない場合でも、各市場の終値時刻をJSTで持たせる。"""
    try:
        d = pd.Timestamp(value).date()
        if symbol in ("^IXIC", "^SOX"):
            local = pd.Timestamp(f"{d} 16:00", tz="America/New_York")
            return local.tz_convert(ZoneInfo("Asia/Tokyo"))
        if symbol == "^KS11":
            local = pd.Timestamp(f"{d} 15:30", tz="Asia/Seoul")
            return local.tz_convert(ZoneInfo("Asia/Tokyo"))
        if symbol in ("^N225", "998405.T", "^TOPX"):
            return pd.Timestamp(f"{d} 15:30", tz=ZoneInfo("Asia/Tokyo"))
        if symbol == "JPY=X":
            local = pd.Timestamp(f"{d} 17:00", tz="America/New_York")
            return local.tz_convert(ZoneInfo("Asia/Tokyo"))
        return _timestamp_to_jst(value)
    except Exception:
        return _timestamp_to_jst(value)


def _fetch_topix_web_fallback():
    """yfinanceでTOPIXが取れない時のYahoo!ファイナンス日本版フォールバック。"""
    # 0) YahooのチャートJSONを直接確認する。
    try:
        response = requests.get(
            "https://query1.finance.yahoo.com/v8/finance/chart/998405.T",
            params={"range": "10d", "interval": "1d", "includePrePost": "true"},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=12,
        )
        response.raise_for_status()
        payload = response.json()
        result = (((payload or {}).get("chart") or {}).get("result") or [None])[0]
        if result:
            timestamps = result.get("timestamp") or []
            quote = (((result.get("indicators") or {}).get("quote") or [{}])[0])
            closes = quote.get("close") or []
            valid = [(ts, c) for ts, c in zip(timestamps, closes) if c is not None]
            if len(valid) >= 2:
                (prev_ts, prev), (last_ts, latest) = valid[-2], valid[-1]
                previous = float(prev)
                close = float(latest)
                asof = pd.Timestamp(last_ts, unit="s", tz="UTC").tz_convert(ZoneInfo("Asia/Tokyo"))
                return {
                    "close": close,
                    "previous": previous,
                    "change": (close / previous - 1) * 100 if previous else None,
                    "date": asof.strftime("%Y-%m-%d"),
                    "time": None,
                    "asof": asof,
                    "interval": "1d-json",
                    "symbol": "998405.T",
                    "live": False,
                }
    except Exception:
        pass

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) "
            "AppleWebKit/605.1.15 Version/18.0 Mobile/15E148 Safari/604.1"
        ),
        "Accept-Language": "ja-JP,ja;q=0.9,en;q=0.5",
    }

    def make_item(change, close=None, previous=None, date_text=None):
        now_jst = pd.Timestamp.now(tz=ZoneInfo("Asia/Tokyo"))
        return {
            "close": close,
            "previous": previous,
            "change": change,
            "date": date_text or now_jst.strftime("%Y-%m-%d"),
            "time": None,
            "asof": None,
            "interval": "web",
            "symbol": "998405.T",
            "live": False,
        }

    # 1) 詳細ページの「前日比 +xx.xx(+x.xx%)」を直接読む。
    try:
        response = requests.get(
            "https://finance.yahoo.co.jp/quote/998405.T",
            headers=headers,
            timeout=12,
        )
        response.raise_for_status()
        plain = html.unescape(re.sub(r"<[^>]+>", " ", response.text))
        plain = plain.replace("％", "%").replace("＋", "+").replace("－", "-").replace("−", "-")
        plain = re.sub(r"\s+", " ", plain)

        patterns = [
            r"前日比\s*([+\-]?\d[\d,]*(?:\.\d+)?)\s*\(\s*([+\-]?\d+(?:\.\d+)?)\s*%\s*\)",
            r"前日比.{0,80}?([+\-]?\d+(?:\.\d+)?)\s*%",
        ]
        for pattern in patterns:
            match = re.search(pattern, plain)
            if match:
                pct = match.group(2) if match.lastindex and match.lastindex >= 2 else match.group(1)
                return make_item(float(pct.replace(",", "")))
    except Exception:
        pass

    # 2) 詳細ページがブロックされた場合は時系列ページから直近2営業日の終値を読む。
    try:
        response = requests.get(
            "https://finance.yahoo.co.jp/quote/998405.T/history",
            headers=headers,
            timeout=12,
        )
        response.raise_for_status()
        plain = html.unescape(re.sub(r"<[^>]+>", " ", response.text))
        plain = re.sub(r"\s+", " ", plain)

        row_pattern = re.compile(
            r"(20\d{2}/\d{1,2}/\d{1,2})\s+"
            r"([\d,]+(?:\.\d+)?)\s+"  # 始値
            r"([\d,]+(?:\.\d+)?)\s+"  # 高値
            r"([\d,]+(?:\.\d+)?)\s+"  # 安値
            r"([\d,]+(?:\.\d+)?)"        # 終値
        )
        rows = row_pattern.findall(plain)
        if len(rows) >= 2:
            latest_date, _, _, _, latest_close = rows[0]
            _, _, _, _, previous_close = rows[1]
            latest = float(latest_close.replace(",", ""))
            previous = float(previous_close.replace(",", ""))
            if previous:
                change = (latest / previous - 1) * 100
                return make_item(change, latest, previous, latest_date)
    except Exception:
        pass

    # 3) Yahoo Japanへ接続できない環境向けにInvesting.comのTOPIXページを試す。
    try:
        response = requests.get(
            "https://jp.investing.com/indices/topix",
            headers={
                "User-Agent": "Mozilla/5.0",
                "Accept-Language": "ja-JP,ja;q=0.9,en;q=0.5",
            },
            timeout=12,
        )
        response.raise_for_status()
        plain = html.unescape(re.sub(r"<[^>]+>", " ", response.text))
        plain = plain.replace("％", "%").replace("＋", "+").replace("－", "-").replace("−", "-")
        plain = re.sub(r"\s+", " ", plain)

        # TOPIX見出し付近にある「4,066.00 +66.66(+1.67%)」型を拾う。
        match = re.search(
            r"TOPIX.{0,500}?([\d,]+(?:\.\d+)?)\s+([+\-]?\d[\d,]*(?:\.\d+)?)\s*\(\s*([+\-]?\d+(?:\.\d+)?)\s*%\s*\)",
            plain,
            re.IGNORECASE,
        )
        if match:
            close = float(match.group(1).replace(",", ""))
            change_pct = float(match.group(3))
            previous = close / (1 + change_pct / 100.0) if change_pct != -100 else None
            item = make_item(change_pct, close, previous)
            item["interval"] = "investing-web"
            item["source"] = "TOPIX"
            return item
    except Exception:
        pass

    return None


def fetch_market_snapshot(symbol):
    """
    朝の地合い用。
    まず1分足→5分足→15分足の順で、その時点で取得できる最新値を探す。
    騰落率は「最新値 ÷ 前営業日終値 - 1」で計算する。
    イントラデイが取れない場合だけ日足へフォールバックする。
    """
    symbols = _as_market_symbols(symbol)

    for active_symbol in symbols:
        try:
            daily = _download_yf(active_symbol, "10d", "1d", prepost=False, timeout=20)
            if daily is None or "Close" not in daily.columns:
                continue
            daily_close = daily["Close"].dropna()
            if len(daily_close) < 2:
                continue

            intraday = None
            used_interval = None
            for interval, period in [("1m", "7d"), ("5m", "10d"), ("15m", "30d")]:
                intraday = _download_yf(active_symbol, period, interval, prepost=True, timeout=20)
                if intraday is not None and "Close" in intraday.columns and not intraday["Close"].dropna().empty:
                    used_interval = interval
                    break
                intraday = None

            if intraday is not None:
                close = intraday["Close"].dropna()
                latest = float(close.iloc[-1])
                latest_raw_time = close.index[-1]
                try:
                    intraday_date = pd.Timestamp(latest_raw_time).date()
                    daily_last_date = pd.Timestamp(daily_close.index[-1]).date()
                except Exception:
                    intraday_date = None
                    daily_last_date = None

                if intraday_date is not None and intraday_date == daily_last_date:
                    previous = float(daily_close.iloc[-2])
                else:
                    previous = float(daily_close.iloc[-1])

                return {
                    "close": latest,
                    "previous": previous,
                    "change": (latest / previous - 1) * 100 if previous else None,
                    "date": str(intraday_date) if intraday_date else str(latest_raw_time)[:10],
                    "time": _timestamp_to_jst(latest_raw_time),
                    "asof": _timestamp_to_jst(latest_raw_time),
                    "interval": used_interval,
                    "symbol": active_symbol,
                    "live": True,
                }

            latest = float(daily_close.iloc[-1])
            previous = float(daily_close.iloc[-2])
            daily_time = daily_close.index[-1]
            return {
                "close": latest,
                "previous": previous,
                "change": (latest / previous - 1) * 100 if previous else None,
                "date": str(daily_time)[:10],
                "time": None,
                "asof": _daily_market_asof(active_symbol, daily_time),
                "interval": "1d",
                "symbol": active_symbol,
                "live": False,
            }
        except Exception:
            continue
    return None


def _valid_market_item(item):
    """表示に使える前日比が入っているか確認する。"""
    if not item:
        return False
    change = item.get("change")
    try:
        return change is not None and np.isfinite(float(change))
    except Exception:
        return False


def fetch_topix_snapshot():
    """
    TOPIXはYahoo/yfinanceで取りにくいことがあるため、
    取得経路を分けて、前日比が実際に取れたものだけ採用する。
    """
    item = _fetch_topix_web_fallback()
    if _valid_market_item(item):
        return item

    item = fetch_market_snapshot(["998405.T", "^TOPX"])
    if _valid_market_item(item):
        return item

    try:
        proxy = fetch_stock_snapshot("1306")
        if _valid_market_item(proxy):
            proxy = dict(proxy)
            proxy["proxy"] = True
            proxy["source"] = "1306.T"
            return proxy
    except Exception:
        pass

    return None



def fetch_stock_snapshot(code):
    """
    個別銘柄について、取得できる最新の「有効な現在値」を返す。

    ルール:
    - 東証取引時間中は当日の最新イントラデイ値を使う。
    - 15:30以降・土日・休場日は、確定した最新日足の終値を優先する。
    - 当日15:30直後など日足更新前だけは、当日の最終イントラデイ値を一時的に使う。

    これにより、休場日に前営業日の1分足終値を「現在値」と誤認しない。
    """
    symbol = f"{normalize_code(code)}.T"
    try:
        daily = _download_yf(symbol, "10d", "1d", prepost=False, timeout=20)
        if daily is None or "Close" not in daily.columns:
            return None
        daily = daily.dropna(subset=["Close"])
        if len(daily) < 2:
            return None

        daily_close = daily["Close"].dropna()
        daily_last_date = pd.Timestamp(daily_close.index[-1]).date()
        now_jst = pd.Timestamp.now(tz=ZoneInfo("Asia/Tokyo"))
        today_jst = now_jst.date()
        market_day = now_jst.weekday() < 5
        before_close = (now_jst.hour, now_jst.minute) < (15, 30)

        intraday_item = None

        for interval, period in [("1m", "7d"), ("5m", "10d"), ("15m", "30d")]:
            intraday = _download_yf(symbol, period, interval, prepost=False, timeout=20)
            if intraday is None or "Close" not in intraday.columns:
                continue
            intraday = intraday.dropna(subset=["Close"])
            if intraday.empty:
                continue

            idx = pd.DatetimeIndex(intraday.index)
            if idx.tz is None:
                idx = idx.tz_localize("UTC")
            idx_jst = idx.tz_convert(ZoneInfo("Asia/Tokyo"))
            intraday = intraday.copy()
            intraday.index = idx_jst

            latest_time = intraday.index[-1]
            latest_date = latest_time.date()
            today_rows = intraday[intraday.index.date == latest_date]
            if today_rows.empty:
                today_rows = intraday.tail(1)

            close = float(today_rows["Close"].dropna().iloc[-1])
            open_ = float(today_rows["Open"].dropna().iloc[0]) if "Open" in today_rows else close
            high = float(today_rows["High"].max()) if "High" in today_rows else close
            low = float(today_rows["Low"].min()) if "Low" in today_rows else close
            volume = float(today_rows["Volume"].fillna(0).sum()) if "Volume" in today_rows else None

            previous = float(daily_close.iloc[-2] if daily_last_date == latest_date else daily_close.iloc[-1])

            intraday_item = {
                "symbol": symbol,
                "close": close,
                "open": open_,
                "high": high,
                "low": low,
                "volume": volume,
                "previous": previous,
                "change": (close / previous - 1) * 100 if previous else None,
                "date": str(latest_date),
                "time": latest_time,
                "asof": latest_time,
                "interval": interval,
                "live": True,
            }
            break

        # 東証の当日取引中だけイントラデイを「現在値」として採用する。
        if (
            intraday_item is not None
            and market_day
            and before_close
            and pd.Timestamp(intraday_item["asof"]).date() == today_jst
        ):
            return intraday_item

        # 15:30以降で当日の日足がまだ未反映なら、当日の最終イントラデイを暫定採用。
        if (
            intraday_item is not None
            and market_day
            and not before_close
            and pd.Timestamp(intraday_item["asof"]).date() == today_jst
            and daily_last_date < today_jst
        ):
            intraday_item["live"] = False
            intraday_item["interval"] = intraday_item["interval"] + "-close-wait"
            return intraday_item

        # 土日・休場日・引け後は、確定した最新日足の終値を使う。
        latest = daily.iloc[-1]
        previous = float(daily_close.iloc[-2])
        daily_time = daily.index[-1]
        return {
            "symbol": symbol,
            "close": float(latest["Close"]),
            "open": float(latest["Open"]) if "Open" in daily.columns else float(latest["Close"]),
            "high": float(latest["High"]) if "High" in daily.columns else float(latest["Close"]),
            "low": float(latest["Low"]) if "Low" in daily.columns else float(latest["Close"]),
            "volume": float(latest["Volume"]) if "Volume" in daily.columns else None,
            "previous": previous,
            "change": (float(latest["Close"]) / previous - 1) * 100 if previous else None,
            "date": str(pd.Timestamp(daily_time).date()),
            "time": None,
            "asof": pd.Timestamp(f"{pd.Timestamp(daily_time).date()} 15:30", tz=ZoneInfo("Asia/Tokyo")),
            "interval": "1d",
            "live": False,
        }
    except Exception:
        return None

def confirmed_daily_data(data, snapshot=None):
    """移動平均など確定足前提の計算から、場中の未確定日足を除く。"""
    df = data.copy()
    if df.empty or not snapshot or not snapshot.get("live"):
        return df
    try:
        last_date = pd.Timestamp(df.index[-1]).date()
        snap_date = pd.Timestamp(snapshot.get("asof") or snapshot.get("time")).date()
        snap_time = pd.Timestamp(snapshot.get("asof") or snapshot.get("time"))
        if snap_time.tzinfo is None:
            snap_time = snap_time.tz_localize("UTC").tz_convert(ZoneInfo("Asia/Tokyo"))
        else:
            snap_time = snap_time.tz_convert(ZoneInfo("Asia/Tokyo"))
        # 15:30より前の当日足は未確定として除外する。
        if last_date == snap_date and (snap_time.hour, snap_time.minute) < (15, 30):
            df = df.iloc[:-1]
    except Exception:
        pass
    return df


def _current_price(snapshot, fallback):
    try:
        value = snapshot.get("close") if snapshot else None
        return float(value) if value is not None and np.isfinite(float(value)) else float(fallback)
    except Exception:
        return float(fallback)


def _snapshot_asof_text(snapshot, fallback_date=None):
    if snapshot:
        value = snapshot.get("asof") or snapshot.get("time")
        if value is not None:
            try:
                ts = pd.Timestamp(value)
                if ts.tzinfo is None:
                    ts = ts.tz_localize("UTC")
                ts = ts.tz_convert(ZoneInfo("Asia/Tokyo"))
                return ts.strftime("%Y-%m-%d %H:%M JST")
            except Exception:
                pass
        if snapshot.get("date"):
            return str(snapshot.get("date"))
    return str(fallback_date or "-")

def morning_score(change):
    if change is None or pd.isna(change):
        return 0
    if change >= 1.0:
        return 2
    if change >= 0.30:
        return 1
    if change <= -1.0:
        return -2
    if change <= -0.30:
        return -1
    return 0


def morning_label(score):
    if score >= 2:
        return "強い追い風"
    if score == 1:
        return "追い風"
    if score <= -2:
        return "強い逆風"
    if score == -1:
        return "逆風"
    return "中立"


def _fx_label(usd_jpy):
    if usd_jpy is None or usd_jpy.get("change") is None:
        return "取得不可"
    change = usd_jpy["change"]
    if change >= 0.5:
        return "円安方向"
    if change <= -0.5:
        return "円高方向"
    return "大きな変化なし"


def _average_market_score(changes):
    scores = [morning_score(value) for value in changes if value is not None and not pd.isna(value)]
    if not scores:
        return None
    return sum(scores) / len(scores)


def _weighted_market_score(weighted_changes):
    total = 0.0
    weight_sum = 0.0
    for value, weight in weighted_changes:
        if value is None or pd.isna(value):
            continue
        total += morning_score(value) * float(weight)
        weight_sum += float(weight)
    if weight_sum == 0:
        return None
    return total / weight_sum


def _market_label_from_average(score):
    if score is None or pd.isna(score):
        return "判定保留"
    if score >= 1.0:
        return "強い追い風"
    if score >= 0.35:
        return "追い風"
    if score <= -1.0:
        return "強い逆風"
    if score <= -0.35:
        return "逆風"
    return "中立"


def _recency_factor(item, newest_asof):
    """新しい市場データほど地合い判定への影響を強くする。"""
    if not item or newest_asof is None:
        return 0.35
    asof = item.get("asof") or item.get("time")
    if asof is None:
        return 0.35
    try:
        age = max(0.0, (newest_asof - pd.Timestamp(asof)).total_seconds() / 3600.0)
    except Exception:
        return 0.35
    if age <= 3:
        return 1.0
    if age <= 8:
        return 0.80
    if age <= 16:
        return 0.50
    if age <= 24:
        return 0.30
    return 0.15


def _current_overall_score(markets):
    """市場が閉まっていても、より新しい市場情報を重視して現在の地合いを判定する。"""
    base_weights = {
        "futures": 0.30,
        "nasdaq": 0.25,
        "nikkei": 0.15,
        "topix": 0.15,
        "kospi": 0.15,
    }
    asofs = []
    for key in base_weights:
        item = markets.get(key)
        if not item:
            continue
        asof = item.get("asof") or item.get("time")
        if asof is not None:
            try:
                asofs.append(pd.Timestamp(asof))
            except Exception:
                pass
    newest = max(asofs) if asofs else None

    total = 0.0
    weight_sum = 0.0
    for key, base_weight in base_weights.items():
        item = markets.get(key)
        if not item:
            continue
        change = item.get("change")
        if change is None or pd.isna(change):
            continue
        weight = base_weight * _recency_factor(item, newest)
        total += morning_score(change) * weight
        weight_sum += weight
    if weight_sum == 0:
        return None
    return total / weight_sum


def _build_morning_brief(markets):
    """アプリを開いた時点で取得できる最新データから地合いを作る。"""
    nikkei = markets.get("nikkei")
    topix = markets.get("topix")
    nasdaq = markets.get("nasdaq")
    sox = markets.get("sox")
    kospi = markets.get("kospi")
    usd_jpy = markets.get("usd_jpy")
    futures = markets.get("futures")

    def change_of(item):
        return item.get("change") if item else None

    # 単純平均では、古い日本市場の終値が新しい米国・先物の動きを
    # 打ち消してしまうため、データの鮮度を加味して判定する。
    overall_score = _current_overall_score(markets)

    semiconductor_score = _weighted_market_score([
        (change_of(sox), 0.45),
        (change_of(nasdaq), 0.20),
        (change_of(futures), 0.20),
        (change_of(kospi), 0.15),
    ])

    latest_times = [
        (item.get("asof") or item.get("time"))
        for item in markets.values()
        if item and (item.get("asof") is not None or item.get("time") is not None)
    ]
    latest_time = max(latest_times) if latest_times else None

    return {
        "overall": _market_label_from_average(overall_score),
        "semiconductor": _market_label_from_average(semiconductor_score),
        "futures": futures,
        "nikkei": nikkei,
        "topix": topix,
        "nasdaq": nasdaq,
        "sox": sox,
        "kospi": kospi,
        "usd_jpy": usd_jpy,
        "fx": _fx_label(usd_jpy),
        "latest_time": latest_time,
    }


@st.cache_data(ttl=60, show_spinner=False)
def get_morning_market_brief():
    """市場が開いていれば最新のイントラデイ値、閉場中なら最後に取得できた値を使う。"""
    markets = {
        "futures": fetch_market_snapshot("NKD=F"),
        "nikkei": fetch_market_snapshot("^N225"),
        "topix": fetch_topix_snapshot(),
        "nasdaq": fetch_market_snapshot("^IXIC"),
        "sox": fetch_market_snapshot("^SOX"),
        "kospi": fetch_market_snapshot("^KS11"),
        "usd_jpy": fetch_market_snapshot("JPY=X"),
    }
    return _build_morning_brief(markets)


def _fmt_pct(value):
    if value is None or pd.isna(value):
        return "取得不可"
    return f"{value:+.2f}%"


def _fmt_price(value):
    if value is None or pd.isna(value):
        return "取得不可"
    try:
        number = float(value)
        if abs(number) >= 1000:
            return f"{number:,.0f}"
        if abs(number) >= 100:
            return f"{number:,.2f}"
        return f"{number:,.3f}"
    except Exception:
        return "取得不可"


def _fmt_market_line(label, item):
    """朝の展望はコンパクトに、前日比％だけを表示する。"""
    if not item:
        return f"{label}　取得不可"
    change = _fmt_pct(item.get("change"))
    return f"{label}　{change}"


def show_morning_market_brief():
    """朝の展望は、アクセス時点の最新スナップショットを1段だけ表示する。"""
    st.markdown("### 🌅 朝の展望")
    brief = get_morning_market_brief()

    latest_time = brief.get("latest_time")
    if latest_time is not None:
        st.caption(f"最新取得データ：{latest_time.strftime('%Y-%m-%d %H:%M')} JST")
    else:
        st.caption("最新取得データ：各市場の最終取得値")

    st.markdown(f"**市場全体：{brief.get('overall', '判定保留')}**")
    st.write(_fmt_market_line("日経先物", brief.get("futures")))
    st.write(_fmt_market_line("日経平均", brief.get("nikkei")))
    topix_item = brief.get("topix")
    topix_label = "TOPIX参考(1306 ETF)" if topix_item and topix_item.get("proxy") else "TOPIX"
    st.write(_fmt_market_line(topix_label, topix_item))
    st.write(_fmt_market_line("NASDAQ", brief.get("nasdaq")))
    st.write(_fmt_market_line("KOSPI", brief.get("kospi")))

    usd_jpy = brief.get("usd_jpy")
    if usd_jpy:
        st.write(f"{_fmt_market_line('USD/JPY', usd_jpy)}　（{brief.get('fx', '取得不可')}）")
    else:
        st.write("USD/JPY　取得不可")

    st.markdown(f"**半導体地合い：{brief.get('semiconductor', '判定保留')}**")
    st.write(_fmt_market_line("SOX", brief.get("sox")))
    st.caption("半導体地合いはSOXを最重視し、NASDAQ・日経先物・KOSPIも加味します。")
    st.caption("各指標は、アプリを開いた時点で取得できる最新値を優先します。市場が閉まっている場合は、その市場で最後に取得できた値を表示します。")
    st.caption("個別銘柄分析では、その銘柄に関連するセクター・テーマ別の地合いも確認できるよう対応予定です。")


@st.cache_data(ttl=3600, show_spinner=False)
def get_intraday_daytrade_adjustment(code):
    """直近の5分足を、画面を増やさずデイトレ適性へ反映する。"""
    try:
        data = yf.download(
            f"{code}.T",
            period="60d",
            interval="5m",
            auto_adjust=False,
            progress=False,
            prepost=False,
            timeout=30,
        )
        if data is None or data.empty:
            return 0
        if isinstance(data.columns, pd.MultiIndex):
            data.columns = data.columns.get_level_values(0)
        data = data[["Open", "High", "Low", "Close", "Volume"]].dropna().copy()
        if data.index.tz is not None:
            data.index = data.index.tz_convert("Asia/Tokyo")

        opening_ranges = []
        opening_turnovers = []
        for _, session in data.groupby(data.index.date):
            opening = session.between_time("09:00", "09:29")
            if len(opening) < 4:
                continue
            open_price = float(opening["Open"].iloc[0])
            if open_price <= 0:
                continue
            opening_ranges.append(
                (float(opening["High"].max()) - float(opening["Low"].min()))
                / open_price * 100
            )
            opening_turnovers.append(
                float((opening["Close"] * opening["Volume"]).sum()) / 100_000_000
            )

        if len(opening_ranges) < 10:
            return 0

        range_average = float(np.mean(opening_ranges))
        turnover_average = float(np.mean(opening_turnovers))
        adjustment = 0
        if range_average >= 2.0:
            adjustment += 4
        elif range_average >= 1.2:
            adjustment += 2
        elif range_average < 0.5:
            adjustment -= 3

        if turnover_average >= 10:
            adjustment += 4
        elif turnover_average >= 3:
            adjustment += 2
        elif turnover_average < 0.5:
            adjustment -= 3

        return adjustment
    except Exception:
        return 0


def calculate_atr(data, period=14):
    high = data["High"]
    low = data["Low"]
    close = data["Close"]

    prev_close = close.shift(1)

    tr = pd.concat(
        [
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs()
        ],
        axis=1
    ).max(axis=1)

    return tr.rolling(period).mean()


# =========================================================
# デイトレ簡易分析
# =========================================================
def analyze_daytrade(data, intraday_adjustment=0, current_snapshot=None):
    # MA/ATR/過去統計は最新の確定日足、現在値・現在位置は取得できる最新値を使う。
    df = confirmed_daily_data(data, current_snapshot)

    df["MA5"] = df["Close"].rolling(5).mean()
    df["MA25"] = df["Close"].rolling(25).mean()
    df["ATR14"] = calculate_atr(df, 14)
    df["Volume20"] = df["Volume"].rolling(20).mean()

    df["Turnover"] = df["Close"] * df["Volume"]
    df["Turnover20"] = df["Turnover"].rolling(20).mean()

    df["High20"] = df["High"].rolling(20).max()
    df["Low20"] = df["Low"].rolling(20).min()

    latest = df.iloc[-1]
    previous = df.iloc[-2]

    confirmed_close = float(latest["Close"])
    close = _current_price(current_snapshot, confirmed_close)
    ma5 = float(latest["MA5"])
    ma25 = float(latest["MA25"])
    atr14 = float(latest["ATR14"])
    volume = float(latest["Volume"])
    volume20 = float(latest["Volume20"])
    turnover20 = float(latest["Turnover20"])
    high20 = float(latest["High20"])
    low20 = float(latest["Low20"])
    prev_close = float(latest["Close"])
    prev_high = float(latest["High"])
    prev_low = float(latest["Low"])

    atr_pct = atr14 / close * 100 if close > 0 else 0
    volume_ratio = volume / volume20 if volume20 > 0 else 0
    ma_gap_pct = (ma5 / ma25 - 1) * 100 if ma25 > 0 else 0

    if high20 > low20:
        range_position = (close - low20) / (high20 - low20)
    else:
        range_position = 0.5

    # 流動性 25点
    turnover_oku = turnover20 / 100_000_000

    if turnover_oku >= 100:
        liquidity_score = 25
        liquidity_label = "非常に高い"
    elif turnover_oku >= 30:
        liquidity_score = 22
        liquidity_label = "高い"
    elif turnover_oku >= 10:
        liquidity_score = 18
        liquidity_label = "十分"
    elif turnover_oku >= 3:
        liquidity_score = 12
        liquidity_label = "やや少ない"
    else:
        liquidity_score = 5
        liquidity_label = "少ない"

    # 値幅 25点
    if 3 <= atr_pct <= 7:
        atr_score = 25
    elif 2 <= atr_pct < 3:
        atr_score = 21
    elif 1.3 <= atr_pct < 2:
        atr_score = 16
    elif 7 < atr_pct <= 10:
        atr_score = 18
    elif atr_pct > 10:
        atr_score = 10
    else:
        atr_score = 8

    # 出来高活性 20点
    if volume_ratio >= 2:
        volume_score = 20
    elif volume_ratio >= 1.4:
        volume_score = 18
    elif volume_ratio >= 1:
        volume_score = 15
    elif volume_ratio >= 0.7:
        volume_score = 10
    else:
        volume_score = 5

    # 短期トレンド明確さ 20点
    abs_gap = abs(ma_gap_pct)

    if abs_gap >= 4:
        trend_score = 20
    elif abs_gap >= 2:
        trend_score = 17
    elif abs_gap >= 0.8:
        trend_score = 13
    else:
        trend_score = 8

    if ma_gap_pct > 0.5:
        trend_label = "上向き"
    elif ma_gap_pct < -0.5:
        trend_label = "下向き"
    else:
        trend_label = "横ばい"

    # 価格位置 10点
    if range_position >= 0.85:
        position_score = 10
        position_label = "20日高値圏"
    elif range_position <= 0.15:
        position_score = 10
        position_label = "20日安値圏"
    elif range_position >= 0.70:
        position_score = 8
        position_label = "高値寄り"
    elif range_position <= 0.30:
        position_score = 8
        position_label = "安値寄り"
    else:
        position_score = 5
        position_label = "中間圏"

    total = (
        liquidity_score
        + atr_score
        + volume_score
        + trend_score
        + position_score
        + intraday_adjustment
    )
    total = max(0, min(100, total))

    if total >= 85:
        grade = "A"
        grade_text = "デイトレ候補としてかなり良好"
    elif total >= 70:
        grade = "B"
        grade_text = "デイトレ候補として十分"
    elif total >= 55:
        grade = "C"
        grade_text = "条件次第"
    else:
        grade = "D"
        grade_text = "優先度低め"

    comments = []

    if turnover_oku >= 30:
        comments.append("売買代金が大きく、流動性は十分あります。")
    elif turnover_oku < 3:
        comments.append("売買代金が少なく、注文の通りやすさには注意が必要です。")

    if atr_pct >= 3:
        comments.append("日中の値幅が出やすい銘柄です。")
    elif atr_pct < 1.3:
        comments.append("値幅は小さめで、デイトレでは動き不足になりやすいです。")

    if volume_ratio >= 1.4:
        comments.append("直近の出来高が活性化しています。")
    elif volume_ratio < 0.7:
        comments.append("直近の出来高はやや低調です。")

    comments.append(f"短期トレンドは「{trend_label}」です。")
    comments.append(f"現在位置は「{position_label}」です。")

    # -----------------------------------------------------------------
    # 当日デイトレ展望（前日の日足を基準にした朝の準備用）
    # -----------------------------------------------------------------
    close_vs_ma5_pct = (close / ma5 - 1) * 100 if ma5 > 0 else 0
    if ma_gap_pct >= 0.8 and close_vs_ma5_pct >= 0:
        outlook = "上方向優勢"
        outlook_detail = "前日高値を上抜けて維持できるかを確認する局面です。"
        invalidation = "前日安値を明確に割れるなら、上目線は一度取り消しです。"
    elif ma_gap_pct <= -0.8 and close_vs_ma5_pct <= 0:
        outlook = "下方向警戒"
        outlook_detail = "前日安値を割るか、戻りが前日高値で抑えられるかを確認する局面です。"
        invalidation = "前日高値を上抜けて維持するなら、下目線は一度取り消しです。"
    else:
        outlook = "上下拮抗"
        outlook_detail = "寄り後に前日高値・安値のどちらを先に抜けて維持するかを待つ局面です。"
        invalidation = "どちらかの前日値を抜けて維持した側を、その日の優先方向として見ます。"

    confidence_points = 0
    if abs(ma_gap_pct) >= 2:
        confidence_points += 1
    if volume_ratio >= 1.4:
        confidence_points += 1
    if atr_pct >= 2:
        confidence_points += 1

    if confidence_points >= 3:
        outlook_confidence = "高め"
    elif confidence_points >= 2:
        outlook_confidence = "中"
    else:
        outlook_confidence = "低め"

    return {
        "score": int(total),
        "grade": grade,
        "grade_text": grade_text,
        "close": close,
        "confirmed_close": confirmed_close,
        "data_asof": _snapshot_asof_text(current_snapshot, pd.Timestamp(df.index[-1]).strftime("%Y-%m-%d")),
        "turnover_oku": turnover_oku,
        "atr_pct": atr_pct,
        "volume_ratio": volume_ratio,
        "trend": trend_label,
        "ma_gap_pct": ma_gap_pct,
        "position": position_label,
        "comments": comments,
        "outlook": outlook,
        "outlook_detail": outlook_detail,
        "outlook_confidence": outlook_confidence,
        "invalidation": invalidation,
        "prev_close": prev_close,
        "prev_high": prev_high,
        "prev_low": prev_low,
        "atr14": atr14,
        "up_target": prev_close + atr14,
        "down_target": max(0, prev_close - atr14),
    }


# =========================================================
# スイング分析
# =========================================================
def analyze_swing(data, current_snapshot=None):
    # MA/ATR/出来高などは確定日足、現在位置・騰落率は最新値を使用。
    df = confirmed_daily_data(data, current_snapshot)

    df["MA5"] = df["Close"].rolling(5).mean()
    df["MA25"] = df["Close"].rolling(25).mean()
    df["MA75"] = df["Close"].rolling(75).mean()

    df["ATR14"] = calculate_atr(df, 14)
    df["Volume20"] = df["Volume"].rolling(20).mean()

    df["High20"] = df["High"].rolling(20).max()
    df["Low20"] = df["Low"].rolling(20).min()
    df["High60"] = df["High"].rolling(60).max()
    df["Low60"] = df["Low"].rolling(60).min()

    latest = df.iloc[-1]

    confirmed_close = float(latest["Close"])
    close = _current_price(current_snapshot, confirmed_close)
    ma5 = float(latest["MA5"])
    ma25 = float(latest["MA25"])
    ma75 = float(latest["MA75"])
    atr14 = float(latest["ATR14"])
    volume = float(latest["Volume"])
    volume20 = float(latest["Volume20"])

    high20 = float(latest["High20"])
    low20 = float(latest["Low20"])
    high60 = float(latest["High60"])
    low60 = float(latest["Low60"])

    atr_pct = atr14 / close * 100 if close > 0 else 0
    volume_ratio = volume / volume20 if volume20 > 0 else 0

    return20 = (
        close / float(df["Close"].iloc[-21]) - 1
    ) * 100 if len(df) >= 21 else 0

    return60 = (
        close / float(df["Close"].iloc[-61]) - 1
    ) * 100 if len(df) >= 61 else 0

    # -----------------------------------------------------
    # 1. トレンド 30点
    # -----------------------------------------------------
    trend_score = 0

    if close > ma25:
        trend_score += 8

    if close > ma75:
        trend_score += 7

    if ma5 > ma25:
        trend_score += 7

    if ma25 > ma75:
        trend_score += 8

    if close > ma25 and ma25 > ma75:
        trend_label = "上昇基調"
    elif close < ma25 and ma25 < ma75:
        trend_label = "下降基調"
    elif close > ma75:
        trend_label = "中期は底堅い"
    else:
        trend_label = "方向感が弱い"

    # -----------------------------------------------------
    # 2. 価格位置・支持抵抗 20点
    # -----------------------------------------------------
    if high60 > low60:
        pos60 = (close - low60) / (high60 - low60)
    else:
        pos60 = 0.5

    if 0.55 <= pos60 <= 0.85:
        position_score = 20
        position_label = "上昇余地を残した高値寄り"
    elif 0.40 <= pos60 < 0.55:
        position_score = 16
        position_label = "60日レンジ中段"
    elif 0.85 < pos60 <= 1.02:
        position_score = 15
        position_label = "60日高値圏"
    elif 0.20 <= pos60 < 0.40:
        position_score = 11
        position_label = "やや安値寄り"
    else:
        position_score = 8
        position_label = "60日レンジ端"

    # -----------------------------------------------------
    # 3. モメンタム 20点
    # -----------------------------------------------------
    momentum_score = 0

    if 2 <= return20 <= 15:
        momentum_score += 12
    elif 0 < return20 < 2:
        momentum_score += 8
    elif 15 < return20 <= 25:
        momentum_score += 8
    elif return20 > 25:
        momentum_score += 4
    elif -5 <= return20 <= 0:
        momentum_score += 5
    else:
        momentum_score += 2

    if return60 > 0:
        momentum_score += 8
    elif return60 > -5:
        momentum_score += 5
    else:
        momentum_score += 2

    # -----------------------------------------------------
    # 4. 出来高 15点
    # -----------------------------------------------------
    if 1.0 <= volume_ratio < 1.8:
        volume_score = 15
        volume_label = "適度に活発"
    elif 1.8 <= volume_ratio <= 3.0:
        volume_score = 13
        volume_label = "かなり活発"
    elif 0.7 <= volume_ratio < 1.0:
        volume_score = 10
        volume_label = "やや低め"
    elif volume_ratio > 3.0:
        volume_score = 9
        volume_label = "急増・材料反応に注意"
    else:
        volume_score = 6
        volume_label = "低調"

    # -----------------------------------------------------
    # 5. 値幅 15点
    # -----------------------------------------------------
    if 1.5 <= atr_pct <= 4.5:
        volatility_score = 15
        volatility_label = "スイング向き"
    elif 1.0 <= atr_pct < 1.5:
        volatility_score = 11
        volatility_label = "やや穏やか"
    elif 4.5 < atr_pct <= 7:
        volatility_score = 10
        volatility_label = "値幅大きめ"
    elif atr_pct > 7:
        volatility_score = 6
        volatility_label = "値動きが荒い"
    else:
        volatility_score = 7
        volatility_label = "値幅小さめ"

    total = (
        trend_score
        + position_score
        + momentum_score
        + volume_score
        + volatility_score
    )

    if total >= 82:
        grade = "A"
        grade_text = "スイング候補としてかなり良好"
    elif total >= 68:
        grade = "B"
        grade_text = "スイング候補として十分"
    elif total >= 52:
        grade = "C"
        grade_text = "条件を見ながら検討"
    else:
        grade = "D"
        grade_text = "現時点では優先度低め"

    # 支持・抵抗の簡易表示
    resistance = high20
    support = low20

    comments = []

    comments.append(f"5日・25日・75日線から見ると「{trend_label}」です。")

    if return20 >= 10:
        comments.append(
            f"直近20営業日で +{return20:.1f}% と上昇が進んでおり、"
            "追いかけ買いには注意が必要です。"
        )
    elif return20 >= 2:
        comments.append(
            f"直近20営業日は +{return20:.1f}% で、"
            "適度な上向きモメンタムがあります。"
        )
    elif return20 <= -10:
        comments.append(
            f"直近20営業日は {return20:.1f}% と弱く、"
            "反転確認が必要です。"
        )

    if volume_ratio >= 1.5:
        comments.append("出来高が増えており、資金流入の有無を確認したい局面です。")
    elif volume_ratio < 0.7:
        comments.append("出来高は低調で、上昇・反発の持続力を確認したい局面です。")

    if close >= high20 * 0.98:
        comments.append("20日高値に近く、上抜けできるかが重要です。")
    elif close <= low20 * 1.02:
        comments.append("20日安値に近く、下げ止まりを確認したい位置です。")

    return {
        "score": int(total),
        "grade": grade,
        "grade_text": grade_text,
        "close": close,
        "confirmed_close": confirmed_close,
        "data_asof": _snapshot_asof_text(current_snapshot, pd.Timestamp(df.index[-1]).strftime("%Y-%m-%d")),
        "trend": trend_label,
        "position": position_label,
        "return20": return20,
        "return60": return60,
        "volume_ratio": volume_ratio,
        "volume_label": volume_label,
        "atr_pct": atr_pct,
        "volatility_label": volatility_label,
        "ma5": ma5,
        "ma25": ma25,
        "ma75": ma75,
        "support": support,
        "resistance": resistance,
        "high60": high60,
        "low60": low60,
        "data_date": (
            str(current_snapshot.get("date"))
            if current_snapshot and current_snapshot.get("date")
            else pd.Timestamp(df.index[-1]).strftime("%Y-%m-%d")
        ),
        "comments": comments
    }


def score_longterm_fundamentals(earnings, price_date=None):
    """中長期用の実績・通期見通しを70点で参考採点する（簡易・独自ルール）。"""
    empty = {"score": None, "coverage": 0, "provisional": True, "components": [],
             "reasons": ["業績データを取得できないため、業績点は算出しません。"], "notes": []}
    if not isinstance(earnings, dict) or not earnings.get("ok"):
        return empty

    try:
        disclosure = pd.Timestamp(earnings.get("disclosure_date"))
        price = pd.Timestamp(price_date)
        if pd.notna(disclosure) and pd.notna(price) and disclosure.normalize() > price.normalize():
            return {**empty, "reasons": ["株価基準日より後の決算は採点に使用しません。"]}
    except (TypeError, ValueError):
        pass

    def num(value):
        try:
            n = float(value)
            return n if np.isfinite(n) else None
        except (TypeError, ValueError):
            return None

    def change(current, previous):
        return (current - previous) / abs(previous) * 100 if (
            current is not None and previous is not None and previous > 0) else None

    components = []
    notes = []

    def add(label, score, maximum, explanation):
        components.append({"label": label, "score": score, "max": maximum,
                           "explanation": explanation})

    history = earnings.get("forecast_history") or {}
    previous = earnings.get("year_ago") or {}
    rev, op = num(earnings.get("revenue")), num(earnings.get("operating_income"))
    prev_rev, prev_op = num(previous.get("revenue")), num(previous.get("operating_income"))
    forecast_rev = num(earnings.get("forecast_revenue"))
    forecast_op = num(earnings.get("forecast_operating_income"))
    prior_annual = earnings.get("previous_annual_actual") or {}
    annual_rev = num(prior_annual.get("revenue"))
    annual_op = num(prior_annual.get("operating_income"))

    # 実績20点：同一四半期・同一集計期間だけを比較する。
    if op is not None and prev_op is not None:
        if prev_op <= 0 < op:
            pts, desc = 20, "営業利益が黒字転換"
        elif prev_op >= 0 > op:
            pts, desc = 0, "営業利益が赤字転落"
        elif prev_op < 0 and op < 0:
            pts, desc = (10 if op > prev_op else 0), ("営業赤字が縮小" if op > prev_op else "営業赤字が拡大または横ばい")
        elif prev_op > 0:
            rate = change(op, prev_op)
            pts = 16 if rate >= 20 else 13 if rate >= 10 else 10 if rate >= 0 else 5 if rate > -20 else 0
            desc = f"営業利益の前年同期比 {rate:+.1f}%"
        else:
            pts, desc = (10 if op == 0 else 0), "営業利益がゼロ付近"
        rev_rate = change(rev, prev_rev)
        if rev_rate is not None:
            pts = min(20, max(0, pts + (4 if rev_rate >= 10 else -4 if rev_rate <= -10 else 0)))
            desc += f"、売上高 {rev_rate:+.1f}%"
        add("前年同期の実績", pts, 20, desc)

    # 通期見通し25点：前期通期の確定実績と当期通期予想を比較。
    # 同じ対象年度の比較先を特定できない場合は欠損扱い（四半期実績とは比較しない）。
    if forecast_op is not None and annual_op is not None:
        if annual_op <= 0 < forecast_op:
            pts, desc = 25, "通期営業利益は黒字転換予想"
        elif annual_op >= 0 > forecast_op:
            pts, desc = 0, "通期営業利益は赤字転落予想"
        elif annual_op < 0 and forecast_op < 0:
            pts = 12 if forecast_op > annual_op else 0
            desc = "通期営業赤字は縮小予想" if pts else "通期営業赤字は拡大または横ばい予想"
        elif annual_op > 0:
            rate = change(forecast_op, annual_op)
            pts = 20 if rate >= 20 else 17 if rate >= 10 else 13 if rate >= 0 else 7 if rate > -20 else 0
            desc = f"通期営業利益の前期比予想 {rate:+.1f}%"
        else:
            pts, desc = (12 if forecast_op == 0 else 0), "通期営業利益はゼロ付近の予想"
        rev_rate = change(forecast_rev, annual_rev)
        if rev_rate is not None:
            pts = min(25, max(0, pts + (5 if rev_rate >= 10 else -5 if rev_rate <= -10 else 0)))
            desc += f"、売上高 {rev_rate:+.1f}%"
        add("通期会社予想と前期実績", pts, 25, desc)
    else:
        notes.append("通期会社予想と前期実績：対応する前期通期実績または当期予想がなく、採点対象外。")

    # 会社予想の修正15点：初回→最新（同一年度）。予想履歴がなければ欠損。
    if history:
        judgment = history.get("overall_judgment")
        scores = {"上方修正": 15, "予想維持": 8, "下方修正": 0,
                  "EPSのみ上方修正": 10, "EPSのみ下方修正": 5}
        if judgment in scores:
            add("会社予想の修正", scores[judgment], 15, f"同一年度の初回から最新：{judgment}")
        elif judgment == "混合修正":
            first = num((history.get("first") or {}).get("forecast_operating_income"))
            latest = num((history.get("latest") or {}).get("forecast_operating_income"))
            if first is not None and latest is not None:
                pts = 11 if latest > first else 4 if latest < first else 8
                add("会社予想の修正", pts, 15, "混合修正：営業利益予想の変更方向を参考")

    # 予想利益率10点：四半期の一時的な赤字と通期の予想利益率は別々に見せる。
    if forecast_rev is not None and forecast_rev > 0 and forecast_op is not None:
        margin = forecast_op / forecast_rev * 100
        pts = 10 if margin >= 15 else 8 if margin >= 10 else 6 if margin >= 5 else 3 if margin >= 0 else 0
        add("通期予想営業利益率", pts, 10, f"会社予想の営業利益率 {margin:.1f}%")

    if rev is not None and rev > 0 and op is not None:
        notes.append(f"直近決算の営業利益率 {op/rev*100:+.1f}%（実績の前年比とあわせて参照）")
    progress = num(earnings.get("operating_progress"))
    if progress is not None:
        notes.append(f"営業利益の単純進捗率 {progress:+.1f}%（季節性を補正していないため加点・減点なし）")

    # 決算DBの金額単位は百万円。残り期間の必要利益と前年同期間を比較する。
    # Q4は通期実績のため、残り期間の計算対象にしない。
    quarter = str(earnings.get("quarter", "")).upper().replace("Q", "")
    if quarter in ("1", "2", "3") and forecast_op is not None and op is not None:
        remaining = forecast_op - op
        notes.append(
            f"通期予想達成に残り期間で必要な営業利益：{remaining / 100:.1f}億円"
            f"（通期予想 {forecast_op / 100:.1f}億円 − 累計実績 {op / 100:.1f}億円）"
        )
        if annual_op is not None and prev_op is not None:
            prior_remaining = annual_op - prev_op
            notes.append(f"前年の同じ残り期間の営業利益：{prior_remaining / 100:.1f}億円")
            if prior_remaining > 0:
                required_growth = (remaining / prior_remaining - 1) * 100
                notes.append(
                    f"残り期間に必要な営業利益の前年同期比：{required_growth:+.1f}%"
                    "（達成確率ではなく必要水準の比較。季節性・会社計画の内訳は未反映、採点への加減点なし）"
                )
            else:
                notes.append("前年の残り期間の営業利益がゼロ以下のため、必要増益率は算出しません。")
        else:
            notes.append("前年の対応する通期・累計実績が不足しており、残り期間の前年比較はできません。")
    notes.append("会社予想は未達の可能性があり、実績とは区別して表示しています。")

    maximum = sum(item["max"] for item in components)
    if maximum < 45:
        return {"score": None, "coverage": maximum, "provisional": True,
                "components": components, "notes": notes,
                "reasons": ["採点可能な根拠が45/70点分に満たないため採点保留。"]}
    raw = sum(item["score"] for item in components)
    score = int(raw / maximum * 70 + 0.5)
    return {"score": score, "coverage": maximum, "provisional": maximum < 70,
            "components": components, "notes": notes,
            "reasons": ["採点できた項目のみを70点満点へ換算した暫定点です。" if maximum < 70
                        else "4項目を合計した簡易参考点です。"]}


# =========================================================
# 中長期分析
# =========================================================
def analyze_longterm(data, earnings=None, current_snapshot=None):
    """業績と約1年の株価位置から、中長期の保有候補としての強さを見る。"""
    # 長期MAは確定日足、現在株価・現在位置は取得できる最新値を使う。
    df = confirmed_daily_data(data, current_snapshot)
    df["MA50"] = df["Close"].rolling(50).mean()
    df["MA200"] = df["Close"].rolling(200).mean()

    latest = df.iloc[-1]
    confirmed_close = float(latest["Close"])
    close = _current_price(current_snapshot, confirmed_close)
    ma50 = float(latest["MA50"])
    ma200 = float(latest["MA200"])
    # 取得本数が252営業日にわずかに届かない場合でも、
    # 取得できた約1年分を使って高値・安値を出す。
    yearly_data = df.tail(252)
    # 欠損値や文字列を除外し、有効な高値・安値だけで年間レンジを計算する。
    yearly_highs = pd.to_numeric(yearly_data["High"], errors="coerce").replace([np.inf, -np.inf], np.nan).dropna()
    yearly_lows = pd.to_numeric(yearly_data["Low"], errors="coerce").replace([np.inf, -np.inf], np.nan).dropna()
    high252 = float(yearly_highs.max()) if not yearly_highs.empty else float("nan")
    low252 = float(yearly_lows.min()) if not yearly_lows.empty else float("nan")
    if current_snapshot:
        try:
            current_high = float(current_snapshot.get("high"))
            current_low = float(current_snapshot.get("low"))
            if np.isfinite(current_high):
                high252 = max(high252, current_high) if np.isfinite(high252) else current_high
            if np.isfinite(current_low):
                low252 = min(low252, current_low) if np.isfinite(low252) else current_low
        except Exception:
            pass

    return120 = (close / float(df["Close"].iloc[-121]) - 1) * 100 if len(df) >= 121 else 0
    return240 = (close / float(df["Close"].iloc[-241]) - 1) * 100 if len(df) >= 241 else 0
    valid_yearly_range = (np.isfinite(high252) and np.isfinite(low252)
                          and np.isfinite(close) and high252 > low252)
    range_position = (close - low252) / (high252 - low252) if valid_yearly_range else float("nan")

    # テクニカルは最大30点。中長期では、業績の補助確認として扱う。
    chart_score = 0
    if close > ma200:
        chart_score += 10
    if ma50 > ma200:
        chart_score += 8
    if return240 > 0:
        chart_score += 6
    elif return240 > -10:
        chart_score += 3
    if valid_yearly_range:
        if 0.30 <= range_position <= 0.85:
            chart_score += 6
        elif range_position > 0.85:
            chart_score += 4
        else:
            chart_score += 2

    if close > ma200 and ma50 > ma200:
        chart_label = "長期上昇基調"
    elif close < ma200 and ma50 < ma200:
        chart_label = "長期下降基調"
    else:
        chart_label = "長期は転換・調整局面"

    # 中長期専用の業績採点。スイング向けの決算鮮度補正は使わない。
    analysis_date = pd.Timestamp.now(tz=ZoneInfo("Asia/Tokyo")).strftime("%Y-%m-%d")
    fundamental = score_longterm_fundamentals(
        earnings, analysis_date
    )
    earnings_score = fundamental["score"]
    total = min(100, chart_score + earnings_score) if earnings_score is not None else None

    if total is None:
        grade, grade_text = "—", "業績資料不足のため採点保留"
    elif total >= 75:
        grade, grade_text = "A", "中長期の保有候補として良好"
    elif total >= 60:
        grade, grade_text = "B", "中長期で検討しやすい"
    elif total >= 45:
        grade, grade_text = "C", "業績・株価の確認を続けたい"
    else:
        grade, grade_text = "D", "現時点では優先度低め"

    return {
        "score": total,
        "grade": grade,
        "close": close,
        "confirmed_close": confirmed_close,
        "data_asof": _snapshot_asof_text(current_snapshot, pd.Timestamp(df.index[-1]).strftime("%Y-%m-%d")),
        "grade_text": grade_text,
        "chart_score": chart_score,
        "earnings_score": earnings_score,
        "fundamental": fundamental,
        "chart_label": chart_label,
        "return120": return120,
        "return240": return240,
        "range_position": range_position,
        "ma50": ma50,
        "ma200": ma200,
        "high252": high252,
        "low252": low252,
        "earnings_adjustment": fundamental,
    }


# =========================================================
# EDINET DB 決算データ
# =========================================================
EDINETDB_BASE_URL = "https://edinetdb.jp/v1"

FORECAST_VALUE_KEYS = (
    "forecast_revenue",
    "forecast_operating_income",
    "forecast_ordinary_income",
    "forecast_net_income",
    "forecast_eps",
)


def edinet_request_json(url, api_key, params=None):
    try:
        response = requests.get(
            url,
            headers={"X-API-Key": api_key},
            params=params,
            timeout=20
        )

        if response.status_code != 200:
            return None, f"HTTP {response.status_code}"

        return response.json(), None

    except Exception as e:
        return None, str(e)


def extract_list_from_data(result):
    if not isinstance(result, dict):
        return []

    data = result.get("data", [])

    if isinstance(data, list):
        return data

    if isinstance(data, dict):
        preferred_keys = [
            "earnings",
            "results",
            "items",
            "records",
            "data",
        ]

        for key in preferred_keys:
            value = data.get(key)
            if isinstance(value, list):
                return value

        for value in data.values():
            if isinstance(value, list):
                return value

    return []


def format_disclosure_date(value):
    if not value:
        return "不明"

    text = str(value)

    try:
        dt = parsedate_to_datetime(text)
        return dt.strftime("%Y/%m/%d")
    except Exception:
        pass

    if len(text) >= 10 and text[4] == "-" and text[7] == "-":
        return text[:10].replace("-", "/")

    return text


def format_financial_number(value):
    if value is None:
        return "不明"

    try:
        number = float(value)

        if number.is_integer():
            number = int(number)

        return f"{number:,}"
    except Exception:
        return str(value)


def calc_progress_rate(actual, forecast):
    try:
        actual = float(actual)
        forecast = float(forecast)

        if forecast == 0:
            return None

        return actual / forecast * 100

    except Exception:
        return None


def forecast_target_fiscal_year_end(row):
    fiscal_year_end = row.get("fiscal_year_end")

    try:
        fiscal_date = pd.Timestamp(fiscal_year_end)
    except Exception:
        return None

    try:
        quarter = int(row.get("quarter"))
    except Exception:
        quarter = None

    note = str(row.get("forecast_period_note") or "")

    if quarter == 4 or "翌期" in note or "翌事業年度" in note:
        fiscal_date = fiscal_date + pd.DateOffset(years=1)

    return fiscal_date.strftime("%Y-%m-%d")


def forecast_numeric_direction(old_value, new_value, tolerance_percent=0.0):
    try:
        if old_value is None or new_value is None:
            return 0

        old_number = float(old_value)
        new_number = float(new_value)
    except Exception:
        return 0

    difference = new_number - old_number

    if difference == 0:
        return 0

    if old_number != 0:
        rate = abs(difference / old_number * 100)
        if rate < tolerance_percent:
            return 0

    return 1 if difference > 0 else -1


def forecast_revision_judgment(old_entry, new_entry):
    core_keys = (
        "forecast_revenue",
        "forecast_operating_income",
        "forecast_ordinary_income",
        "forecast_net_income",
    )

    directions = [
        forecast_numeric_direction(
            old_entry.get(key),
            new_entry.get(key),
        )
        for key in core_keys
    ]

    has_up = 1 in directions
    has_down = -1 in directions

    if has_up and has_down:
        return "混合修正"
    if has_up:
        return "上方修正"
    if has_down:
        return "下方修正"

    eps_direction = forecast_numeric_direction(
        old_entry.get("forecast_eps"),
        new_entry.get("forecast_eps"),
        tolerance_percent=0.5,
    )

    if eps_direction > 0:
        return "EPSのみ上方修正"
    if eps_direction < 0:
        return "EPSのみ下方修正"

    return "予想維持"


def forecast_operating_margin(entry):
    try:
        revenue = float(entry.get("forecast_revenue"))
        operating_income = float(entry.get("forecast_operating_income"))
    except Exception:
        return None

    if revenue == 0:
        return None

    return operating_income / revenue * 100


def forecast_business_summary(first, latest):
    revenue_direction = forecast_numeric_direction(
        first.get("forecast_revenue"),
        latest.get("forecast_revenue"),
    )
    profit_direction = forecast_numeric_direction(
        first.get("forecast_operating_income"),
        latest.get("forecast_operating_income"),
    )

    if revenue_direction > 0 and profit_direction > 0:
        return "増収・増益"
    if revenue_direction > 0 and profit_direction < 0:
        return "増収・減益（採算悪化）"
    if revenue_direction < 0 and profit_direction > 0:
        return "減収・増益（採算改善）"
    if revenue_direction < 0 and profit_direction < 0:
        return "減収・減益"
    if revenue_direction > 0 and profit_direction == 0:
        return "増収・営業利益据え置き"
    if revenue_direction < 0 and profit_direction == 0:
        return "減収・営業利益据え置き"
    if revenue_direction == 0 and profit_direction > 0:
        return "売上据え置き・増益"
    if revenue_direction == 0 and profit_direction < 0:
        return "売上据え置き・減益"

    return "売上・営業利益とも予想維持"


def build_forecast_history_summary(earnings, latest):
    grouped = {}

    for row in earnings:
        if not isinstance(row, dict):
            continue

        if not any(row.get(key) is not None for key in FORECAST_VALUE_KEYS):
            continue

        target_fye = forecast_target_fiscal_year_end(row)
        if not target_fye:
            continue

        disclosure_date = format_disclosure_date(row.get("disclosure_date"))
        entry = {
            "target_fiscal_year_end": target_fye,
            "disclosure_date": disclosure_date,
            "source_fiscal_year_end": row.get("fiscal_year_end"),
            "source_quarter": row.get("quarter"),
        }

        for key in FORECAST_VALUE_KEYS:
            entry[key] = row.get(key)

        grouped.setdefault(target_fye, {})[disclosure_date] = entry

    latest_target_fye = forecast_target_fiscal_year_end(latest)
    target_entries = grouped.get(latest_target_fye, {})
    entries = sorted(
        target_entries.values(),
        key=lambda item: item["disclosure_date"],
    )

    if len(entries) < 2:
        return None

    core_revisions = {"上方修正", "下方修正", "混合修正"}
    real_revision_count = sum(
        forecast_revision_judgment(entries[index - 1], entries[index])
        in core_revisions
        for index in range(1, len(entries))
    )

    first = entries[0]
    current = entries[-1]
    previous = entries[-2]
    first_margin = forecast_operating_margin(first)
    current_margin = forecast_operating_margin(current)

    return {
        "target_fiscal_year_end": latest_target_fye,
        "confirmation_count": len(entries),
        "real_revision_count": real_revision_count,
        "first_disclosure_date": first["disclosure_date"],
        "latest_disclosure_date": current["disclosure_date"],
        "latest_judgment": forecast_revision_judgment(previous, current),
        "overall_judgment": forecast_revision_judgment(first, current),
        "business_summary": forecast_business_summary(first, current),
        "first_operating_margin": first_margin,
        "latest_operating_margin": current_margin,
        "first": first,
        "previous": previous,
        "latest": current,
    }


def _annual_amount(value):
    """financials の年次金額は円。表示用に億円へ変換する。"""
    try:
        number = float(value)
        return number / 100_000_000 if np.isfinite(number) else None
    except (ValueError, TypeError):
        return None


def build_annual_trend(result):
    """年次の同一年度重複を除き、年度・提出日を検証して最大5年表示する。"""
    records = extract_list_from_data(result)
    # API によっては data が直の配列で返る。
    if isinstance(result, list):
        records = result
    by_year = {}
    for record in records:
        if not isinstance(record, dict):
            continue
        raw_year = record.get("fiscal_year")
        try:
            year = int(str(raw_year)[:4])
        except (ValueError, TypeError):
            continue
        if not 2000 <= year <= 2100:
            continue
        submit = str(record.get("submit_date") or "")
        # 連結・単体が途中で混在しないよう、連単混在の行を除外する。
        basis = record.get("basis")
        if basis not in (None, "consolidated", "standalone"):
            continue
        if record.get("basis_source") == "mixed_provenance":
            continue
        if year not in by_year or submit > str(by_year[year].get("submit_date") or ""):
            by_year[year] = record
    selected = [by_year[y] for y in sorted(by_year)[-5:]]
    bases = {r.get("basis") for r in selected if r.get("basis")}
    standards = {r.get("accounting_standard") for r in selected if r.get("accounting_standard")}
    rows = []
    for r in selected:
        rev = _annual_amount(r.get("revenue"))
        op = _annual_amount(r.get("operating_income"))
        rows.append({
            "年度": str(r["fiscal_year"]),
            "売上高（億円）": rev,
            "営業利益（億円）": op,
            "営業利益率（%）": round(op / rev * 100, 1) if rev is not None and rev > 0 and op is not None else None,
            "提出日": str(r.get("submit_date") or "不明")[:10],
        })
    warnings = []
    if len(rows) < 5:
        warnings.append(f"年次決算は{len(rows)}年度分のみ取得できました。欠損年度は補完していません。")
    if len(bases) > 1 or any(r.get("basis") is None for r in selected):
        warnings.append("連結・単体の区分が混在または不明の年度があります。年度比較に注意してください。")
    if len(standards) > 1:
        warnings.append("会計基準が変わった年度があります。単純比較に注意してください。")
    return {"rows": rows, "warnings": warnings}



def analyze_annual_growth(trend):
    """通期実績だけから変化量を計算する。欠損・会計区分不明は推測補完しない。"""
    rows = (trend or {}).get("rows") or []
    clean = []
    for row in rows:
        try:
            year = int(str(row.get("年度"))[:4])
        except (ValueError, TypeError):
            continue
        clean.append((year, row))
    clean.sort(key=lambda item: item[0])
    # 年度の重複があれば曖昧な比較を避ける。
    if len({year for year, _ in clean}) != len(clean):
        return {"lines": [], "notes": ["年度が重複しているため、成長指標は計算していません。"]}
    if len(clean) < 2:
        return {"lines": [], "notes": ["通期実績が2年度以上必要です。"]}

    def value(row, key):
        try:
            v = float(row.get(key))
            return v if np.isfinite(v) else None
        except (ValueError, TypeError):
            return None

    previous_year, previous = clean[-2]
    latest_year, latest = clean[-1]
    lines = []
    notes = []
    if latest_year == previous_year + 1:
        for label, key in (("売上高", "売上高（億円）"), ("営業利益", "営業利益（億円）")):
            old, current = value(previous, key), value(latest, key)
            if old is not None and old > 0 and current is not None:
                lines.append(f"直近の{label}：{previous_year}年度比 {((current / old) - 1) * 100:+.1f}%")
            elif old is not None and old <= 0 and current is not None:
                notes.append(f"{label}の直近増減率は前年値がゼロ以下のため算出していません。")
        old_margin = value(previous, "営業利益率（%）")
        current_margin = value(latest, "営業利益率（%）")
        if old_margin is not None and current_margin is not None:
            lines.append(f"直近の営業利益率：{old_margin:.1f}% → {current_margin:.1f}%（{current_margin - old_margin:+.1f}ポイント）")
    else:
        notes.append("最新2年度が連続していないため、直近の前年比は表示していません。")

    earliest_year, earliest = clean[0]
    intervals = latest_year - earliest_year
    years_contiguous = intervals == len(clean) - 1
    if years_contiguous and intervals > 0:
        old_rev = value(earliest, "売上高（億円）")
        new_rev = value(latest, "売上高（億円）")
        if old_rev is not None and old_rev > 0 and new_rev is not None and new_rev >= 0:
            cagr = ((new_rev / old_rev) ** (1 / intervals) - 1) * 100
            lines.append(f"売上高の年平均増減率：{cagr:+.1f}%（{earliest_year}〜{latest_year}年度、{intervals}年間）")
        else:
            notes.append("売上高の年平均増減率は有効な開始・終了値がないため算出していません。")
    else:
        notes.append("年度に欠けがあるため、売上高の年平均増減率は算出していません。")

    margins = [(year, value(row, "営業利益率（%）")) for year, row in clean]
    margins = [(year, margin) for year, margin in margins if margin is not None]
    if len(margins) >= 2:
        first_year, first_margin = margins[0]
        last_year, last_margin = margins[-1]
        lines.append(f"期間初と最新の営業利益率：{first_margin:.1f}% → {last_margin:.1f}%（{last_margin - first_margin:+.1f}ポイント）")
    notes.append("年次実績の変化を示す参考情報です。中長期37点など既存の採点には反映していません。")
    notes.append("年平均増減率は年度間の複利換算であり、将来の成長率の予測ではありません。")
    return {"lines": lines, "notes": notes}


def fetch_annual_trend(edinet_code, api_key):
    """既存の決算取得と同じ API キー・企業コードで年次実績のみ追加取得。"""
    response, error = edinet_request_json(
        f"{EDINETDB_BASE_URL}/companies/{edinet_code}/financials",
        api_key, params={"years": 5, "period": "annual"},
    )
    if error:
        return {"rows": [], "warnings": [f"過去5年の年次決算を取得できませんでした（{error}）。"]}
    trend = build_annual_trend(response)
    if not trend["rows"]:
        trend["warnings"].append("年次実績が見つかりません。既存の採点は変更しません。")
    return trend


def fetch_earnings_summary(code):
    api_key = os.getenv("EDINETDB_API_KEY")

    if not api_key:
        edinetdb_settings = get_secret_section("edinetdb")
        api_key = edinetdb_settings.get("api_key")

    if not api_key:
        return {
            "ok": False,
            "message": "EDINET DBのAPIキーが見つかりません。"
        }

    search_result, error = edinet_request_json(
        f"{EDINETDB_BASE_URL}/search",
        api_key,
        params={"q": code}
    )

    if error:
        return {
            "ok": False,
            "message": f"企業検索に失敗しました（{error}）。"
        }

    companies = extract_list_from_data(search_result)

    if not companies:
        return {
            "ok": False,
            "message": "EDINET DBで企業を特定できませんでした。"
        }

    exact_matches = [
        company
        for company in companies
        if str(company.get("sec_code", "")).startswith(code)
    ]

    company = exact_matches[0] if exact_matches else companies[0]

    edinet_code = (
        company.get("edinet_code")
        or company.get("code")
    )

    if not edinet_code:
        return {
            "ok": False,
            "message": "EDINETコードを取得できませんでした。"
        }

    earnings_result, error = edinet_request_json(
        f"{EDINETDB_BASE_URL}/companies/{edinet_code}/earnings",
        api_key,
        params={"limit": 8}
    )

    if error:
        return {
            "ok": False,
            "message": f"決算データ取得に失敗しました（{error}）。"
        }

    earnings = extract_list_from_data(earnings_result)

    if not earnings:
        return {
            "ok": False,
            "message": "決算データが見つかりませんでした。"
        }

    # API は新しい順の仕様だが、日付と対象期で再確認してから最新決算を決定する。
    earnings = sorted(
        [item for item in earnings if isinstance(item, dict)],
        key=lambda item: (str(item.get("disclosure_date") or "")[:10],
                          str(item.get("fiscal_year_end") or ""),
                          str(item.get("quarter") or "")),
        reverse=True,
    )
    if not earnings:
        return {"ok": False, "message": "有効な決算データがありません。"}
    latest = earnings[0]
    forecast_history = build_forecast_history_summary(earnings, latest)
    annual_trend = fetch_annual_trend(edinet_code, api_key)
    # 会社予想が対象とする期の直前の通期確定実績だけを比較対象にする。
    # Q4時点で翌期の会社予想が載るケースにも対応する。
    target_fye = forecast_target_fiscal_year_end(latest)
    previous_annual_actual = None
    try:
        prior_fye = (pd.Timestamp(target_fye) - pd.DateOffset(years=1)).strftime("%Y-%m-%d")
        for item in earnings:
            if (str(item.get("fiscal_year_end"))[:10] == prior_fye
                    and str(item.get("quarter")).upper() in ("4", "Q4")):
                previous_annual_actual = {
                    "fiscal_year_end": prior_fye,
                    "revenue": item.get("revenue"),
                    "operating_income": item.get("operating_income"),
                }
                break
    except (TypeError, ValueError):
        pass

    fiscal_year_end = latest.get("fiscal_year_end")
    quarter = latest.get("quarter")
    disclosure_date = format_disclosure_date(latest.get("disclosure_date"))

    revenue = latest.get("revenue")
    operating_income = latest.get("operating_income")
    ordinary_income = latest.get("ordinary_income")
    net_income = latest.get("net_income")

    forecast_revenue = latest.get("forecast_revenue")
    forecast_operating_income = latest.get("forecast_operating_income")
    forecast_ordinary_income = latest.get("forecast_ordinary_income")
    forecast_net_income = latest.get("forecast_net_income")

    revenue_progress = calc_progress_rate(
        revenue,
        forecast_revenue
    )

    operating_progress = calc_progress_rate(
        operating_income,
        forecast_operating_income
    )

    previous_forecast = None

    if forecast_history:
        previous = forecast_history["previous"]
        previous_forecast = {
            "disclosure_date": previous.get("disclosure_date"),
            "forecast_revenue": previous.get("forecast_revenue"),
            "forecast_operating_income": previous.get(
                "forecast_operating_income"
            ),
            "forecast_ordinary_income": previous.get(
                "forecast_ordinary_income"
            ),
            "forecast_net_income": previous.get("forecast_net_income"),
        }

    # 直近決算と同じ四半期の前年データを探す
    year_ago = None

    try:
        latest_year = int(str(fiscal_year_end)[:4])
        latest_quarter = str(quarter).upper()

        for item in earnings[1:]:
            item_fye = item.get("fiscal_year_end")
            item_quarter = str(item.get("quarter")).upper()

            if not item_fye:
                continue

            try:
                item_year = int(str(item_fye)[:4])
            except Exception:
                continue

            if item_year == latest_year - 1 and item_quarter == latest_quarter:
                year_ago_revenue = item.get("revenue")
                year_ago_operating_income = item.get("operating_income")
                year_ago_forecast_revenue = item.get("forecast_revenue")
                year_ago_forecast_operating_income = item.get("forecast_operating_income")

                year_ago = {
                    "fiscal_year_end": item.get("fiscal_year_end"),
                    "quarter": item.get("quarter"),
                    "disclosure_date": format_disclosure_date(
                        item.get("disclosure_date")
                    ),
                    "revenue": year_ago_revenue,
                    "operating_income": year_ago_operating_income,
                    "ordinary_income": item.get("ordinary_income"),
                    "net_income": item.get("net_income"),
                    # 同じ四半期時点の会社予想も保持し、季節性を考慮した
                    # 「前年同期の進捗率」と比較できるようにする。
                    "forecast_revenue": year_ago_forecast_revenue,
                    "forecast_operating_income": year_ago_forecast_operating_income,
                    "revenue_progress": calc_progress_rate(
                        year_ago_revenue,
                        year_ago_forecast_revenue,
                    ),
                    "operating_progress": calc_progress_rate(
                        year_ago_operating_income,
                        year_ago_forecast_operating_income,
                    ),
                }
                break
    except Exception:
        year_ago = None

    return {
        "ok": True,
        "fiscal_year_end": fiscal_year_end,
        "quarter": quarter,
        "disclosure_date": disclosure_date,
        "revenue": revenue,
        "operating_income": operating_income,
        "ordinary_income": ordinary_income,
        "net_income": net_income,
        "revenue_change": latest.get("revenue_change"),
        "operating_income_change": latest.get("operating_income_change"),
        "ordinary_income_change": latest.get("ordinary_income_change"),
        "net_income_change": latest.get("net_income_change"),
        "forecast_revenue": forecast_revenue,
        "forecast_operating_income": forecast_operating_income,
        "forecast_ordinary_income": forecast_ordinary_income,
        "forecast_net_income": forecast_net_income,
        "forecast_revenue_change": latest.get("forecast_revenue_change"),
        "forecast_operating_income_change": latest.get("forecast_operating_income_change"),
        "forecast_ordinary_income_change": latest.get("forecast_ordinary_income_change"),
        "forecast_net_income_change": latest.get("forecast_net_income_change"),
        "revenue_progress": revenue_progress,
        "operating_progress": operating_progress,
        "previous_forecast": previous_forecast,
        "forecast_history": forecast_history,
        "previous_annual_actual": previous_annual_actual,
        "annual_trend": annual_trend,
        "year_ago": year_ago,
    }


def swing_grade(score):
    if score >= 82:
        return "A", "スイング候補としてかなり良好"
    if score >= 68:
        return "B", "スイング候補として十分"
    if score >= 52:
        return "C", "条件を見ながら検討"
    return "D", "現時点では優先度低め"


def calculate_earnings_adjustment(earnings, price_date=None):
    """業績をスイング点へ反映する補正値（最大±12点）を返す。"""
    if not earnings or not earnings.get("ok"):
        return {
            "total": 0,
            "raw_total": 0,
            "forecast": 0,
            "actual": 0,
            "margin": 0,
            "progress": 0,
            "freshness_rate": 1.0,
            "days_since_disclosure": None,
            "reasons": ["決算データを取得できないため補正なし"],
        }

    def number(value):
        try:
            return float(value) if value is not None else None
        except Exception:
            return None

    reasons = []

    # 1. 会社予想の修正（上方 +6 / 維持 0 / 下方 -8）
    forecast_score = 0
    history = earnings.get("forecast_history")
    judgment = history.get("overall_judgment") if history else None

    if judgment == "上方修正":
        forecast_score = 6
        reasons.append("会社予想の上方修正：+6点")
    elif judgment == "下方修正":
        forecast_score = -8
        reasons.append("会社予想の下方修正：-8点")
    elif judgment == "混合修正":
        first = history.get("first", {})
        latest = history.get("latest", {})
        first_op = number(first.get("forecast_operating_income"))
        latest_op = number(latest.get("forecast_operating_income"))
        if first_op is not None and latest_op is not None:
            if latest_op > first_op:
                forecast_score = 4
                reasons.append("会社予想は混合修正・営業利益は上方：+4点")
            elif latest_op < first_op:
                forecast_score = -6
                reasons.append("会社予想は混合修正・営業利益は下方：-6点")
            else:
                reasons.append("会社予想は混合修正・営業利益は維持：0点")
    elif judgment == "予想維持":
        reasons.append("会社予想は維持：0点")
    else:
        reasons.append("会社予想の比較材料不足：0点")

    # 2. 前年同四半期との実績比較（+4 ～ -5）
    actual_score = 0
    year_ago = earnings.get("year_ago")
    current_rev = number(earnings.get("revenue"))
    current_op = number(earnings.get("operating_income"))
    previous_rev = number(year_ago.get("revenue")) if year_ago else None
    previous_op = number(year_ago.get("operating_income")) if year_ago else None

    rev_change = None
    op_change = None
    if current_rev is not None and previous_rev not in (None, 0):
        rev_change = (current_rev - previous_rev) / abs(previous_rev) * 100
    if current_op is not None and previous_op not in (None, 0):
        op_change = (current_op - previous_op) / abs(previous_op) * 100

    if current_op is not None and previous_op is not None:
        if previous_op <= 0 < current_op:
            actual_score = 4
            reasons.append("営業利益が黒字転換：+4点")
        elif previous_op > 0 > current_op:
            actual_score = -5
            reasons.append("営業利益が赤字転落：-5点")
        elif previous_op < 0 and current_op < 0:
            if current_op > previous_op:
                actual_score = 2
                reasons.append("営業赤字が縮小：+2点")
            elif current_op < previous_op:
                actual_score = -3
                reasons.append("営業赤字が拡大：-3点")
        elif op_change is not None:
            if op_change >= 20 and rev_change is not None and rev_change >= 10:
                actual_score = 4
                reasons.append("売上・営業利益とも前年同期比で強い：+4点")
            elif op_change >= 10 or (rev_change is not None and rev_change >= 10):
                actual_score = 2
                reasons.append("前年同期比でやや強い：+2点")
            elif op_change <= -20 and rev_change is not None and rev_change <= -10:
                actual_score = -5
                reasons.append("売上・営業利益とも前年同期比で弱い：-5点")
            elif op_change <= -10 or (rev_change is not None and rev_change <= -10):
                actual_score = -3
                reasons.append("前年同期比でやや弱い：-3点")
            else:
                reasons.append("前年同期比はおおむね横ばい：0点")
    else:
        reasons.append("前年同四半期の比較材料不足：0点")

    # 3. 予想営業利益率の変化（+2 ～ -2）
    margin_score = 0
    if history:
        first_margin = number(history.get("first_operating_margin"))
        latest_margin = number(history.get("latest_operating_margin"))
        if first_margin is not None and latest_margin is not None:
            margin_change = latest_margin - first_margin
            if margin_change >= 2:
                margin_score = 2
                reasons.append("予想営業利益率が2ポイント以上改善：+2点")
            elif margin_change >= 0.5:
                margin_score = 1
                reasons.append("予想営業利益率が改善：+1点")
            elif margin_change <= -2:
                margin_score = -2
                reasons.append("予想営業利益率が2ポイント以上悪化：-2点")
            elif margin_change <= -0.5:
                margin_score = -1
                reasons.append("予想営業利益率が悪化：-1点")
            else:
                reasons.append("予想営業利益率はほぼ変化なし：0点")
        else:
            reasons.append("予想営業利益率の比較材料不足：0点")

    # 4. 前年同四半期と比べた進捗（+2 ～ -2）
    # 単純な25%/50%/75%基準ではなく、同じ会社の前年同四半期と比較することで
    # 季節性や利益計上時期の偏りをある程度吸収する。
    progress_score = 0
    quarter_text = str(earnings.get("quarter") or "").upper().replace("Q", "")

    if quarter_text == "4":
        reasons.append("通期決算のため前年同期進捗の比較は採点外：0点")
    elif year_ago:
        current_op_progress = number(earnings.get("operating_progress"))
        previous_op_progress = number(year_ago.get("operating_progress"))
        current_rev_progress = number(earnings.get("revenue_progress"))
        previous_rev_progress = number(year_ago.get("revenue_progress"))

        # 営業利益の進捗を優先。比較不能な場合のみ売上高を補助に使う。
        if current_op_progress is not None and previous_op_progress is not None:
            progress_gap = current_op_progress - previous_op_progress
            if progress_gap >= 10:
                progress_score = 2
                reasons.append(
                    f"営業利益の進捗が前年同期を{progress_gap:+.1f}ポイント上回る：+2点"
                )
            elif progress_gap >= 5:
                progress_score = 1
                reasons.append(
                    f"営業利益の進捗が前年同期を{progress_gap:+.1f}ポイント上回る：+1点"
                )
            elif progress_gap <= -10:
                progress_score = -2
                reasons.append(
                    f"営業利益の進捗が前年同期を{progress_gap:+.1f}ポイント下回る：-2点"
                )
            elif progress_gap <= -5:
                progress_score = -1
                reasons.append(
                    f"営業利益の進捗が前年同期を{progress_gap:+.1f}ポイント下回る：-1点"
                )
            else:
                reasons.append(
                    f"営業利益の進捗は前年同期比{progress_gap:+.1f}ポイントで大差なし：0点"
                )
        elif current_rev_progress is not None and previous_rev_progress is not None:
            progress_gap = current_rev_progress - previous_rev_progress
            if progress_gap >= 10:
                progress_score = 1
                reasons.append(
                    f"売上高の進捗が前年同期を{progress_gap:+.1f}ポイント上回る：+1点"
                )
            elif progress_gap <= -10:
                progress_score = -1
                reasons.append(
                    f"売上高の進捗が前年同期を{progress_gap:+.1f}ポイント下回る：-1点"
                )
            else:
                reasons.append(
                    f"売上高の進捗は前年同期比{progress_gap:+.1f}ポイントで大差なし：0点"
                )
        else:
            reasons.append("前年同期の進捗比較材料不足：0点")
    else:
        reasons.append("前年同期の進捗比較材料不足：0点")

    raw_total = max(
        -12,
        min(12, forecast_score + actual_score + margin_score + progress_score),
    )

    # 決算開示から時間が経つほど、スイング判断への影響を弱める。
    # パソコンの現在日ではなく、分析対象の最新株価日を基準にする。
    freshness_rate = 1.0
    days_since_disclosure = None

    try:
        disclosure = pd.Timestamp(earnings.get("disclosure_date")).normalize()
        market_date = pd.Timestamp(price_date).normalize()
        days_since_disclosure = int((market_date - disclosure).days)

        if days_since_disclosure < 0:
            freshness_rate = 0.0
            reasons.append("株価データ日より後の決算のため補正対象外")
        elif days_since_disclosure <= 14:
            freshness_rate = 1.0
        elif days_since_disclosure <= 30:
            freshness_rate = 0.9
        elif days_since_disclosure <= 60:
            freshness_rate = 0.75
        else:
            # これが取得できる最新の公式決算である限り、
            # 時間経過だけで影響を消しすぎない。
            # 次の決算が出るまでは最低60%を残す。
            freshness_rate = 0.6
    except Exception:
        # 日付を比較できない場合は、従来どおり元の補正を使用する。
        freshness_rate = 1.0
        days_since_disclosure = None

    adjusted_value = raw_total * freshness_rate
    if adjusted_value >= 0:
        total = int(adjusted_value + 0.5)
    else:
        total = -int(abs(adjusted_value) + 0.5)

    return {
        "total": total,
        "raw_total": raw_total,
        "forecast": forecast_score,
        "actual": actual_score,
        "margin": margin_score,
        "progress": progress_score,
        "freshness_rate": freshness_rate,
        "days_since_disclosure": days_since_disclosure,
        "reasons": reasons,
    }


def show_earnings_summary(earnings, price_date=None, show_swing_effect=True):
    st.markdown("### 🧾 業績・決算")

    if not earnings or not earnings.get("ok"):
        message = (
            earnings.get("message")
            if isinstance(earnings, dict)
            else "決算データを取得できませんでした。"
        )
        st.warning(message)
        return

    fiscal_year_end = earnings.get("fiscal_year_end", "不明")
    quarter = earnings.get("quarter")

    try:
        fy_text = pd.Timestamp(fiscal_year_end).strftime("%Y年%m月期")
    except Exception:
        fy_text = str(fiscal_year_end)

    quarter_text = f"{quarter}Q" if quarter is not None else "不明"

    st.write(
        f"**直近決算**　{fy_text} {quarter_text}　"
        f"｜　開示日 {earnings['disclosure_date']}"
    )

    c1, c2 = st.columns(2)

    with c1:
        st.markdown("**実績（百万円）**")
        st.write(
            f"売上高：{format_financial_number(earnings['revenue'])}"
        )
        st.write(
            f"営業利益：{format_financial_number(earnings['operating_income'])}"
        )
        st.write(
            f"経常利益：{format_financial_number(earnings['ordinary_income'])}"
        )
        st.write(
            f"純利益：{format_financial_number(earnings['net_income'])}"
        )

    with c2:
        st.markdown("**通期会社予想（百万円）**")
        st.write(
            f"売上高：{format_financial_number(earnings['forecast_revenue'])}"
        )
        st.write(
            f"営業利益：{format_financial_number(earnings['forecast_operating_income'])}"
        )
        st.write(
            f"経常利益：{format_financial_number(earnings['forecast_ordinary_income'])}"
        )
        st.write(
            f"純利益：{format_financial_number(earnings['forecast_net_income'])}"
        )

    st.markdown("**単純進捗率**")

    revenue_progress = earnings.get("revenue_progress")
    operating_progress = earnings.get("operating_progress")

    if revenue_progress is None:
        st.write("売上高：算出不可")
    else:
        st.write(f"売上高：{revenue_progress:.1f}%")

    if operating_progress is None:
        st.write("営業利益：算出不可")
    else:
        st.write(f"営業利益：{operating_progress:.1f}%")

    # 単純進捗率だけでは季節性を誤判定しやすいため、
    # 同じ四半期の前年進捗と並べて確認する。
    year_ago = earnings.get("year_ago")
    if year_ago:
        prior_rev_progress = year_ago.get("revenue_progress")
        prior_op_progress = year_ago.get("operating_progress")
        if any(value is not None for value in (prior_rev_progress, prior_op_progress)):
            st.markdown("**前年同期と比べた進捗**")
            if revenue_progress is not None and prior_rev_progress is not None:
                st.write(
                    f"売上高：前年 {prior_rev_progress:.1f}% → 今期 {revenue_progress:.1f}%"
                    f"（{revenue_progress - prior_rev_progress:+.1f}ポイント）"
                )
            else:
                st.write("売上高：比較不可")
            if operating_progress is not None and prior_op_progress is not None:
                st.write(
                    f"営業利益：前年 {prior_op_progress:.1f}% → 今期 {operating_progress:.1f}%"
                    f"（{operating_progress - prior_op_progress:+.1f}ポイント）"
                )
            else:
                st.write("営業利益：比較不可")
            st.caption("同じ四半期同士で比較し、会社ごとの季節性をできるだけ考慮します。")

    # 前年同四半期との実額比較
    st.markdown("**前年同四半期との比較**")

    year_ago = earnings.get("year_ago")

    def to_number(value):
        try:
            if value is None:
                return None
            return float(value)
        except Exception:
            return None

    def yoy_label(current, previous_value):
        current_num = to_number(current)
        previous_num = to_number(previous_value)

        if current_num is None or previous_num is None:
            return "比較不可"

        if previous_num > 0 and current_num < 0:
            return "赤字転落"

        if previous_num < 0 and current_num > 0:
            return "黒字転換"

        if previous_num < 0 and current_num < 0:
            if current_num > previous_num:
                return "赤字縮小"
            if current_num < previous_num:
                return "赤字拡大"
            return "赤字横ばい"

        if previous_num == 0:
            if current_num > 0:
                return "黒字化"
            if current_num < 0:
                return "赤字化"
            return "横ばい"

        change = (current_num - previous_num) / abs(previous_num) * 100
        return f"{change:+.1f}%"

    if year_ago:
        for label, current, previous_value in [
            ("売上高", earnings.get("revenue"), year_ago.get("revenue")),
            ("営業利益", earnings.get("operating_income"), year_ago.get("operating_income")),
            ("経常利益", earnings.get("ordinary_income"), year_ago.get("ordinary_income")),
            ("純利益", earnings.get("net_income"), year_ago.get("net_income")),
        ]:
            st.write(
                f"{label}：{format_financial_number(previous_value)} → "
                f"{format_financial_number(current)}"
                f"（{yoy_label(current, previous_value)}）"
            )
    else:
        st.write("前年同四半期データを取得できませんでした。")

    rev_change = None
    op_change = None

    if year_ago:
        current_rev = to_number(earnings.get("revenue"))
        previous_rev = to_number(year_ago.get("revenue"))
        current_op = to_number(earnings.get("operating_income"))
        previous_op = to_number(year_ago.get("operating_income"))

        if current_rev is not None and previous_rev not in (None, 0):
            rev_change = (
                (current_rev - previous_rev) / abs(previous_rev) * 100
            )

        if current_op is not None and previous_op not in (None, 0):
            op_change = (
                (current_op - previous_op) / abs(previous_op) * 100
            )

    previous = earnings.get("previous_forecast")
    forecast_history = earnings.get("forecast_history")

    st.markdown("**会社予想の流れ**")

    if forecast_history:
        try:
            target_text = pd.Timestamp(
                forecast_history["target_fiscal_year_end"]
            ).strftime("%Y年%m月期")
        except Exception:
            target_text = str(
                forecast_history.get("target_fiscal_year_end", "不明")
            )

        st.write(
            f"{target_text}｜{forecast_history['overall_judgment']}｜"
            f"{forecast_history['business_summary']}"
        )
        st.write(
            f"予想確認 {forecast_history['confirmation_count']}回　｜　"
            f"実質修正 {forecast_history['real_revision_count']}回　｜　"
            f"直近判定 {forecast_history['latest_judgment']}"
        )

        first_margin = forecast_history.get("first_operating_margin")
        latest_margin = forecast_history.get("latest_operating_margin")

        if first_margin is not None and latest_margin is not None:
            margin_change = latest_margin - first_margin
            st.write(
                f"予想営業利益率：{first_margin:.2f}% → "
                f"{latest_margin:.2f}%（{margin_change:+.2f}ポイント）"
            )

        with st.expander("会社予想の履歴を見る"):
            first_forecast = forecast_history["first"]
            latest_forecast = forecast_history["latest"]
            st.caption(
                f"初回 {forecast_history['first_disclosure_date']} → "
                f"最新 {forecast_history['latest_disclosure_date']}"
            )
            st.write(
                "売上高："
                f"{format_financial_number(first_forecast.get('forecast_revenue'))} → "
                f"{format_financial_number(latest_forecast.get('forecast_revenue'))}"
            )
            st.write(
                "営業利益："
                f"{format_financial_number(first_forecast.get('forecast_operating_income'))} → "
                f"{format_financial_number(latest_forecast.get('forecast_operating_income'))}"
            )
            st.write(
                "経常利益："
                f"{format_financial_number(first_forecast.get('forecast_ordinary_income'))} → "
                f"{format_financial_number(latest_forecast.get('forecast_ordinary_income'))}"
            )
            st.write(
                "純利益："
                f"{format_financial_number(first_forecast.get('forecast_net_income'))} → "
                f"{format_financial_number(latest_forecast.get('forecast_net_income'))}"
            )
    else:
        st.write("同じ対象年度の会社予想が2回以上なく、履歴比較はできません。")

    def compare_forecast(current, previous_value):
        try:
            current = float(current)
            previous_value = float(previous_value)
        except Exception:
            return "比較不可", None

        if previous_value == 0:
            if current == 0:
                return "据え置き", 0.0
            return "変更", None

        change_pct = (current - previous_value) / abs(previous_value) * 100

        if abs(change_pct) < 0.01:
            return "据え置き", 0.0
        elif change_pct > 0:
            return "上方修正", change_pct
        else:
            return "下方修正", change_pct

    revenue_revision = ("比較不可", None)
    op_revision = ("比較不可", None)

    if previous:
        revenue_revision = compare_forecast(
            earnings.get("forecast_revenue"),
            previous.get("forecast_revenue")
        )
        op_revision = compare_forecast(
            earnings.get("forecast_operating_income"),
            previous.get("forecast_operating_income")
        )

    st.markdown("**直近開示での会社予想の変化**")

    if previous:
        rev_label, rev_pct = revenue_revision
        op_label, op_pct = op_revision

        rev_suffix = (
            f"（{rev_pct:+.1f}%）"
            if rev_pct is not None and rev_label != "据え置き"
            else ""
        )
        op_suffix = (
            f"（{op_pct:+.1f}%）"
            if op_pct is not None and op_label != "据え置き"
            else ""
        )

        st.write(f"売上高：{rev_label}{rev_suffix}")
        st.write(f"営業利益：{op_label}{op_suffix}")
    else:
        st.write("前回予想がないため比較不可")

    # 簡易評価
    score = 0
    reasons = []

    try:
        if rev_change is not None:
            rev_change_f = float(rev_change)
            if rev_change_f >= 10:
                score += 1
                reasons.append("売上高は前年同期比で増加")
            elif rev_change_f <= -10:
                score -= 1
                reasons.append("売上高は前年同期比で減少")
    except Exception:
        pass

    # 営業利益の前年比が取得できない場合でも、
    # 現在値が赤字かどうかは評価材料にする
    operating_income = earnings.get("operating_income")

    try:
        current_op = to_number(operating_income)
        previous_op = (
            to_number(year_ago.get("operating_income"))
            if year_ago
            else None
        )

        if current_op is not None and previous_op is not None:
            if previous_op > 0 and current_op < 0:
                score -= 2
                reasons.append("営業利益は前年同期の黒字から赤字転落")
            elif previous_op < 0 and current_op > 0:
                score += 2
                reasons.append("営業利益は前年同期の赤字から黒字転換")
            elif previous_op < 0 and current_op < 0:
                if current_op > previous_op:
                    score += 1
                    reasons.append("営業赤字は前年同期より縮小")
                elif current_op < previous_op:
                    score -= 1
                    reasons.append("営業赤字は前年同期より拡大")
            elif op_change is not None:
                if op_change >= 10:
                    score += 2
                    reasons.append("営業利益は前年同期比で増加")
                elif op_change <= -10:
                    score -= 2
                    reasons.append("営業利益は前年同期比で減少")
        elif current_op is not None and current_op < 0:
            score -= 1
            reasons.append("営業利益は赤字")
    except Exception:
        pass

    rev_label, _ = revenue_revision
    op_label, _ = op_revision

    if rev_label == "上方修正":
        score += 1
        reasons.append("売上高会社予想を上方修正")
    elif rev_label == "下方修正":
        score -= 1
        reasons.append("売上高会社予想を下方修正")
    elif rev_label == "据え置き":
        reasons.append("売上高会社予想は据え置き")

    if op_label == "上方修正":
        score += 2
        reasons.append("営業利益会社予想を上方修正")
    elif op_label == "下方修正":
        score -= 2
        reasons.append("営業利益会社予想を下方修正")
    elif op_label == "据え置き":
        reasons.append("営業利益会社予想は据え置き")

    # 画面上の評価も、総合点に使う業績補正と同じ基準へ統一する
    adjustment = calculate_earnings_adjustment(earnings, price_date)
    score = adjustment["total"]
    reasons = adjustment["reasons"]

    if score >= 8:
        earnings_view = "強め"
        swing_effect = "追い風"
    elif score >= 3:
        earnings_view = "やや強め"
        swing_effect = "やや追い風"
    elif score <= -5:
        earnings_view = "弱め"
        swing_effect = "逆風"
    elif score <= -1:
        earnings_view = "やや弱め"
        swing_effect = "やや逆風"
    else:
        earnings_view = "中立"
        swing_effect = "中立"

    st.markdown("**決算の簡易評価**")
    st.write(f"業績評価：{earnings_view}")
    if show_swing_effect:
        st.write(f"スイングへの影響：{swing_effect}")
    freshness_pct = adjustment["freshness_rate"] * 100
    days = adjustment["days_since_disclosure"]
    if days is None:
        st.write(f"業績補正：{score:+d}点")
    else:
        st.write(
            f"業績補正：{score:+d}点"
            f"（元 {adjustment['raw_total']:+d}点 × 鮮度 {freshness_pct:.0f}%）"
        )
        st.write(f"情報の経過日数：開示から {days}日")

    if reasons:
        with st.expander("決算評価の理由"):
            for reason in reasons:
                st.write(f"・{reason}")
    else:
        st.caption("評価材料が少ないため、中立評価です。")

    if previous:
        current_rev = earnings.get("forecast_revenue")
        previous_rev = previous.get("forecast_revenue")
        current_op = earnings.get("forecast_operating_income")
        previous_op = previous.get("forecast_operating_income")

        if (
            current_rev is not None
            and previous_rev is not None
            and current_op is not None
            and previous_op is not None
        ):
            st.markdown("**前回開示時の会社予想との比較**")
            st.caption(
                f"比較対象の開示日：{previous['disclosure_date']}"
            )
            st.write(
                "売上高："
                f"{format_financial_number(previous_rev)} → "
                f"{format_financial_number(current_rev)}"
            )
            st.write(
                "営業利益："
                f"{format_financial_number(previous_op)} → "
                f"{format_financial_number(current_op)}"
            )

    st.caption(
        "決算評価は、前年同四半期の実績・会社予想の修正・予想利益率に加え、"
        "同じ四半期の前年進捗との比較を使う簡易判定です。"
        "前年同期比較で季節性を一部考慮しますが、特殊要因や会社固有の利益計上時期を完全には補正できません。"
        "総合スイング適性には、業績補正として最大±12点を反映します。"
    )


# =========================================================
# 表示
# =========================================================
def show_stock_outlook(result):
    st.subheader("📈 今日の株価展望")

    if result.get("error"):
        st.warning(result["error"])
        if result.get("detail"):
            with st.expander("株価展望のエラー詳細"):
                st.code(result["detail"])
        return

    # スマホの縦画面でも文言が切れないよう、3列ではなく縦に表示する。
    st.metric("方向性", result["direction"])
    st.metric("値動き", result["volatility"])
    st.metric("信頼度", result["confidence"])

    zones = result["zones"]
    up_prices = [
        compact_outlook_price(zones["up"][key])
        for key in ("first", "middle", "main", "major")
        if zones["up"][key] != "-"
    ]
    down_prices = [
        compact_outlook_price(zones["down"][key])
        for key in ("first", "middle", "main", "major")
        if zones["down"][key] != "-"
    ]

    st.markdown("**今日の重要価格**")
    st.write(f'↑ 上方向　{" → ".join(up_prices) if up_prices else "取得できませんでした"}')
    st.write(f'↓ 下方向　{" → ".join(down_prices) if down_prices else "取得できませんでした"}')

    market = result["market"]
    st.markdown("**地合い**")
    st.write(f'総合：{market["total"]}')
    with st.expander("地合いの内訳"):
        st.write(f'日本株：{market["japan"]}')
        st.write(f'米国ハイテク：{market["us_tech"]}')
        st.write(f'半導体：{market["semiconductor"]}')
        st.write(f'アジア：{market["asia"]}')
        st.write(f'為替：{market["fx"]}')

    if result.get("warning"):
        st.caption(result["warning"])

    with st.expander("株価展望の詳しい分析を見る"):
        st.text(result["detail"])


def _latest_market_detail_text(detail, brief):
    """stock_aiの古い日足地合い表示を、朝の最新スナップショット表示へ差し替える。"""
    if not detail:
        return detail

    lines = detail.splitlines()
    start = None
    end = None
    for i, line in enumerate(lines):
        if "今日の地合い" in line:
            start = i
            continue
        if start is not None and i > start and "類似局面の統計" in line:
            end = i
            break

    if start is None:
        return detail
    if end is None:
        end = len(lines)

    latest_lines = [
        "======================================",
        "           今日の地合い（最新）",
        "======================================",
        f"市場全体         : {brief.get('overall', '判定保留')}",
        f"日経先物         : {_fmt_pct((brief.get('futures') or {}).get('change'))}",
        f"日経平均         : {_fmt_pct((brief.get('nikkei') or {}).get('change'))}",
        f"TOPIX            : {_fmt_pct((brief.get('topix') or {}).get('change'))}",
        f"NASDAQ           : {_fmt_pct((brief.get('nasdaq') or {}).get('change'))}",
        f"SOX              : {_fmt_pct((brief.get('sox') or {}).get('change'))}",
        f"KOSPI            : {_fmt_pct((brief.get('kospi') or {}).get('change'))}",
        f"USD/JPY          : {_fmt_pct((brief.get('usd_jpy') or {}).get('change'))}",
        f"為替環境         : {brief.get('fx', '取得不可')}",
        f"半導体地合い     : {brief.get('semiconductor', '判定保留')}",
        "",
    ]
    return "\n".join(lines[:start] + latest_lines + lines[end:])


def show_daytrade(result, stock_outlook=None):
    st.subheader("☀️ 今日のデイトレ展望")
    st.caption(f'現在値 {result.get("close", 0):.1f} 円　｜　データ時点 {result.get("data_asof", "-")}')

    # 「今日の株価展望」で使っていた情報は削除せず、
    # デイトレ展望の中へ整理統合して表示する。
    if stock_outlook:
        if stock_outlook.get("error"):
            st.warning(stock_outlook["error"])
            if stock_outlook.get("detail"):
                with st.expander("展望データのエラー詳細"):
                    st.code(stock_outlook["detail"])
        else:
            st.markdown("**全体の方向性**")
            c1, c2, c3 = st.columns(3)
            with c1:
                st.caption("方向性")
                st.write(stock_outlook.get("direction", "-"))
            with c2:
                st.caption("値動き")
                st.write(stock_outlook.get("volatility", "-"))
            with c3:
                st.caption("信頼度")
                st.write(stock_outlook.get("confidence", "-"))

            zones = stock_outlook.get("zones") or {}
            up = zones.get("up") or {}
            down = zones.get("down") or {}
            up_prices = [
                compact_outlook_price(up.get(key, "-"))
                for key in ("first", "middle", "main", "major")
                if up.get(key, "-") != "-"
            ]
            down_prices = [
                compact_outlook_price(down.get(key, "-"))
                for key in ("first", "middle", "main", "major")
                if down.get(key, "-") != "-"
            ]

            st.markdown("**重要価格**")
            st.write(
                f'↑ 上方向　{" → ".join(up_prices) if up_prices else "取得できませんでした"}'
            )
            st.write(
                f'↓ 下方向　{" → ".join(down_prices) if down_prices else "取得できませんでした"}'
            )

            market = stock_outlook.get("market") or {}
            # 地合いは朝の展望と同じ最新スナップショットを優先する。
            latest_brief = get_morning_market_brief()
            st.markdown("**個別銘柄の地合い**")
            st.write(f'総合：{latest_brief.get("overall", market.get("total", "-"))}')
            with st.expander("地合いの内訳"):
                st.write(_fmt_market_line("日経先物", latest_brief.get("futures")))
                st.write(_fmt_market_line("日経平均", latest_brief.get("nikkei")))
                topix_item = latest_brief.get("topix")
                topix_label = "TOPIX参考(1306 ETF)" if topix_item and topix_item.get("proxy") else "TOPIX"
                st.write(_fmt_market_line(topix_label, topix_item))
                st.write(_fmt_market_line("NASDAQ", latest_brief.get("nasdaq")))
                st.write(_fmt_market_line("SOX", latest_brief.get("sox")))
                st.write(_fmt_market_line("KOSPI", latest_brief.get("kospi")))
                st.write(_fmt_market_line("USD/JPY", latest_brief.get("usd_jpy")))

            if stock_outlook.get("warning"):
                st.caption(stock_outlook["warning"])

            with st.expander("類似局面・展望の詳しい分析を見る"):
                latest_detail = _latest_market_detail_text(
                    stock_outlook.get("detail", "詳細データはありません。"),
                    latest_brief,
                )
                st.text(latest_detail)

            st.divider()

    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**基本目線**")
        st.write(result["outlook"])
    with c2:
        st.markdown("**信頼度**")
        st.write(result["outlook_confidence"])

    st.write(result["outlook_detail"])

    st.markdown("**朝に見る分岐ライン**")
    st.write(
        f'上の分岐：前日高値 {result["prev_high"]:.1f} 円　｜　'
        f'下の分岐：前日安値 {result["prev_low"]:.1f} 円'
    )
    st.write(
        f'目安の上値：{result["up_target"]:.1f} 円　｜　'
        f'目安の下値：{result["down_target"]:.1f} 円　'
        f'（ATR14：{result["atr14"]:.1f} 円）'
    )
    st.caption(result["invalidation"])

    st.divider()
    st.subheader("⚡ デイトレ適性")

    c1, c2 = st.columns(2)

    with c1:
        st.metric(
            "デイトレ適性",
            result["grade"],
            f'{result["score"]} / 100'
        )

    with c2:
        st.markdown("**判定**")
        st.write(result["grade_text"])

    st.write(f'**20日平均売買代金**　{result["turnover_oku"]:.1f} 億円')
    st.write(f'**ATR**　{result["atr_pct"]:.2f}%')
    st.write(f'**出来高倍率**　{result["volume_ratio"]:.2f} 倍')
    st.write(f'**短期トレンド**　{result["trend"]}')
    st.write(f'**現在位置**　{result["position"]}')

    with st.expander("デイトレ分析のポイント"):
        for comment in result["comments"]:
            st.write(f"・{comment}")

    st.caption(
        "重要価格・類似局面・地合いは、この「今日のデイトレ展望」に統合して表示しています。"
    )



def analyze_profit_change(trend, code=None):
    """通期実績で前年差を確認。一次資料の要因は検証済み銘柄・決算期に限る。"""
    rows = (trend or {}).get("rows") or []
    by_year = {}
    for row in rows:
        try:
            year = int(str(row.get("年度", ""))[:4])
            sales = float(row["売上高（億円）"])
            profit = float(row["営業利益（億円）"])
        except (TypeError, ValueError, KeyError):
            continue
        if not (np.isfinite(sales) and np.isfinite(profit)) or year in by_year:
            return {"summary": "年度や実績に不備があるため、利益変化を分析できません。", "factors": []}
        by_year[year] = (sales, profit)
    if len(by_year) < 2:
        return {"summary": "連続する2年度の通期実績が必要です。", "factors": []}
    latest_year = max(by_year)
    if latest_year - 1 not in by_year:
        return {"summary": "直近2年度が連続していないため、利益変化を分析できません。", "factors": []}
    previous_sales, previous_profit = by_year[latest_year - 1]
    latest_sales, latest_profit = by_year[latest_year]
    sales_diff = latest_sales - previous_sales
    profit_diff = latest_profit - previous_profit
    direction = "増加" if profit_diff > 0 else "減少" if profit_diff < 0 else "横ばい"
    summary = (f"{latest_year}年3月期：売上高は前年比 {sales_diff:+.1f}億円、"
               f"営業利益は {profit_diff:+.1f}億円（{direction}）。")
    output = {"summary": summary, "factors": [], "source": None, "note":
              "金額の増減だけから減益の原因は断定できません。要因別の内訳は一次資料を照合した年度だけ表示します。"}
    # 固定の公式資料を別年度や別銘柄へ流用しない。億円単位、表示丸め差を許容。
    if (str(code) == "6526" and latest_year == 2026
            and abs(previous_profit - 250.0) < 0.3
            and abs(latest_profit - 123.54) < 0.3
            and abs(previous_sales - 1885.35) < 0.3
            and abs(latest_sales - 2008.34) < 0.3):
        output["factors"] = [
            ("製品粗利益", -120.0),
            ("NRE売上", -27.0),
            ("研究開発費・販管費など", 12.0),
            ("為替影響", 9.0),
        ]
        output["source"] = "https://www.socionext.com/en/ir/pdf/sn_ir20260428_04e.pdf"
        output["note"] = ("会社の2026年4月28日決算説明会資料（9ページ）にある前年差の要因分解。"
                          "各項目は概数で、費用等のプラスは利益へのプラス寄与。"
                          "合計は約-126億円で、実績の前年差との差は丸めによります。"
                          "一時的・恒常的かの判断や将来の回復を保証するものではありません。")
    return output

def show_longterm(result, earnings=None, code=None):
    """中長期分析：重要事項を先に示し、採点根拠は必要時だけ展開する。"""
    st.subheader("🏢 中長期分析")
    st.caption(f'現在値 {result.get("close", 0):.1f} 円　｜　株価データ時点 {result.get("data_asof", "-")}')
    fundamental = result["fundamental"]
    total_label = (f'{result["score"]} / 100' if result["score"] is not None else "採点保留")
    # st.metric の変動値（delta）は矢印SVGを伴うため、評価点はテキストで明示する。
    # ランク／得点をひと続きに表示し、コピー時の不要な「svg」混入も避ける。
    st.markdown(f'**中長期適性：{result["grade"]}　｜　{total_label}**')
    st.write(f'**判定**　{result["grade_text"]}')

    earnings_label = (f'{result["earnings_score"]} / 70点'
                      if result["earnings_score"] is not None else "採点保留")
    st.write(f'**業績・会社予想**　{earnings_label}　｜　'
             f'**長期チャート**　{result["chart_score"]} / 30点')

    # 冒頭は重要な事実だけ。数値は採点時と同じデータから抽出し、再計算しない。
    components = fundamental.get("components", [])
    for item in components:
        if item["label"] in ("前年同期の実績", "通期会社予想と前期実績"):
            st.write(f'**{item["label"]}**　{item["explanation"]}')

    # 残り期間に必要な利益は採点外だが、会社予想を読むうえで重要なので先頭に表示。
    notes = fundamental.get("notes", [])
    required_note = next((note for note in notes
                          if note.startswith("通期予想達成に残り期間で必要な営業利益：")), None)
    comparison_note = next((note for note in notes
                            if note.startswith("残り期間に必要な営業利益の前年同期比：")), None)
    if required_note:
        st.info(f"**会社予想の確認ポイント**\n\n{required_note}"
                + (f"\n\n{comparison_note}" if comparison_note else ""))
    st.write(f'**長期チャート**　{result["chart_label"]}　｜　'
             f'約1年騰落率 {result["return240"]:+.1f}%')

    if fundamental["provisional"] and fundamental["score"] is not None:
        st.warning(f'業績の一部資料が不足しています（採点可能 {fundamental["coverage"]}/70点分）。'
                   '表示点は暫定値です。')
    elif fundamental["score"] is None:
        st.warning("業績資料不足のため、総合点・ランクは表示していません。")

    with st.expander("採点の根拠・業績データを詳しく見る"):
        # 5年推移は参考情報。スイングにも渡す共通 earnings から参照し、採点式を変更しない。
        trend = (earnings or {}).get("annual_trend") or {}
        st.markdown("**過去5年間の通期業績（実績）**")
        if trend.get("rows"):
            st.dataframe(pd.DataFrame(trend["rows"]), hide_index=True, use_container_width=True)
            st.caption("金額は億円。通期の確定実績のみで、会社予想や四半期累計は含みません。")
            growth = analyze_annual_growth(trend)
            st.markdown("**長期の売上・利益・収益性の変化（採点対象外）**")
            for line in growth["lines"]:
                st.write("・" + line)
            for note in growth["notes"]:
                st.caption("・" + note)
            st.markdown("**営業利益の変動要因（採点対象外）**")
            profit_change = analyze_profit_change(trend, code=code)
            st.write(profit_change["summary"])
            if profit_change["factors"]:
                st.dataframe(pd.DataFrame([
                    {"会社資料の要因": name, "営業利益への寄与（億円）": amount}
                    for name, amount in profit_change["factors"]
                ]), hide_index=True, use_container_width=True)
            st.caption(profit_change["note"])
            if profit_change["source"]:
                st.markdown(f'[根拠：会社公式の決算説明会資料（PDF）]({profit_change["source"]})')
        else:
            st.caption("過去5年間の通期実績は取得できませんでした。")
        for warning in trend.get("warnings", []):
            st.caption("注意：" + warning)
        st.markdown("**業績点の計算内訳**")
        if components:
            for item in components:
                st.write(f'{item["label"]}：{item["score"]} / {item["max"]}点 — {item["explanation"]}')
            raw_points = sum(item["score"] for item in components)
            coverage = fundamental["coverage"]
            if fundamental["score"] is not None:
                if coverage < 70:
                    st.caption(f'採点可能な項目 {raw_points}/{coverage}点を70点満点に換算 → '
                               f'業績 {fundamental["score"]}点（暫定）')
                else:
                    st.caption(f'4項目合計 {raw_points}/70点 → 業績 {fundamental["score"]}点')
        else:
            st.caption("採点できる業績データがありません。")

        # 冒頭に表示した必要利益・必要増益率は詳細欄では繰り返さない。
        supplemental_notes = [
            note for note in notes
            if not note.startswith((
                "通期予想達成に残り期間で必要な営業利益：",
                "残り期間に必要な営業利益の前年同期比：",
            ))
            and note != "会社予想は未達の可能性があり、実績とは区別して表示しています。"
        ]
        if supplemental_notes:
            st.markdown("**会社予想・進捗の補足**")
            for note in supplemental_notes:
                st.caption(f"・{note}")

        st.markdown("**長期チャートの数値**")
        st.write(f'6か月騰落率 {result["return120"]:+.1f}%　｜　'
                 f'約1年騰落率 {result["return240"]:+.1f}%')

        # 古いセッション結果やデータ欠損でも nan を表示しない。
        def longterm_display(value, suffix="", digits=1):
            try:
                number = float(value)
                return f"{number:.{digits}f}{suffix}" if np.isfinite(number) else "データ不足"
            except (TypeError, ValueError):
                return "データ不足"

        position = result.get("range_position")
        position_text = (longterm_display(float(position) * 100, "%", 0)
                         if position is not None else "データ不足")
        st.write(f'約1年の位置 {position_text}　｜　'
                 f'高値 {longterm_display(result.get("high252"), " 円")}　｜　'
                 f'安値 {longterm_display(result.get("low252"), " 円")}')
        if position_text == "データ不足":
            st.caption("年間高値・安値の有効データが不足しているため、年間位置は採点対象外です。")
        st.write(f'移動平均線：50日線 {longterm_display(result.get("ma50"))}　｜　'
                 f'200日線 {longterm_display(result.get("ma200"))}')

    st.caption("中長期専用の参考指標（業績70点・チャート30点）。会社予想は実績と区別し、"
               "必要利益の比較は採点や達成確率に使用しません。")



def _parse_news_date(value):
    if value is None:
        return None
    try:
        if isinstance(value, (int, float)):
            return pd.Timestamp(value, unit="s", tz="UTC").tz_convert("Asia/Tokyo")
        dt = parsedate_to_datetime(str(value))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=ZoneInfo("UTC"))
        return pd.Timestamp(dt).tz_convert("Asia/Tokyo")
    except Exception:
        try:
            ts = pd.Timestamp(value)
            if ts.tzinfo is None:
                ts = ts.tz_localize("UTC")
            return ts.tz_convert("Asia/Tokyo")
        except Exception:
            return None


def _clean_news_company_name(company):
    name = str(company or "").strip()
    for token in ("株式会社", "（株）", "(株)", "Co., Ltd.", "Ltd."):
        name = name.replace(token, "")
    return name.strip()


def _assess_stock_news(title, summary=""):
    """ニュース本文を売買推奨ではなく注意喚起用に簡易分類する。"""
    text = f"{title} {summary}".lower()

    positive = (
        "上方修正", "増収", "増益", "黒字転換", "大型受注", "受注", "新規契約",
        "業務提携", "資本提携", "採用", "増産", "自社株買い", "増配", "補助金", "承認",
        "order", "contract", "partnership", "deal", "award", "buyback", "dividend increase",
        "raises forecast", "raised forecast", "approval"
    )
    negative = (
        "下方修正", "減収", "減益", "赤字転落", "赤字拡大", "減産", "契約解除", "破談",
        "訴訟", "調査", "行政処分", "リコール", "停止", "事故", "規制", "制裁", "情報漏洩",
        "downgrade", "cuts forecast", "cut forecast", "recall", "lawsuit", "investigation",
        "suspension", "ban", "sanction", "data breach"
    )
    high_impact = (
        "大型", "大幅", "巨額", "上方修正", "下方修正", "tob", "買収", "合併", "株式交換", "経営統合",
        "契約締結", "新規契約", "業務提携", "資本提携", "リコール", "訴訟", "行政処分",
        "工場停止", "生産停止", "contract", "acquisition", "merger", "buyout", "recall"
    )
    definitive = (
        "締結", "発表", "決定", "受注", "採用", "完了", "承認", "成立", "signed", "awarded",
        "announced", "approved", "completed"
    )
    tentative = (
        "見込み", "予定", "計画", "合意する見込み", "expected", "plans to", "set to", "likely"
    )
    uncertain = (
        "検討", "協議", "観測", "可能性", "報道", "関係者", "rumor", "reportedly", "considering",
        "talks", "may "
    )

    pos = sum(1 for k in positive if k in text)
    neg = sum(1 for k in negative if k in text)

    if pos > neg:
        direction = "追い風候補"
    elif neg > pos:
        direction = "逆風候補"
    else:
        direction = "要確認"

    if any(k in text for k in high_impact):
        impact = "大"
    elif pos or neg:
        impact = "中"
    else:
        impact = "小"

    if any(k in text for k in uncertain):
        certainty = "低"
    elif any(k in text for k in tentative):
        certainty = "中"
    elif any(k in text for k in definitive):
        certainty = "高"
    else:
        certainty = "中"

    # 株価への伝わり方を一言で示す。断定はしない。
    if any(k in text for k in ("株式交換", "経営統合", "merger", "acquisition", "buyout")):
        influence = (
            "資本関係や事業構造が変わる可能性があり、条件次第で株価への影響が大きくなりやすい材料です。"
            "交換条件・希薄化・業績寄与を確認したい内容です。"
        )
    elif any(k in text for k in ("契約", "受注", "contract", "order", "deal", "award")):
        if direction == "追い風候補":
            influence = "売上・受注増への期待が先行しやすい材料。正式契約や金額の具体化で影響度が変わります。"
        else:
            influence = "契約条件や金額が業績にどう効くかを確認したい材料です。"
    elif any(k in text for k in ("上方修正", "増益", "raises forecast", "raised forecast")):
        influence = "業績上振れ期待から買い材料として意識されやすい内容です。"
    elif any(k in text for k in ("下方修正", "減益", "赤字", "cuts forecast", "cut forecast")):
        influence = "業績悪化懸念から戻り売りや評価低下につながりやすい内容です。"
    elif any(k in text for k in ("訴訟", "リコール", "停止", "事故", "規制", "制裁", "lawsuit", "recall", "ban", "sanction")):
        influence = "費用増・事業制約への警戒が出やすく、短期的には売り材料になりやすい内容です。"
    elif direction == "追い風候補":
        influence = "期待先行で買い材料として意識される可能性があります。続報と株価反応を確認したい局面です。"
    elif direction == "逆風候補":
        influence = "警戒材料として売り圧力につながる可能性があります。株価への織り込み具合を確認したい局面です。"
    else:
        influence = "株価への影響方向はまだ明確ではありません。内容と続報を確認したい材料です。"

    return {
        "direction": direction,
        "impact": impact,
        "certainty": certainty,
        "influence": influence,
    }


def _news_search_names(code, company, headers):
    """ニュース検索用に、取得済み会社名に加えてYahoo日本版の日本語社名も拾う。"""
    code = str(code).strip().upper()
    names = []

    company_clean = _clean_news_company_name(company)
    if company_clean:
        names.append(company_clean)

    # yfinanceの会社名が英語名になる銘柄向け。Yahoo日本版の<title>から日本語社名を補う。
    try:
        response = requests.get(
            f"https://finance.yahoo.co.jp/quote/{code}.T",
            headers=headers,
            timeout=7,
        )
        if response.ok:
            match = re.search(r"<title>\s*([^<【]+)【" + re.escape(code) + r"】", response.text, re.I)
            if match:
                jp_name = html.unescape(match.group(1)).strip()
                jp_name = _clean_news_company_name(jp_name)
                if jp_name:
                    names.append(jp_name)
    except Exception:
        pass

    # 順序を維持して重複排除。
    unique = []
    seen = set()
    for name in names:
        key = re.sub(r"\s+", "", str(name)).lower()
        if key and key not in seen:
            seen.add(key)
            unique.append(name)
    return unique


def fetch_stock_impact_news(code, company, max_age_days=14):
    """スイング向けに、直近の株価影響ニュースを注意喚起用として取得する。"""
    ticker = f"{str(code).strip().upper()}.T"
    company_clean = _clean_news_company_name(company)
    items = []

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 Chrome/124 Safari/537.36"
        )
    }

    search_names = _news_search_names(code, company, headers)

    # 1) Yahoo Finance検索API。
    #    ティッカーだけでなく、銘柄コード・会社名でも探して日本語ニュースの取りこぼしを減らす。
    yahoo_queries = [ticker, str(code).strip().upper()] + search_names
    for yahoo_query in yahoo_queries:
        if not yahoo_query:
            continue
        try:
            response = requests.get(
                "https://query1.finance.yahoo.com/v1/finance/search",
                params={"q": yahoo_query, "quotesCount": 1, "newsCount": 12},
                headers=headers,
                timeout=7,
            )
            if response.ok:
                for row in (response.json().get("news") or []):
                    items.append({
                        "title": str(row.get("title") or "").strip(),
                        "summary": str(row.get("summary") or "").strip(),
                        "url": str(row.get("link") or "").strip(),
                        "source": str(row.get("publisher") or "Yahoo Finance").strip(),
                        "published": _parse_news_date(row.get("providerPublishTime")),
                    })
        except Exception:
            pass

    # 2) Google News RSS。
    #    yfinanceの英語社名だけに依存せず、日本語社名・銘柄コード・材料語でも複数検索する。
    google_queries = []
    for name in search_names:
        google_queries.append(f'"{name}" 株')
        google_queries.append(f'"{name}" 契約 OR 提携 OR 受注 OR 買収 OR 株式交換')
    google_queries.extend([
        f'"{str(code).strip().upper()}" 株',
        f'"{str(code).strip().upper()}" 契約 OR 提携 OR 受注 OR 買収 OR 株式交換',
    ])

    seen_queries = set()
    for raw_query in google_queries:
        query_key = raw_query.strip().lower()
        if not query_key or query_key in seen_queries:
            continue
        seen_queries.add(query_key)
        try:
            query = quote(raw_query)
            url = f"https://news.google.com/rss/search?q={query}&hl=ja&gl=JP&ceid=JP:ja"
            response = requests.get(url, headers=headers, timeout=7)
            if response.ok:
                root = ET.fromstring(response.content)
                for node in root.findall(".//item")[:12]:
                    title = (node.findtext("title") or "").strip()
                    link = (node.findtext("link") or "").strip()
                    pub = _parse_news_date(node.findtext("pubDate"))
                    source_node = node.find("source")
                    source = (source_node.text or "Google News").strip() if source_node is not None else "Google News"
                    items.append({
                        "title": title,
                        "summary": "",
                        "url": link,
                        "source": source,
                        "published": pub,
                    })
        except Exception:
            pass

    now = pd.Timestamp.now(tz="Asia/Tokyo")
    deduped = []
    seen = set()
    for item in items:
        title = re.sub(r"\s+", " ", item.get("title") or "").strip()
        if not title:
            continue
        key = re.sub(r"[^0-9a-zA-Zぁ-んァ-ン一-龥]", "", title.lower())[:90]
        if key in seen:
            continue
        seen.add(key)

        published = item.get("published")
        age_days = None
        if published is not None:
            try:
                age_days = max(0, int((now - published).total_seconds() // 86400))
            except Exception:
                age_days = None
        if age_days is not None and age_days > max_age_days:
            continue

        assessed = _assess_stock_news(title, item.get("summary") or "")
        item.update(assessed)
        item["age_days"] = age_days

        # 注意喚起として意味のあるものだけ残す。方向不明でも影響度大なら表示。
        if assessed["impact"] in ("大", "中") and (
            assessed["direction"] != "要確認" or assessed["impact"] == "大"
        ):
            deduped.append(item)

    impact_rank = {"大": 2, "中": 1, "小": 0}
    certainty_rank = {"高": 2, "中": 1, "低": 0}
    # 注意喚起は「最新の材料」を最優先。同日時なら影響度・確度が高いものを優先。
    def _news_sort_key(item):
        published = item.get("published")
        try:
            published_value = pd.Timestamp(published).timestamp() if published is not None else 0
        except Exception:
            published_value = 0
        return (
            published_value,
            impact_rank.get(item.get("impact"), 0),
            certainty_rank.get(item.get("certainty"), 0),
        )

    deduped.sort(key=_news_sort_key, reverse=True)

    # スマホで見やすく、既読かどうか日付で判断できるよう最新1件だけ返す。
    return deduped[:1]


def _news_date_text(item):
    published = (item or {}).get("published")
    if published is None:
        return "日付不明"
    try:
        ts = pd.Timestamp(published)
        if ts.tzinfo is None:
            ts = ts.tz_localize("UTC").tz_convert("Asia/Tokyo")
        else:
            ts = ts.tz_convert("Asia/Tokyo")
        return ts.strftime("%Y/%m/%d")
    except Exception:
        return "日付不明"


def _freshness_explanation(adjustment):
    """数式ではなく、人が読みやすい決算鮮度の説明を返す。"""
    days = adjustment.get("days_since_disclosure")
    rate = int(round(float(adjustment.get("freshness_rate", 1.0)) * 100))
    total = int(adjustment.get("total", 0))
    raw = int(adjustment.get("raw_total", 0))

    if days is None:
        return f"業績補正：{total:+d}点（決算日の比較ができないため、最新取得データをそのまま反映）"
    if days < 0:
        return "業績補正：0点（株価基準日より後の決算のため、今回の採点には使用していません）"
    if rate >= 100:
        return f"業績補正：{total:+d}点（開示から{days}日。最新決算の内容をそのまま反映）"

    return (
        f"業績補正：{total:+d}点（開示から{days}日。"
        f"最新の公式決算ですが、時間経過を考慮して影響を{rate}%に抑えて反映）"
    )


def show_swing(result, earnings=None, news=None):
    st.subheader("📊 スイング分析")
    st.caption(f'現在値 {result.get("close", 0):.1f} 円　｜　データ時点 {result.get("data_asof", "-")}')

    adjustment = calculate_earnings_adjustment(
        earnings,
        result.get("data_date"),
    )
    technical_score = result["score"]
    total_score = max(0, min(100, technical_score + adjustment["total"]))
    total_grade, total_grade_text = swing_grade(total_score)

    c1, c2 = st.columns(2)

    with c1:
        st.metric(
            "総合スイング適性",
            total_grade,
            f'{total_score} / 100'
        )

    with c2:
        st.markdown("**判定**")
        st.write(total_grade_text)
        if news:
            news_date = _news_date_text(news[0])
            st.markdown(
                '<a href="#swing-impact-news" style="text-decoration:none; font-weight:700;">'
                f'⚠ 株価影響ニュースがあります（{html.escape(news_date)}）→ 内容を見る</a>',
                unsafe_allow_html=True,
            )

    st.write(
        f'**点数内訳**　テクニカル {technical_score}点　｜　'
        f'業績補正 {adjustment["total"]:+d}点　｜　総合 {total_score}点'
    )

    with st.expander("業績補正の内訳"):
        st.write(
            f'元の内訳：会社予想 {adjustment["forecast"]:+d}点　｜　'
            f'前年同期実績 {adjustment["actual"]:+d}点　｜　'
            f'予想利益率 {adjustment["margin"]:+d}点　｜　'
            f'前年同期進捗 {adjustment["progress"]:+d}点'
        )
        st.write(_freshness_explanation(adjustment))
        for reason in adjustment["reasons"]:
            st.write(f"・{reason}")

    st.write(f'**トレンド**　{result["trend"]}')
    st.write(f'**60日内の位置**　{result["position"]}')
    st.write(f'**20日騰落率**　{result["return20"]:+.1f}%')
    st.write(f'**60日騰落率**　{result["return60"]:+.1f}%')
    st.write(
        f'**出来高**　{result["volume_ratio"]:.2f}倍'
        f'（{result["volume_label"]}）'
    )
    st.write(
        f'**ATR**　{result["atr_pct"]:.2f}%'
        f'（{result["volatility_label"]}）'
    )

    st.markdown("**移動平均線**")
    st.write(
        f'5日線 {result["ma5"]:.1f}　｜　'
        f'25日線 {result["ma25"]:.1f}　｜　'
        f'75日線 {result["ma75"]:.1f}'
    )

    st.markdown("**簡易支持・抵抗**")
    st.write(
        f'支持候補：{result["support"]:.1f} 円　｜　'
        f'抵抗候補：{result["resistance"]:.1f} 円'
    )

    with st.expander("スイング分析のポイント"):
        for comment in result["comments"]:
            st.write(f"・{comment}")

    # 株価に影響しそうなニュースは、点数に混ぜず注意喚起としてだけ表示する。
    if news:
        st.markdown('<div id="swing-impact-news"></div>', unsafe_allow_html=True)
        st.markdown("### 📰 株価影響ニュース")
        st.caption("ニュースは総合点には加えず、スイング判断で意識したい外部材料として表示します。")
        item = news[0]
        title = item.get("title") or "ニュース"
        source = item.get("source") or "情報源不明"
        news_date = _news_date_text(item)
        st.markdown(f"**{html.escape(news_date)}｜{html.escape(title)}**")
        st.write(
            f'材料方向：{item.get("direction", "要確認")}　｜　'
            f'影響度：{item.get("impact", "-")}　｜　'
            f'確度：{item.get("certainty", "-")}'
        )
        st.write(f'**株価への影響分析：** {item.get("influence", "確認が必要です。")}')
        st.caption(source)
        if item.get("url"):
            st.link_button("元記事を見る", item["url"])

    # 決算の実額と短期用の業績補正は、スイング欄でだけ確認できるようにする。
    with st.expander("決算データ・スイング用の業績評価を詳しく見る"):
        show_earnings_summary(earnings, result.get("data_date"))

    st.caption(
        "総合点はテクニカル100点に鮮度調整後の業績補正を加え、"
        "0～100点の範囲に収めています。"
    )


# =========================================================
# メイン画面
# =========================================================
st.markdown(
    '<p style="font-size:1.4rem; font-weight:700; margin:0 0 0.5rem 0; '
    'white-space:nowrap;">カブグルマン★★★</p>',
    unsafe_allow_html=True,
)
show_morning_market_brief()
st.divider()

mode = st.radio(
    "何をしますか？",
    [
        "気になる銘柄を調べる",
        "候補銘柄を探す"
    ]
)


if mode == "気になる銘柄を調べる":

    code_input = st.text_input(
        "銘柄コード",
        placeholder="例：6526",
        key="stock_code_input",
        on_change=submit_stock_code,
    )

    st.markdown("### 投資スタイルを選択")

    daytrade = st.checkbox("⚡ デイトレ")
    swing = st.checkbox("📊 スイング")
    longterm = st.checkbox("🏢 中長期")

    selected_styles = []

    if daytrade:
        selected_styles.append("daytrade")

    if swing:
        selected_styles.append("swing")

    if longterm:
        selected_styles.append("longterm")

    analyze_button = st.button(
        "分析する",
        type="primary",
        use_container_width=True
    )
    enter_submit = bool(st.session_state.pop("stock_code_enter_submit", False))

    if analyze_button or enter_submit:

        code = normalize_code(code_input)

        if re.fullmatch(r"[0-9A-Z]{4}", code) is None:
            st.error("銘柄コードは半角英数字4文字で入力してください（例：6526 / 265A）。")

        elif not selected_styles:
            st.warning("投資スタイルを1つ以上選んでください。")

        else:
            with st.spinner("株価データを分析しています..."):

                data = download_stock_data(code)

                if data is None:
                    st.error(
                        "株価データを取得できませんでした。"
                        "銘柄コードを確認してください。"
                    )

                else:
                    ticker = f"{code}.T"
                    company = get_company_name(ticker)
                    current_snapshot = fetch_stock_snapshot(code)
                    confirmed_data = confirmed_daily_data(data, current_snapshot)

                    result = {}
                    earnings = None
                    if "swing" in selected_styles or "longterm" in selected_styles:
                        earnings = fetch_earnings_summary(code)

                    if "daytrade" in selected_styles:
                        result["outlook"] = run_stock_outlook(code, confirmed_data, current_snapshot=current_snapshot)
                        intraday_adjustment = get_intraday_daytrade_adjustment(code)
                        result["daytrade"] = analyze_daytrade(
                            data,
                            intraday_adjustment=intraday_adjustment,
                            current_snapshot=current_snapshot,
                        )

                    if "swing" in selected_styles:
                        result["swing"] = analyze_swing(data, current_snapshot=current_snapshot)
                        result["swing_earnings"] = earnings
                        result["swing_news"] = fetch_stock_impact_news(code, company)

                    if "longterm" in selected_styles:
                        result["longterm"] = analyze_longterm(data, earnings, current_snapshot=current_snapshot)
                        result["longterm_earnings"] = earnings

                    st.session_state.helper_result = result
                    st.session_state.helper_code = code
                    st.session_state.helper_company = company
                    st.session_state.helper_date = (
                        str((current_snapshot or {}).get("date"))
                        if current_snapshot and current_snapshot.get("date")
                        else pd.Timestamp(data.index[-1]).strftime("%Y-%m-%d")
                    )
                    st.session_state.helper_styles = selected_styles

                    st.rerun()

    # -----------------------------------------------------
    # 分析結果
    # -----------------------------------------------------
    if st.session_state.helper_result is not None:

        st.divider()

        code = st.session_state.helper_code
        company = st.session_state.helper_company
        data_date = st.session_state.helper_date
        result = st.session_state.helper_result

        st.header(f"{code} {company}")
        st.caption(f"データ日：{data_date}")

        if "daytrade" in result:
            show_daytrade(
                result["daytrade"],
                result.get("outlook"),
            )

        if "swing" in result:
            st.divider()
            show_swing(
                result["swing"],
                result.get("swing_earnings"),
                result.get("swing_news"),
            )

        if "longterm" in result:
            st.divider()
            show_longterm(
                result["longterm"],
                result.get("longterm_earnings"),
                code=code,
            )
            # 決算データは中長期でも参照可能にする。スイング固有の影響表示だけ省略。
            # 両方の分析を選択した場合はスイング側の決算詳細を共用し、重複表示しない。
            if "swing" not in result:
                with st.expander("🧾 決算データ・短期用の業績評価を見る（中長期採点とは別）"):
                    show_earnings_summary(
                        result.get("longterm_earnings"),
                        show_swing_effect=False,
                    )

        st.button(
            "🔄 別の銘柄を調べる",
            use_container_width=True,
            on_click=reset_analysis
        )


else:
    st.info(
        "「候補銘柄を探す」は次の段階で実装します。"
        "デイトレ・スイング・中長期を別々にランキングする予定です。"
    )


st.caption("分析は参考情報であり、投資成果を保証しません。")
