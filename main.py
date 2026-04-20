# ==========================================================
# TRUE SCALPER v8 SESSION SNIPER PRO
# Focus:
# Wait for strong long candles (momentum confirmation)
# Enter before ~40% trend expansion (not too early / not too late)
# Multi-timeframe confirmation: 5m + 15m + 1h
# Session restricted sniper execution
# main.py
# ==========================================================

import os
import time
import math
import requests
import pandas as pd
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv
from binance.client import Client

load_dotenv()

# ==========================================================
# ENV
# ==========================================================
API_KEY = os.getenv("API_KEY")
API_SECRET = os.getenv("API_SECRET")
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# ==========================================================
# BINANCE
# ==========================================================
client = Client(API_KEY, API_SECRET)
client.FUTURES_URL = "https://testnet.binancefuture.com/fapi"

# ==========================================================
# CONFIG
# ==========================================================
SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT"]

WAIT = 20
LEVERAGE = 3

BASE_CAPITAL = 0.10
STRONG_CAPITAL = 0.14

TP_PCT = 0.68
SL_PCT = 0.32

MAX_HOLD_MIN = 34
COOLDOWN = 2400

MIN_SCORE = 8
STRONG_SCORE = 10

open_trade = None
last_trade = {}
last_heartbeat = 0

# ==========================================================
# TIME
# ==========================================================
IST = timezone(timedelta(hours=5, minutes=30))

def utc_now():
    return datetime.now(timezone.utc)

def ist_now():
    return utc_now().astimezone(IST)

def ts():
    return f"UTC {utc_now().strftime('%H:%M:%S')} | IST {ist_now().strftime('%H:%M:%S')}"

# ==========================================================
# TELEGRAM / LOG
# ==========================================================
def send(msg):
    print(msg, flush=True)

    if TOKEN and CHAT_ID:
        try:
            requests.post(
                f"https://api.telegram.org/bot{TOKEN}/sendMessage",
                json={"chat_id": CHAT_ID, "text": msg},
                timeout=8
            )
        except:
            pass

# ==========================================================
# SESSION ENGINE
# ==========================================================
def current_session():

    now = ist_now()
    mins = now.hour * 60 + now.minute

    # India momentum
    if 735 <= mins <= 1035:      # 12:15 to 17:15
        return "INDIA"

    # London entry
    if 1065 <= mins <= 1290:     # 17:45 to 21:30
        return "LONDON"

    # US momentum
    if 1290 <= mins <= 1425:     # 21:30 to 23:45
        return "US"

    return None

def allowed_symbols(session):

    if session == "INDIA":
        return ["BTCUSDT", "ETHUSDT", "BNBUSDT"]

    if session == "LONDON":
        return ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"]

    if session == "US":
        return ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"]

    return []

# ==========================================================
# HELPERS
# ==========================================================
def balance():
    try:
        for row in client.futures_account_balance():
            if row["asset"] == "USDT":
                return float(row["balance"])
    except:
        return 0
    return 0

def price(symbol):
    try:
        return float(client.get_symbol_ticker(symbol=symbol)["price"])
    except:
        return None

def klines(symbol, interval="5m", limit=220):
    try:
        return client.get_klines(
            symbol=symbol,
            interval=interval,
            limit=limit
        )
    except:
        return None

def set_leverage(symbol):
    try:
        client.futures_change_leverage(symbol=symbol, leverage=LEVERAGE)
    except:
        pass

def step(symbol):
    try:
        info = client.futures_exchange_info()
        for s in info["symbols"]:
            if s["symbol"] == symbol:
                for f in s["filters"]:
                    if f["filterType"] == "LOT_SIZE":
                        return float(f["stepSize"])
    except:
        return 0.001
    return 0.001

def floor_qty(qty, st):
    return math.floor(qty / st) * st

def market(symbol, side, qty):
    try:
        return client.futures_create_order(
            symbol=symbol,
            side=side,
            type="MARKET",
            quantity=qty
        )
    except:
        return None

# ==========================================================
# DATAFRAME
# ==========================================================
def frame(raw):
    df = pd.DataFrame(raw, columns=[
        "time","open","high","low","close","volume",
        "ct","qv","n","tb","tq","ig"
    ])
    df = df[["open","high","low","close","volume"]].astype(float)
    return df

