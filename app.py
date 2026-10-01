import streamlit as st
import subprocess
import sys
import locale
import re
from pathlib import Path


st.set_page_config(
    page_title="株価分析アプリ",
    page_icon="📈",
    layout="centered"
)

# =========================================================
# 銘柄入力の状態管理
# =========================================================
if "kabuka_code_input" not in st.session_state:
    st.session_state.kabuka_code_input = "6526"


def reset_stock_view():
    st.session_state.kabuka_code_input = ""



# =========================================================
# スマホ向け表示調整
# =========================================================
st.markdown(
    """
    <style>
    .block-container {
        padding-top: 1.2rem;
        padding-bottom: 2rem;
        max-width: 760px;
    }

    h1 {
        font-size: 1.65rem !important;
        margin-bottom: 0.35rem !important;
    }

    h2 {
        font-size: 1.25rem !important;
        margin-top: 0.8rem !important;
        margin-bottom: 0.35rem !important;
    }

    h3 {
        font-size: 1.05rem !important;
        margin-top: 0.65rem !important;
        margin-bottom: 0.25rem !important;
    }

    .compact-card {
        border: 1px solid rgba(128,128,128,0.25);
        border-radius: 10px;
        padding: 10px 12px;
        margin-bottom: 8px;
    }

    .compact-label {
        font-size: 0.78rem;
        opacity: 0.75;
        margin-bottom: 2px;
    }

    .compact-value {
        font-size: 1.02rem;
        font-weight: 700;
        line-height: 1.3;
        word-break: break-word;
    }

    .price-line {
        font-size: 0.92rem;
        line-height: 1.5;
        margin: 3px 0;
        word-break: break-word;
    }

    .ground-line {
        font-size: 0.90rem;
        line-height: 1.45;
        margin: 2px 0;
        word-break: break-word;
    }

    .small-info {
        font-size: 0.88rem;
        line-height: 1.45;
        padding: 8px 10px;
        border-radius: 8px;
        border: 1px solid rgba(128,128,128,0.25);
        margin-top: 6px;
        margin-bottom: 8px;
    }

    div[data-testid="stTextInput"] label,
    div[data-testid="stButton"] button {
        font-size: 0.95rem !important;
    }

    div[data-testid="stExpander"] summary {
        font-size: 0.92rem !important;
    }
    </style>
    """,
    unsafe_allow_html=True
)


# =========================================================
# 表示補助
# =========================================================
def find_value(text, label):
    pattern = rf"{re.escape(label)}\s*:\s*(.+)"
    match = re.search(pattern, text)
    return match.group(1).strip() if match else "-"


def find_company_title(text, code):
    for line in text.splitlines():
        if code in line and "今日のデイトレ展望" in line:
            title = line.strip()
            title = title.replace("今日のデイトレ展望", "").strip()
            return title
    return code


def compact_price(value):
    if value == "-":
        return "-"
    return value.replace("円付近", "").replace("円", "").strip()


# =========================================================
# ヘッダー
# =========================================================
st.title("📈 株価分析アプリ")
st.subheader("株価展望")
st.caption("日足・類似局面・重要価格・地合いから、短期の株価展望を確認します。")


code = st.text_input(
    "銘柄コード",
    placeholder="例：6526",
    key="kabuka_code_input"
)


analyze_button = st.button(
    "株価展望を見る",
    type="primary",
    use_container_width=True
)


