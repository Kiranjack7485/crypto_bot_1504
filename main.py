# ==========================================================
# TRUE SCALPER v7 ADAPTIVE SNIPER ENGINE
# Practical sniper version:
# Weighted scoring instead of zero-signal hard filters
# Watches 24x7 | BTC ETH SOL BNB XRP
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

TP_PCT = 0.62
SL_PCT = 0.30

MAX_HOLD_MIN = 32
COOLDOWN = 2400

MIN_SCORE = 7
STRONG_SCORE = 9

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
# LOG / TELEGRAM
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

def klines(symbol, interval="5m", limit=180):
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

    df["ema9"] = df["close"].ewm(span=9).mean()
    df["ema20"] = df["close"].ewm(span=20).mean()
    df["ema50"] = df["close"].ewm(span=50).mean()

    delta = df["close"].diff()

    up = delta.clip(lower=0)
    down = -delta.clip(upper=0)

    gain = up.rolling(14).mean()
    loss = down.rolling(14).mean()

    rs = gain / loss
    df["rsi"] = 100 - (100 / (1 + rs))

    df["vol_avg"] = df["volume"].rolling(20).mean()

    df["body"] = (df["close"] - df["open"]).abs()

    return df

# ==========================================================
# TREND FILTER
# ==========================================================
def trend_15m(symbol):

    raw = klines(symbol, "15m", 120)
    if not raw:
        return None

    df = enrich(frame(raw))
    c = df.iloc[-1]

    if c["ema20"] > c["ema50"]:
        return "UP"

    if c["ema20"] < c["ema50"]:
        return "DOWN"

    return "SIDE"

def btc_bias():
    t = trend_15m("BTCUSDT")
    return t if t else "SIDE"

# ==========================================================
# ADAPTIVE SCORE ENGINE
# ==========================================================
def score_signal(df, trend, market_bias):

    c = df.iloc[-1]
    p = df.iloc[-2]

    # lookback for pullback in last 3 candles
    last3 = df.iloc[-4:-1]

    # ======================================================
    # BUY SCORE
    # ======================================================
    if trend == "UP":

        score = 0

        # trend align
        score += 2

        # local EMA alignment
        if c["ema20"] > c["ema50"]:
            score += 1

        # pullback seen recently
        if any(last3["close"] <= last3["ema9"]) or any(last3["close"] <= last3["ema20"]):
            score += 2

        # breakout restart
        if c["close"] > p["high"]:
            score += 2

        # RSI healthy
        if 50 <= c["rsi"] <= 68:
            score += 1

        # volume
        if c["volume"] > c["vol_avg"] * 1.05:
            score += 1

        # body strength
        if c["body"] >= p["body"]:
            score += 1

        # BTC supportive
        if market_bias == "UP":
            score += 1

        # stretched penalty
        if ((c["close"] - c["ema20"]) / c["ema20"]) > 0.010:
            score -= 2

        if score >= MIN_SCORE:
            return ("BUY", min(score,10))

    # ======================================================
    # SELL SCORE
    # ======================================================
    if trend == "DOWN":

        score = 0

        score += 2

        if c["ema20"] < c["ema50"]:
            score += 1

        if any(last3["close"] >= last3["ema9"]) or any(last3["close"] >= last3["ema20"]):
            score += 2

        if c["close"] < p["low"]:
            score += 2

        if 32 <= c["rsi"] <= 50:
            score += 1

        if c["volume"] > c["vol_avg"] * 1.05:
            score += 1

        if c["body"] >= p["body"]:
            score += 1

        if market_bias == "DOWN":
            score += 1

        if ((c["ema20"] - c["close"]) / c["ema20"]) > 0.010:
            score -= 2

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

    order = market(symbol, side, qty)
    if not order:
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
        f"Confidence Score: {score}/10\n"
        f"Leverage: {LEVERAGE}x\n"
        f"Mode: Adaptive Sniper Engine\n"
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

    held = int((time.time() - t["time"]) / 60)

    if held >= MAX_HOLD_MIN:
        close_position("SMART EXIT", px)

# ==========================================================
# STARTUP
# ==========================================================
send(
    f"🚀 TRUE SCALPER v7 ADAPTIVE SNIPER ENGINE STARTED\n"
    f"Balance: ${round(balance(),2)}\n"
    f"Watching: BTC ETH SOL BNB XRP\n"
    f"Logic: Weighted Score Probability Model\n"
    f"🕒 {ts()}"
)

# ==========================================================
# LOOP
# ==========================================================
while True:

    try:
        if time.time() - last_heartbeat > 3600:
            send(
                f"💓 BOT ACTIVE\n"
                f"Open Trade: {'YES' if open_trade else 'NO'}\n"
                f"🕒 {ts()}"
            )
            last_heartbeat = time.time()

        if open_trade:
            manage_trade()
            time.sleep(WAIT)
            continue

        bias = btc_bias()

        for symbol in SYMBOLS:

            if symbol in last_trade:
                if time.time() - last_trade[symbol] < COOLDOWN:
                    continue

            try:
                raw = klines(symbol, "5m", 180)
                if not raw:
                    continue

                df = enrich(frame(raw))
                trend = trend_15m(symbol)

                if not trend:
                    continue

                sig = score_signal(df, trend, bias)

                if sig:

                    side, score = sig
                    px = float(df.iloc[-1]["close"])

                    send(
                        f"🔥 GOLDEN ADAPTIVE SIGNAL\n\n"
                        f"Coin: {symbol}\n"
                        f"Direction: {side}\n"
                        f"Price: {round(px,4)}\n"
                        f"Confidence: {score}/10\n"
                        f"15m Trend: {trend}\n"
                        f"BTC Bias: {bias}\n"
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