# ==========================================================
# TRUE SCALPER v9 – MOMENTUM SESSION HUNTER
# Balanced system:
# - Session restricted (v4 strength)
# - Adaptive scoring (v7 strength)
# - Medium + strong momentum (fix v8)
# - Early-mid trend capture (your core goal)
# ==========================================================

import os, time, math, requests, pandas as pd
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

# ==========================================================
# CONFIG
# ==========================================================
SYMBOLS = ["BTCUSDT","ETHUSDT","SOLUSDT","BNBUSDT","XRPUSDT"]

WAIT = 20
LEVERAGE = 3

BASE_CAPITAL = 0.10
STRONG_CAPITAL = 0.14

TP_PCT = 0.65
SL_PCT = 0.30

MIN_SCORE = 6
STRONG_SCORE = 8

MAX_HOLD_MIN = 30
COOLDOWN = 2400

open_trade = None
last_trade = {}
last_heartbeat = 0

IST = timezone(timedelta(hours=5, minutes=30))

# ==========================================================
# UTIL
# ==========================================================
def ts():
    now = datetime.now(timezone.utc)
    ist = now.astimezone(IST)
    return f"UTC {now.strftime('%H:%M:%S')} | IST {ist.strftime('%H:%M:%S')}"

def send(msg):
    print(msg, flush=True)
    if TOKEN and CHAT_ID:
        try:
            requests.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage",
                          json={"chat_id": CHAT_ID, "text": msg}, timeout=5)
        except: pass

# ==========================================================
# SESSION CONTROL
# ==========================================================
def get_session():
    now = datetime.now(IST)
    mins = now.hour*60 + now.minute

    if 735 <= mins <= 1035: return "INDIA"
    if 1065 <= mins <= 1290: return "LONDON"
    if 1290 <= mins <= 1425: return "US"
    return None

def session_symbols(sess):
    if sess == "INDIA":
        return ["BTCUSDT","ETHUSDT","BNBUSDT"]
    return ["BTCUSDT","ETHUSDT","SOLUSDT","XRPUSDT"]

# ==========================================================
# DATA
# ==========================================================
def klines(symbol, interval, limit=200):
    try:
        return client.get_klines(symbol=symbol, interval=interval, limit=limit)
    except: return None

def frame(raw):
    df = pd.DataFrame(raw, columns=[
        "t","o","h","l","c","v","ct","qv","n","tb","tq","ig"])
    df = df[["o","h","l","c","v"]].astype(float)
    df.columns = ["open","high","low","close","volume"]
    return df

def enrich(df):
    df["ema20"] = df["close"].ewm(span=20).mean()
    df["ema50"] = df["close"].ewm(span=50).mean()

    delta = df["close"].diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()
    rs = gain / loss
    df["rsi"] = 100 - (100/(1+rs))

    df["body"] = abs(df["close"] - df["open"])
    df["body_avg"] = df["body"].rolling(20).mean()
    df["vol_avg"] = df["volume"].rolling(20).mean()

    return df

# ==========================================================
# TREND
# ==========================================================
def trend(symbol, tf):
    raw = klines(symbol, tf, 120)
    if not raw: return None
    df = enrich(frame(raw))
    c = df.iloc[-1]
    if c["ema20"] > c["ema50"]: return "UP"
    if c["ema20"] < c["ema50"]: return "DOWN"
    return "SIDE"

# ==========================================================
# MOMENTUM ENGINE (CORE FIX)
# ==========================================================
def momentum_signal(df, t15, t1h):

    c = df.iloc[-1]
    p = df.iloc[-2]
    p2 = df.iloc[-3]

    # breakout in last 2 candles
    breakout_up = (c["close"] > p["high"]) or (p["close"] > p2["high"])
    breakout_dn = (c["close"] < p["low"]) or (p["close"] < p2["low"])

    body_ok = c["body"] > df["body_avg"].iloc[-1] * 1.18
    volume_ok = c["volume"] > df["vol_avg"].iloc[-1] * 1.05

    score = 0

    # ======================================================
    # BUY
    # ======================================================
    if t15 == "UP" and t1h == "UP":

        score += 2

        if c["ema20"] > c["ema50"]: score += 1
        if breakout_up: score += 2
        if body_ok: score += 1
        if volume_ok: score += 1

        if 50 <= c["rsi"] <= 70: score += 1

        stretch = (c["close"] - c["ema20"]) / c["ema20"]
        if stretch < 0.008: score += 1
        elif stretch > 0.015: score -= 2

        if score >= MIN_SCORE:
            return ("BUY", score)

    # ======================================================
    # SELL
    # ======================================================
    if t15 == "DOWN" and t1h == "DOWN":

        score += 2

        if c["ema20"] < c["ema50"]: score += 1
        if breakout_dn: score += 2
        if body_ok: score += 1
        if volume_ok: score += 1

        if 30 <= c["rsi"] <= 50: score += 1

        stretch = (c["ema20"] - c["close"]) / c["ema20"]
        if stretch < 0.008: score += 1
        elif stretch > 0.015: score -= 2

        if score >= MIN_SCORE:
            return ("SELL", score)

    return None