def enrich(df):

    df["ema20"] = df["close"].ewm(span=20).mean()
    df["ema50"] = df["close"].ewm(span=50).mean()

    delta = df["close"].diff()

    up = delta.clip(lower=0)
    down = -delta.clip(upper=0)

    gain = up.rolling(14).mean()
    loss = down.rolling(14).mean()

    rs = gain / loss
    df["rsi"] = 100 - (100 / (1 + rs))

    df["body"] = (df["close"] - df["open"]).abs()
    df["range"] = df["high"] - df["low"]

    df["body_avg"] = df["body"].rolling(20).mean()
    df["vol_avg"] = df["volume"].rolling(20).mean()

    return df

# ==========================================================
# TREND CHECKS
# ==========================================================
def tf_trend(symbol, interval):

    raw = klines(symbol, interval, 150)
    if not raw:
        return None

    df = enrich(frame(raw))
    c = df.iloc[-1]

    if c["ema20"] > c["ema50"]:
        return "UP"

    if c["ema20"] < c["ema50"]:
        return "DOWN"

    return "SIDE"

# ==========================================================
# LONG CANDLE TREND SNIPER
# ==========================================================
def sniper_signal(df, t15, t1h):

    c = df.iloc[-1]
    p = df.iloc[-2]

    # candle quality
    body_ratio = c["body"] / c["range"] if c["range"] > 0 else 0
    long_body = c["body"] > c["body_avg"] * 1.45
    volume_push = c["volume"] > c["vol_avg"] * 1.15

    # ======================================================
    # BUY
    # ======================================================
    if t15 == "UP" and t1h == "UP":

        score = 0

        if c["ema20"] > c["ema50"]:
            score += 2

        if c["close"] > p["high"]:
            score += 2

        if long_body:
            score += 2

        if body_ratio > 0.62:
            score += 1

        if volume_push:
            score += 1

        if 54 <= c["rsi"] <= 67:
            score += 1

        # not too late = not more than 40% stretched move
        stretch = (c["close"] - c["ema20"]) / c["ema20"]

        if stretch < 0.0045:
            score += 1
        elif stretch > 0.010:
            score -= 3

        if score >= MIN_SCORE:
            return ("BUY", min(score,10))

    # ======================================================
    # SELL
    # ======================================================
    if t15 == "DOWN" and t1h == "DOWN":

        score = 0

        if c["ema20"] < c["ema50"]:
            score += 2

        if c["close"] < p["low"]:
            score += 2

        if long_body:
            score += 2

        if body_ratio > 0.62:
            score += 1

        if volume_push:
            score += 1

        if 33 <= c["rsi"] <= 46:
            score += 1

        stretch = (c["ema20"] - c["close"]) / c["ema20"]

        if stretch < 0.0045:
            score += 1
        elif stretch > 0.010:
            score -= 3

        if score >= MIN_SCORE:
            return ("SELL", min(score,10))

    return None

# ==========================================================
# ENTRY
# ==========================================================
def open_position(symbol, side, px, score):
    global open_trade

    bal = balance()

    use = STRONG_CAPITAL if score >= STRONG_SCORE else BASE_CAPITAL

    margin = bal * use
    pos = margin * LEVERAGE

    qty = floor_qty(pos / px, step(symbol))
    if qty <= 0:
        return

    set_leverage(symbol)

    if not market(symbol, side, qty):
        send(f"❌ ORDER FAILED {symbol}")
        return

    if side == "BUY":
        tp = px * (1 + TP_PCT / 100)
        sl = px * (1 - SL_PCT / 100)
    else:
        tp = px * (1 - TP_PCT / 100)
        sl = px * (1 + SL_PCT / 100)

    open_trade = {
        "symbol": symbol,
        "side": side,
        "entry": px,
        "tp": tp,
        "sl": sl,
        "qty": qty,
        "pos": pos,
        "time": time.time()
    }

    send(
        f"✅ ENTRY EXECUTED\n\n"
        f"Coin: {symbol}\n"
        f"Side: {side}\n"
        f"Entry: {round(px,4)}\n"
        f"TP: {round(tp,4)}\n"
        f"SL: {round(sl,4)}\n"
        f"Score: {score}/10\n"
        f"Mode: Session Sniper Pro\n"
        f"🕒 {ts()}"
    )

