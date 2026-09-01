import os
import requests
import streamlit as st
import pandas as pd
import yfinance as yf
import feedparser
from datetime import datetime

st.set_page_config(page_title="KingsApple", page_icon="🍎", layout="centered")

PASSWORD = os.environ.get("KA_PASSWORD", "kathmandu123")
TG_TOKEN = os.environ.get("TG_TOKEN", "")
TG_CHAT = os.environ.get("TG_CHAT", "")

def send_phone(msg):
    if TG_TOKEN and TG_CHAT:
        try:
            requests.post(f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
                          json={"chat_id": TG_CHAT, "text": msg}, timeout=10)
            return True
        except Exception:
            return False
    return False

# ---------------- LOGIN ----------------
if "auth" not in st.session_state:
    st.session_state.auth = False
if not st.session_state.auth:
    st.title("🍎 KingsApple")
    st.caption("Your private financial AI in the cloud")
    pw = st.text_input("Password", type="password")
    if st.button("Unlock"):
        if pw == PASSWORD:
            st.session_state.auth = True
            st.rerun()
        else:
            st.error("Wrong password")
    st.stop()

# ---------------- DATA ----------------
@st.cache_data(ttl=600)
def get_hist(symbol):
    df = yf.download(symbol, period="1y", interval="1d", progress=False, auto_adjust=True)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    return df

@st.cache_data(ttl=600)
def get_news(query):
    url = f"https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en"
    return feedparser.parse(url).entries[:8]

def rsi14(close):
    d = close.diff()
    up = d.clip(lower=0).rolling(14).mean()
    dn = (-d.clip(upper=0)).rolling(14).mean()
    rs = up / dn.replace(0, 1e-9)
    return float((100 - 100 / (1 + rs)).iloc[-1])

def win_probability(hist):
    close = hist["Close"].dropna()
    if len(close) < 60:
        return 50, ["Not enough data"]
    price = float(close.iloc[-1])
    sma20 = float(close.rolling(20).mean().iloc[-1])
    sma50 = float(close.rolling(50).mean().iloc[-1])
    mom = (price / float(close.iloc[-30]) - 1) * 100
    rsi = rsi14(close)
    score, reasons = 50, []
    if price > sma20: score += 10; reasons.append("📈 Price above 20-day average")
    else: score -= 10; reasons.append("📉 Price below 20-day average")
    if price > sma50: score += 10; reasons.append("💪 Above 50-day average (strong trend)")
    else: score -= 10; reasons.append("🩸 Below 50-day average (weak trend)")
    if mom > 0: score += 8; reasons.append(f"🚀 Up {mom:.1f}% in 30 days (momentum)")
    else: score -= 8; reasons.append(f"🐌 Down {abs(mom):.1f}% in 30 days")
    if rsi < 30: score += 12; reasons.append("🧲 Oversold (RSI) - bounce likely")
    elif rsi > 70: score -= 12; reasons.append("🔥 Overbought (RSI) - pullback likely")
    else: reasons.append(f"⚖️ RSI neutral ({rsi:.0f})")
    return max(5, min(95, score)), reasons

def news_query(sym):
    m = {"BTC-USD": "Bitcoin", "ETH-USD": "Ethereum", "GLD": "gold price", "SPY": "S&P 500"}
    return m.get(sym, sym)

# ---------------- PORTFOLIO ----------------
try:
    port = pd.read_csv("portfolio.csv")
except Exception:
    port = pd.DataFrame([{"symbol": "BTC-USD", "qty": 0.5, "cost": 45000}])

prices = {}
for _, r in port.iterrows():
    try:
        prices[r["symbol"]] = float(get_hist(r["symbol"])["Close"].iloc[-1])
    except Exception:
        prices[r["symbol"]] = float(r["cost"])
total = sum(prices.get(r["symbol"], r["cost"]) * r["qty"] for _, r in port.iterrows())

# ---------------- APP ----------------
st.title("🍎 KingsApple")
st.caption(datetime.now().strftime("%A, %d %B %Y"))
st.metric("💰 Portfolio Value", f"${total:,.2f}")

st.subheader("💼 Your Holdings")
for _, r in port.iterrows():
    p = prices.get(r["symbol"], r["cost"])
    pnl = (p - r["cost"]) * r["qty"]
    st.write(f"**{r['symbol']}** — {r['qty']} units | Now: ${p:,.2f} | P&L: ${pnl:,.2f}")

st.subheader("📊 Market Chart")
options = sorted(set(port["symbol"].tolist() + ["SPY", "BTC-USD", "ETH-USD", "GLD", "AAPL", "MSFT"]))
sym = st.selectbox("Choose asset", options)
hist = get_hist(sym)
st.area_chart(hist["Close"].tail(180))

prob, reasons = win_probability(hist)
st.subheader(f"🎯 Win Probability for {sym}: {prob}%")
st.progress(prob / 100)
for x in reasons:
    st.write(x)
st.caption("Heuristic estimate from trend, momentum & RSI. Not financial advice.")

st.subheader(f"📰 News about {news_query(sym)}")
for e in get_news(news_query(sym)):
    st.markdown(f"• {e.title}")

st.subheader("📲 Phone Notifications")
if st.button("Send test notification to my phone"):
    st.success("Sent! Check Telegram ✅" if send_phone("🍎 KingsApple Cloud connected! Charts, news & alerts are live.") else "Add TG_TOKEN & TG_CHAT in Streamlit Secrets first")

if st.button("Log out"):
    st.session_state.auth = False
    st.rerun()