# ==========================================================
# EXECUTION
# ==========================================================
def price(symbol):
    try: return float(client.get_symbol_ticker(symbol=symbol)["price"])
    except: return None

def balance():
    try:
        for x in client.futures_account_balance():
            if x["asset"] == "USDT":
                return float(x["balance"])
    except: return 0

def step(symbol):
    info = client.futures_exchange_info()
    for s in info["symbols"]:
        if s["symbol"] == symbol:
            for f in s["filters"]:
                if f["filterType"] == "LOT_SIZE":
                    return float(f["stepSize"])
    return 0.001

def qty(symbol, px, capital):
    q = capital / px
    st = step(symbol)
    return math.floor(q/st)*st

def order(symbol, side, q):
    try:
        return client.futures_create_order(
            symbol=symbol, side=side, type="MARKET", quantity=q)
    except: return None

# ==========================================================
# TRADE
# ==========================================================
def open_trade_fn(symbol, side, px, score):
    global open_trade

    bal = balance()
    cap = STRONG_CAPITAL if score >= STRONG_SCORE else BASE_CAPITAL

    margin = bal * cap * LEVERAGE
    q = qty(symbol, px, margin)

    if q <= 0: return

    order(symbol, side, q)

    if side == "BUY":
        tp = px * (1 + TP_PCT/100)
        sl = px * (1 - SL_PCT/100)
    else:
        tp = px * (1 - TP_PCT/100)
        sl = px * (1 + SL_PCT/100)

    open_trade = {
        "symbol": symbol, "side": side,
        "entry": px, "tp": tp, "sl": sl,
        "qty": q, "pos": margin,
        "time": time.time()
    }

    send(f"✅ ENTRY {symbol} {side}\nPrice: {px}\nScore: {score}\n🕒 {ts()}")

def manage_trade():
    global open_trade

    if not open_trade: return

    px = price(open_trade["symbol"])
    if not px: return

    t = open_trade

    if t["side"] == "BUY":
        if px >= t["tp"]: return close("TP", px)
        if px <= t["sl"]: return close("SL", px)
    else:
        if px <= t["tp"]: return close("TP", px)
        if px >= t["sl"]: return close("SL", px)

    if (time.time()-t["time"])/60 > MAX_HOLD_MIN:
        close("TIME EXIT", px)

def close(reason, px):
    global open_trade

    t = open_trade

    pnl = ((px - t["entry"]) / t["entry"]) * t["pos"]
    if t["side"] == "SELL":
        pnl = -pnl

    order(t["symbol"], "SELL" if t["side"]=="BUY" else "BUY", t["qty"])

    send(f"{reason} | PnL: {round(pnl,2)} | {t['symbol']}")

    open_trade = None

# ==========================================================
# START
# ==========================================================
send(f"🚀 v9 MOMENTUM SESSION HUNTER STARTED\n🕒 {ts()}")

# ==========================================================
# LOOP
# ==========================================================
while True:
    try:

        if open_trade:
            manage_trade()
            time.sleep(WAIT)
            continue

        sess = get_session()
        if not sess:
            time.sleep(WAIT)
            continue

        watch = session_symbols(sess)

        for s in watch:

            if s in last_trade and time.time()-last_trade[s] < COOLDOWN:
                continue

            raw = klines(s, "5m", 200)
            if not raw: continue

            df = enrich(frame(raw))

            t15 = trend(s, "15m")
            t1h = trend(s, "1h")

            sig = momentum_signal(df, t15, t1h)

            if sig:
                side, score = sig
                px = df.iloc[-1]["close"]

                send(f"🔥 SIGNAL {s} {side} | Score {score}")
                open_trade_fn(s, side, px, score)

                last_trade[s] = time.time()
                break

        time.sleep(WAIT)

    except:
        send("❌ AUTO RECOVERY")
        time.sleep(10)