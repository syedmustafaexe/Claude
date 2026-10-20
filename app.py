"""
NIFTY Live Option Chain Tracker (NSE India)
-------------------------------------------
- Fetches the NSE option chain for NIFTY (handles cookies + headers)
- Finds ATM strike from spot
- Sums Change in OI for CE / PE across ATM +/- N strikes
- Auto-refreshes every 60 seconds
- Mobile-friendly card UI

Run locally:  streamlit run app.py
"""

import time
from datetime import datetime, timedelta, timezone

import pandas as pd
import requests
import streamlit as st

# ----------------------------------------------------------------------------
# Config
# ----------------------------------------------------------------------------
SYMBOL = "NIFTY"
REFRESH_SECONDS = 60
BASE_URL = "https://www.nseindia.com"
API_URL = f"{BASE_URL}/api/option-chain-indices?symbol={SYMBOL}"
IST = timezone(timedelta(hours=5, minutes=30))

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate",
    "Referer": f"{BASE_URL}/option-chain",
    "Connection": "keep-alive",
}

st.set_page_config(
    page_title="NIFTY Option Chain Live",
    page_icon="📈",
    layout="centered",
    initial_sidebar_state="collapsed",
)

# ----------------------------------------------------------------------------
# Styling (mobile responsive cards)
# ----------------------------------------------------------------------------
st.markdown(
    """
<style>
.block-container {padding-top: 1.2rem; padding-bottom: 2rem; max-width: 900px;}
.grid {display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 14px; margin: 10px 0 18px 0;}
.card {border-radius: 16px; padding: 18px 20px; background: rgba(128,128,128,0.10);
       border: 1px solid rgba(128,128,128,0.25);}
.card .label {font-size: 0.85rem; opacity: 0.75; text-transform: uppercase; letter-spacing: .05em;}
.card .value {font-size: 2.1rem; font-weight: 800; line-height: 1.2; margin-top: 4px;}
.card .sub {font-size: 0.9rem; opacity: 0.8; margin-top: 4px;}
.green {color: #16a34a;} .red {color: #dc2626;} .amber {color: #d97706;}
.card.bull {background: rgba(22,163,74,0.14); border-color: rgba(22,163,74,0.5);}
.card.bear {background: rgba(220,38,38,0.14); border-color: rgba(220,38,38,0.5);}
.card.neutral {background: rgba(217,119,6,0.14); border-color: rgba(217,119,6,0.5);}
.small {font-size: 0.8rem; opacity: 0.7;}
@media (max-width: 480px) {.card .value {font-size: 1.8rem;}}
</style>
""",
    unsafe_allow_html=True,
)

# ----------------------------------------------------------------------------
# NSE fetching
# ----------------------------------------------------------------------------
def _new_session() -> requests.Session:
    """Create a session and prime it with NSE cookies."""
    s = requests.Session()
    s.headers.update(HEADERS)
    # Visiting these pages first sets the cookies NSE requires for the API.
    s.get(BASE_URL, timeout=10)
    s.get(f"{BASE_URL}/option-chain", timeout=10)
    return s


@st.cache_resource(ttl=240, show_spinner=False)
def _cached_session() -> requests.Session:
    # Cookies go stale quickly, so the session is rebuilt every 4 minutes.
    return _new_session()


@st.cache_data(ttl=45, show_spinner=False)
def fetch_option_chain() -> dict:
    """Fetch raw option chain JSON, retrying with a fresh session on failure."""
    last_err = None
    for attempt in range(3):
        try:
            session = _cached_session() if attempt == 0 else _new_session()
            resp = session.get(API_URL, timeout=15)
            if resp.status_code == 200:
                data = resp.json()
                if data and "records" in data and data["records"].get("data"):
                    return data
                last_err = "NSE returned an empty response (cookies/blocked)."
            else:
                last_err = f"HTTP {resp.status_code} from NSE."
        except Exception as e:  # network / JSON errors
            last_err = str(e)
        _cached_session.clear()
        time.sleep(1.5)
    raise RuntimeError(last_err or "Unknown error fetching NSE data")


# ----------------------------------------------------------------------------
# Analysis
# ----------------------------------------------------------------------------
def build_dataframe(raw: dict, expiry: str) -> pd.DataFrame:
    rows = []
    for item in raw["records"]["data"]:
        if item.get("expiryDate") != expiry:
            continue
        ce, pe = item.get("CE", {}), item.get("PE", {})
        rows.append(
            {
                "Strike": item["strikePrice"],
                "CE OI": ce.get("openInterest", 0),
                "CE Chng OI": ce.get("changeinOpenInterest", 0),
                "CE LTP": ce.get("lastPrice", 0),
                "PE LTP": pe.get("lastPrice", 0),
                "PE Chng OI": pe.get("changeinOpenInterest", 0),
                "PE OI": pe.get("openInterest", 0),
            }
        )
    return pd.DataFrame(rows).sort_values("Strike").reset_index(drop=True)