# ==========================================================
# EXIT
# ==========================================================
def close_position(reason, px):
    global open_trade

    t = open_trade
    if not t:
        return

    if t["side"] == "BUY":
        pnl = ((px - t["entry"]) / t["entry"]) * t["pos"]
        close_side = "SELL"
    else:
        pnl = ((t["entry"] - px) / t["entry"]) * t["pos"]
        close_side = "BUY"

    market(t["symbol"], close_side, t["qty"])

    mins = int((time.time() - t["time"]) / 60)

    send(
        f"{'🎯' if pnl >=0 else '🛑'} {reason}\n\n"
        f"{t['symbol']} {t['side']}\n"
        f"Entry: {round(t['entry'],4)}\n"
        f"Exit: {round(px,4)}\n"
        f"PnL: ${round(pnl,2)}\n"
        f"Held: {mins} mins\n"
        f"🕒 {ts()}"
    )

    open_trade = None

def manage_trade():

    if not open_trade:
        return

    px = price(open_trade["symbol"])
    if not px:
        return

    t = open_trade

    if t["side"] == "BUY":
        if px >= t["tp"]:
            close_position("TAKE PROFIT HIT", px)
            return
        if px <= t["sl"]:
            close_position("STOP LOSS HIT", px)
            return
    else:
        if px <= t["tp"]:
            close_position("TAKE PROFIT HIT", px)
            return
        if px >= t["sl"]:
            close_position("STOP LOSS HIT", px)
            return

    if int((time.time() - t["time"]) / 60) >= MAX_HOLD_MIN:
        close_position("SMART EXIT", px)

# ==========================================================
# STARTUP
# ==========================================================
send(
    f"🚀 TRUE SCALPER v8 SESSION SNIPER PRO STARTED\n"
    f"Balance: ${round(balance(),2)}\n"
    f"Strategy: Long Candle Trend Confirmation\n"
    f"TF Align: 5m + 15m + 1H\n"
    f"Session Restricted Enabled\n"
    f"🕒 {ts()}"
)

# ==========================================================
# LOOP
# ==========================================================
while True:

    try:
        if time.time() - last_heartbeat > 3600:
            sess = current_session() or "WAITING"
            send(
                f"💓 BOT ACTIVE\n"
                f"Session: {sess}\n"
                f"Open Trade: {'YES' if open_trade else 'NO'}\n"
                f"🕒 {ts()}"
            )
            last_heartbeat = time.time()

        if open_trade:
            manage_trade()
            time.sleep(WAIT)
            continue

        session = current_session()

        if not session:
            time.sleep(WAIT)
            continue

        watchlist = allowed_symbols(session)

        for symbol in watchlist:

            if symbol in last_trade:
                if time.time() - last_trade[symbol] < COOLDOWN:
                    continue

            try:
                raw = klines(symbol, "5m", 220)
                if not raw:
                    continue

                df = enrich(frame(raw))

                t15 = tf_trend(symbol, "15m")
                t1h = tf_trend(symbol, "1h")

                if not t15 or not t1h:
                    continue

                sig = sniper_signal(df, t15, t1h)

                if sig:

                    side, score = sig
                    px = float(df.iloc[-1]["close"])

                    send(
                        f"🔥 GOLDEN SESSION SIGNAL\n\n"
                        f"Coin: {symbol}\n"
                        f"Direction: {side}\n"
                        f"Price: {round(px,4)}\n"
                        f"Score: {score}/10\n"
                        f"15m: {t15}\n"
                        f"1H: {t1h}\n"
                        f"Session: {session}\n"
                        f"🕒 {ts()}"
                    )

                    open_position(symbol, side, px, score)

                    last_trade[symbol] = time.time()
                    break

            except:
                pass

        time.sleep(WAIT)

    except:
        send("❌ AUTO RECOVERY")
        time.sleep(10)