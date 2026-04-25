# ==========================================================
# TRUE SNIPER V7 - TEST MODE (FULL FIXED VERSION)
# ==========================================================

import os, time, requests, pandas as pd
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv
from binance.client import Client

load_dotenv()

API_KEY = os.getenv("API_KEY")
API_SECRET = os.getenv("API_SECRET")
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

client = Client(API_KEY, API_SECRET)
client.FUTURES_URL = "https://testnet.binancefuture.com/fapi"

SYMBOLS = ["BTCUSDT","ETHUSDT","BNBUSDT","SOLUSDT","XRPUSDT"]

WAIT = 20
LEVERAGE = 3
CAPITAL = 0.12

IST = timezone(timedelta(hours=5, minutes=30))

last_signal = {}

# ==========================================================
def ts():
    now = datetime.now(timezone.utc)
    ist = now.astimezone(IST)
    return f"{now.strftime('%H:%M:%S')} | {ist.strftime('%H:%M:%S')} IST"

# ==========================================================
def send(msg):
    print(msg, flush=True)
    if TOKEN and CHAT_ID:
        try:
            requests.post(
                f"https://api.telegram.org/bot{TOKEN}/sendMessage",
                json={"chat_id": CHAT_ID, "text": msg},
                timeout=5
            )
        except Exception as e:
            print("Telegram Error:", e)

# ==========================================================
def klines(symbol, interval):
    try:
        return client.get_klines(symbol=symbol, interval=interval, limit=100)
    except Exception as e:
        send(f"❌ KLINE ERROR {symbol}: {e}")
        return None

def frame(raw):
    df = pd.DataFrame(raw, columns=[
        "t","o","h","l","c","v","ct","qv","n","tb","tq","ig"])
    df = df[["o","h","l","c","v"]].astype(float)
    df.columns = ["open","high","low","close","volume"]
    return df

def compute_rsi(series, period=14):
    delta = series.diff()
    gain = (delta.where(delta > 0, 0)).rolling(period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))

def enrich(df):
    df["ema20"] = df["close"].ewm(span=20).mean()
    df["ema50"] = df["close"].ewm(span=50).mean()
    df["rsi"] = compute_rsi(df["close"], 14)
    df["vol_avg"] = df["volume"].rolling(10).mean()
    return df

# ==========================================================
# PRECISION FIX
def get_precision(symbol):
    try:
        info = client.futures_exchange_info()
        for s in info["symbols"]:
            if s["symbol"] == symbol:
                for f in s["filters"]:
                    if f["filterType"] == "LOT_SIZE":
                        step = float(f["stepSize"])
                        precision = str(step)[::-1].find('.')
                        return precision
    except:
        pass
    return 3

# ==========================================================
# BTC TREND FILTER
def btc_trend():
    df15 = enrich(frame(klines("BTCUSDT","15m")))
    df1h = enrich(frame(klines("BTCUSDT","1h")))

    c15 = df15.iloc[-1]
    c1h = df1h.iloc[-1]

    if c15["ema20"] > c15["ema50"] and c1h["ema20"] > c1h["ema50"]:
        return "BULLISH"
    elif c15["ema20"] < c15["ema50"] and c1h["ema20"] < c1h["ema50"]:
        return "BEARISH"
    return "SIDEWAYS"

# ==========================================================
# SIGNAL ENGINE
def signal(symbol, btc_dir):

    raw = klines(symbol, "15m")
    if not raw:
        return None

    df = enrich(frame(raw))
    c = df.iloc[-1]
    p = df.iloc[-2]

    score = 0
    reasons = []

    # EMA direction
    if c["ema20"] > c["ema50"]:
        direction = "BUY"
        score += 20; reasons.append("EMA Bull")
    elif c["ema20"] < c["ema50"]:
        direction = "SELL"
        score += 20; reasons.append("EMA Bear")
    else:
        return None

    # BTC alignment
    if (btc_dir == "BULLISH" and direction == "BUY") or \
       (btc_dir == "BEARISH" and direction == "SELL"):
        score += 25; reasons.append("BTC Align")
    else:
        return None

    # Breakout confirmation
    if direction == "BUY" and c["close"] > p["high"]:
        score += 15; reasons.append("Breakout")
    elif direction == "SELL" and c["close"] < p["low"]:
        score += 15; reasons.append("Breakdown")
    else:
        return None

    # Volume confirmation
    if c["volume"] > c["vol_avg"]:
        score += 10; reasons.append("Volume")

    # RSI confirmation
    if direction == "BUY" and c["rsi"] > 55:
        score += 10; reasons.append("RSI Bull")
    elif direction == "SELL" and c["rsi"] < 45:
        score += 10; reasons.append("RSI Bear")

    if score < 70:
        return None

    entry = c["close"]

    # Swing SL
    sl = df["low"].tail(5).min() if direction=="BUY" else df["high"].tail(5).max()
    tp = entry + (entry - sl) if direction=="BUY" else entry - (sl - entry)

    return {
        "symbol": symbol,
        "side": direction,
        "entry": entry,
        "sl": sl,
        "tp": tp,
        "score": score,
        "reasons": reasons
    }

# ==========================================================
# EXECUTION
def balance():
    for x in client.futures_account_balance():
        if x["asset"] == "USDT":
            return float(x["balance"])

def price(symbol):
    return float(client.get_symbol_ticker(symbol=symbol)["price"])

def order(symbol, side, qty):
    return client.futures_create_order(
        symbol=symbol,
        side=side,
        type="MARKET",
        quantity=qty
    )

def execute(sig):

    try:
        px = price(sig["symbol"])
        bal = balance()
        pos = bal * CAPITAL * LEVERAGE

        precision = get_precision(sig["symbol"])
        qty = round(pos / px, precision)

        order(sig["symbol"], sig["side"], qty)

        send(
            f"🚀 TRADE EXECUTED\n\n"
            f"{sig['symbol']} {sig['side']}\n"
            f"Entry: {round(px,4)}\n"
            f"SL: {round(sig['sl'],4)}\n"
            f"TP: {round(sig['tp'],4)}\n"
            f"Score: {sig['score']}\n"
            f"Reasons: {', '.join(sig['reasons'])}\n"
            f"🕒 {ts()}"
        )

    except Exception as e:
        send(f"❌ ORDER ERROR {sig['symbol']}: {e}")

# ==========================================================
send(f"✅ SNIPER V7 TEST MODE STARTED\n🕒 {ts()}")

# ==========================================================
while True:

    try:

        btc_dir = btc_trend()

        for s in SYMBOLS:

            sig = signal(s, btc_dir)

            if sig:

                key = f"{sig['symbol']}_{sig['side']}"

                if last_signal.get(sig["symbol"]) == key:
                    continue

                last_signal[sig["symbol"]] = key

                send(
                    f"📊 SNIPER SIGNAL\n\n"
                    f"{s} {sig['side']}\n"
                    f"Score: {sig['score']}\n"
                    f"Entry: {round(sig['entry'],4)}\n"
                    f"SL: {round(sig['sl'],4)}\n"
                    f"TP: {round(sig['tp'],4)}\n"
                    f"Reasons: {', '.join(sig['reasons'])}\n"
                    f"🕒 {ts()}"
                )

                execute(sig)
                break

        time.sleep(WAIT)

    except Exception as e:
        send(f"❌ LOOP ERROR: {e}")
        time.sleep(10)