def analyse(df: pd.DataFrame, spot: float, n: int):
    atm_idx = (df["Strike"] - spot).abs().idxmin()
    atm = int(df.loc[atm_idx, "Strike"])
    lo, hi = max(0, atm_idx - n), min(len(df) - 1, atm_idx + n)
    window = df.loc[lo:hi].copy()
    ce_sum = int(window["CE Chng OI"].sum())
    pe_sum = int(window["PE Chng OI"].sum())
    return atm, window, ce_sum, pe_sum


def fmt(x: int) -> str:
    return f"{x:+,}"


def card(label, value, sub="", css="", vclass=""):
    return (
        f'<div class="card {css}"><div class="label">{label}</div>'
        f'<div class="value {vclass}">{value}</div><div class="sub">{sub}</div></div>'
    )


# ----------------------------------------------------------------------------
# Sidebar controls
# ----------------------------------------------------------------------------
st.sidebar.header("⚙️ Settings")
st.sidebar.slider("Strikes above/below ATM", 1, 15, 5, key="n_strikes")
st.sidebar.checkbox("Show strike table", value=True, key="show_table")
if st.sidebar.button("🔄 Refresh now"):
    fetch_option_chain.clear()
    st.rerun()

st.title("📈 NIFTY Option Chain")


# ----------------------------------------------------------------------------
# Live view (re-runs automatically every REFRESH_SECONDS)
# ----------------------------------------------------------------------------
@st.fragment(run_every=REFRESH_SECONDS)
def live_view():
    n = st.session_state.get("n_strikes", 5)

    try:
        raw = fetch_option_chain()
        st.session_state["last_good"] = raw
        st.session_state["last_ok_time"] = datetime.now(IST)
        error = None
    except Exception as e:
        raw = st.session_state.get("last_good")
        error = str(e)

    if raw is None:
        st.error(
            f"Could not fetch data from NSE: {error}\n\n"
            "NSE often blocks cloud/overseas IPs. Retrying automatically in "
            f"{REFRESH_SECONDS}s."
        )
        return
    if error:
        st.warning(f"Showing last successful data. Latest fetch failed: {error}")

    records = raw["records"]
    spot = float(records["underlyingValue"])
    expiries = records["expiryDates"]

    expiry = st.selectbox("Expiry", expiries, index=0, key="expiry")
    df = build_dataframe(raw, expiry)
    if df.empty:
        st.warning("No data for this expiry.")
        return

    atm, window, ce_sum, pe_sum = analyse(df, spot, n)
    net = pe_sum - ce_sum  # +ve => more put OI build-up than call OI build-up
    total = abs(pe_sum) + abs(ce_sum)
    ratio = net / total if total else 0

    if ratio > 0.15:
        sentiment, css, vcls, emoji = "BULLISH", "bull", "green", "🟢"
    elif ratio < -0.15:
        sentiment, css, vcls, emoji = "BEARISH", "bear", "red", "🔴"
    else:
        sentiment, css, vcls, emoji = "NEUTRAL", "neutral", "amber", "🟡"

    pcr_chg = (pe_sum / ce_sum) if ce_sum else float("nan")

    html = '<div class="grid">'
    html += card("NIFTY Spot", f"{spot:,.2f}", f"ATM Strike: <b>{atm}</b>")
    html += card(
        "Range",
        f"{window['Strike'].min()} – {window['Strike'].max()}",
        f"ATM ± {n} strikes · Expiry {expiry}",
    )
    html += "</div><div class=\"grid\">"
    html += card("Total CE Chng in OI", fmt(ce_sum), "Call side (resistance)", vclass="red")
    html += card("Total PE Chng in OI", fmt(pe_sum), "Put side (support)", vclass="green")
    html += "</div><div class=\"grid\">"
    html += card(
        "Net Difference (PE − CE)",
        fmt(net),
        f"{emoji} {sentiment} · PE/CE chg ratio: "
        + (f"{pcr_chg:.2f}" if pcr_chg == pcr_chg else "n/a"),
        css=css,
        vclass=vcls,
    )
    html += "</div>"
    st.markdown(html, unsafe_allow_html=True)

    if st.session_state.get("show_table", True):
        def hl(row):
            return [
                "background-color: rgba(250,204,21,0.25); font-weight:700"
                if row["Strike"] == atm
                else ""
                for _ in row
            ]

        st.dataframe(
            window.style.apply(hl, axis=1).format(
                {c: "{:,.0f}" for c in ["CE OI", "CE Chng OI", "PE Chng OI", "PE OI"]}
                | {"CE LTP": "{:.2f}", "PE LTP": "{:.2f}"}
            ),
            hide_index=True,
            use_container_width=True,
        )

    ts = st.session_state.get("last_ok_time")
    ts_txt = ts.strftime("%d %b %Y, %H:%M:%S IST") if ts else "—"
    st.markdown(
        f'<div class="small">Last updated: {ts_txt} · Auto-refresh every '
        f"{REFRESH_SECONDS}s · NSE data timestamp: {records.get('timestamp', '—')}<br>"
        "Reading: PE OI build-up &gt; CE OI build-up = put writing / support (bullish bias); "
        "the reverse = call writing / resistance (bearish bias). Not investment advice.</div>",
        unsafe_allow_html=True,
    )


live_view()