if analyze_button:

    code = code.strip().upper()

    if code.endswith(".T"):
        code = code[:-2]

    # 東証の銘柄コードは、数字4桁に加えて 265A のような
    # 半角英数字4文字のコードにも対応する
    if re.fullmatch(r"[0-9A-Z]{4}", code) is None:

        st.error("銘柄コードは半角英数字4文字で入力してください（例：6526 / 265A）。")

    else:

        stock_ai_path = Path(__file__).parent / "stock_ai.py"

        if not stock_ai_path.exists():

            st.error("stock_ai.py が見つかりません。")

        else:

            with st.spinner("株価と地合いを分析しています..."):

                try:

                    encoding = locale.getpreferredencoding(False)

                    input_text = code + "\n\n"

                    result = subprocess.run(
                        [
                            sys.executable,
                            str(stock_ai_path)
                        ],
                        input=input_text.encode(
                            encoding,
                            errors="replace"
                        ),
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        cwd=str(stock_ai_path.parent),
                        timeout=120
                    )

                    output = result.stdout.decode(
                        encoding,
                        errors="replace"
                    )

                    error_output = result.stderr.decode(
                        encoding,
                        errors="replace"
                    )

                    if output:

                        cleaned_lines = []

                        for line in output.splitlines():

                            if "Enterキーを押して" in line:
                                break

                            cleaned_lines.append(line)

                        output = "\n".join(cleaned_lines)

                        # 詳細表示用：起動ログを除き、分析結果の開始位置から表示
                        detail_lines = output.splitlines()
                        detail_start = None

                        for i, line in enumerate(detail_lines):
                            if (
                                code in line
                                and "今日のデイトレ展望" in line
                            ):
                                detail_start = max(0, i - 1)
                                break

                        if detail_start is not None:
                            detail_output = "\n".join(
                                detail_lines[detail_start:]
                            )
                        else:
                            detail_output = output

                        # -----------------------------
                        # 基本情報
                        # -----------------------------
                        company_title = find_company_title(
                            output,
                            code
                        )

                        latest_date = find_value(
                            output,
                            "最新データ日"
                        )

                        close_price = find_value(
                            output,
                            "前日終値"
                        )

                        direction = find_value(
                            output,
                            "方向性"
                        )

                        volatility = find_value(
                            output,
                            "値動き傾向"
                        )

                        confidence = find_value(
                            output,
                            "予測信頼度"
                        )

                        st.success("分析が完了しました。")

                        st.header(company_title)

                        st.caption(
                            f"データ日：{latest_date}　｜　前日終値：{close_price}"
                        )

                        # -----------------------------
                        # 今日の展望
                        # -----------------------------
                        st.subheader("今日の展望")

                        c1, c2, c3 = st.columns(3)

                        with c1:
                            st.markdown(
                                f"""
                                <div class="compact-card">
                                    <div class="compact-label">方向性</div>
                                    <div class="compact-value">{direction}</div>
                                </div>
                                """,
                                unsafe_allow_html=True
                            )

                        with c2:
                            st.markdown(
                                f"""
                                <div class="compact-card">
                                    <div class="compact-label">値動き</div>
                                    <div class="compact-value">{volatility}</div>
                                </div>
                                """,
                                unsafe_allow_html=True
                            )

                        with c3:
                            st.markdown(
                                f"""
                                <div class="compact-card">
                                    <div class="compact-label">信頼度</div>
                                    <div class="compact-value">{confidence}</div>
                                </div>
                                """,
                                unsafe_allow_html=True
                            )

                        # -----------------------------
                        # 重要価格
                        # -----------------------------
                        st.subheader("重要価格")

                        up_first = "-"
                        up_middle = "-"
                        up_main = "-"
                        up_major = "-"

                        down_first = "-"
                        down_middle = "-"
                        down_main = "-"
                        down_major = "-"

                        current_direction = None

                        for line in output.splitlines():

                            stripped = line.strip()

                            if stripped == "【上方向】":
                                current_direction = "up"
                                continue

                            if stripped == "【下方向】":
                                current_direction = "down"
                                continue

                            if current_direction == "up":

                                if "① 最初の分岐" in line:
                                    up_first = line.split(":", 1)[-1].strip()

                                elif "途中警戒" in line:
                                    up_middle = line.split(":", 1)[-1].strip()

                                elif "② 本命分岐" in line:
                                    up_main = line.split(":", 1)[-1].strip()

                                elif "③ 大きな節目" in line:
                                    up_major = line.split(":", 1)[-1].strip()

                            elif current_direction == "down":

                                if "① 最初の分岐" in line:
                                    down_first = line.split(":", 1)[-1].strip()

                                elif "途中警戒" in line:
                                    down_middle = line.split(":", 1)[-1].strip()

                                elif "② 本命分岐" in line:
                                    down_main = line.split(":", 1)[-1].strip()

                                elif "③ 大きな節目" in line:
                                    down_major = line.split(":", 1)[-1].strip()

                        up_prices = [
                            compact_price(x)
                            for x in [up_first, up_middle, up_main, up_major]
                            if x != "-"
                        ]

                        down_prices = [
                            compact_price(x)
                            for x in [down_first, down_middle, down_main, down_major]
                            if x != "-"
                        ]

                        st.markdown(
                            f'<div class="price-line"><b>↑ 上方向</b>　{" → ".join(up_prices)}</div>',
                            unsafe_allow_html=True
                        )

                        st.markdown(
                            f'<div class="price-line"><b>↓ 下方向</b>　{" → ".join(down_prices)}</div>',
                            unsafe_allow_html=True
                        )

                        # -----------------------------
                        # 地合い
                        # -----------------------------
                        st.subheader("地合い")

                        japan = find_value(output, "日本株地合い")
                        us_tech = find_value(output, "米国ハイテク")
                        semiconductor = find_value(output, "半導体地合い")
                        asia = find_value(output, "アジア地合い")
                        fx = find_value(output, "為替環境")
                        total_ground = find_value(output, "総合地合い")

                        ground_html = f"""
                        <div class="ground-line"><b>日本株</b>　{japan}</div>
                        <div class="ground-line"><b>米国ハイテク</b>　{us_tech}</div>
                        <div class="ground-line"><b>半導体</b>　{semiconductor}</div>
                        <div class="ground-line"><b>アジア</b>　{asia}</div>
                        <div class="ground-line"><b>為替</b>　{fx}</div>
                        <div class="small-info"><b>総合地合い</b>　{total_ground}</div>
                        """

                        st.markdown(
                            ground_html,
                            unsafe_allow_html=True
                        )

                        # -----------------------------
                        # 詳細
                        # -----------------------------
                        with st.expander("詳しい分析を見る"):
                            st.text(detail_output)

                        st.divider()

                        st.button(
                            "🔄 別の銘柄を調べる",
                            use_container_width=True,
                            on_click=reset_stock_view
                        )

                    else:

                        st.error(
                            "分析結果を取得できませんでした。"
                        )

                    if error_output.strip():

                        with st.expander("エラー詳細"):
                            st.text(error_output)

                except subprocess.TimeoutExpired:

                    st.error(
                        "分析に時間がかかりすぎました。"
                    )

                except Exception as e:

                    st.error(
                        f"エラーが発生しました：{e}"
                    )


st.divider()

st.caption("現在は「株価展望」日足Ver.1です。")

st.caption(
    "分析結果は将来の株価や売買成果を保証するものではなく、"
    "投資判断の参考情報として表示しています。"
)

st.caption(
    "今後「銘柄選定お助けマン」「デイトレ展望」を追加予定です。"
)
