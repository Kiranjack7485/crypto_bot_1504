# ==========================================================
# TRUE SCALPER v5 EARLY TREND SNIPER
# Precision upgrade:
# Enters at beginning / mid trend, avoids late breakout traps
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
from strategy import indicators

load_dotenv()

# ==========================================================
# ENV
# ==========================================================
API_KEY = os.getenv("API_KEY")
API_SECRET = os.getenv("API_SECRET")
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# ==========================================================
# CLIENT
# ==========================================================
client = Client(API_KEY, API_SECRET)
client.FUTURES_URL = "https://testnet.binancefuture.com/fapi"

# ==========================================================
# CONFIG
# ==========================================================
SYMBOLS = ["BTCUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT"]

WAIT = 20
LEVERAGE = 3

BASE_CAPITAL = 0.10
STRONG_CAPITAL = 0.14

TP_PCT = 0.65
SL_PCT = 0.30

MAX_HOLD_MIN = 35
COOLDOWN = 2700

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
# LOG + TELEGRAM
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
# SESSION (24x7 sniper but priority windows)
# ==========================================================
def session_name():
    m = utc_now().hour * 60 + utc_now().minute

    if 420 <= m <= 720:
        return "INDIA PRIORITY"

    if 780 <= m <= 1080:
        return "LONDON/US PRIORITY"

    return "BACKGROUND WATCH"

# ==========================================================
# BINANCE HELPERS
# ==========================================================
def balance():
    try:
        for x in client.futures_account_balance():
            if x["asset"] == "USDT":
                return float(x["balance"])
    except:
        return 0
    return 0

def price(symbol):
    try:
        return float(client.get_symbol_ticker(symbol=symbol)["price"])
    except:
        return None

def klines(symbol):
    try:
        return client.get_klines(
            symbol=symbol,
            interval="5m",
            limit=150
        )
    except:
        return None

def leverage(symbol):
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
# INDICATORS
# ==========================================================
def enrich(df):

    df["ema9"] = df["close"].ewm(span=9).mean()
    df["ema20"] = df["close"].ewm(span=20).mean()
    df["ema50"] = df["close"].ewm(span=50).mean()

    delta = df["close"].diff()

    up = delta.clip(lower=0)
    down = -delta.clip(upper=0)

    avg_gain = up.rolling(14).mean()
    avg_loss = down.rolling(14).mean()

    rs = avg_gain / avg_loss
    df["rsi"] = 100 - (100 / (1 + rs))

    df["vol_avg"] = df["volume"].rolling(20).mean()

    return df

# ==========================================================
# EARLY TREND SNIPER LOGIC
# ==========================================================
def sniper_signal(df):

    c = df.iloc[-1]
    p = df.iloc[-2]
    p2 = df.iloc[-3]

    # ------------------------------------
    # LONG SETUP
    # Trend already exists but not stretched
    # Pullback happened
    # Fresh momentum restarting
    # ------------------------------------
    if c["ema20"] > c["ema50"]:

        pullback = p["close"] <= p["ema9"] or p["close"] <= p["ema20"]

        restart = c["close"] > p["high"]

        healthy_rsi = 52 <= c["rsi"] <= 64

        volume = c["volume"] > c["vol_avg"] * 1.12

        not_stretched = ((c["close"] - c["ema20"]) / c["ema20"]) < 0.006

        if pullback and restart and healthy_rsi and volume and not_stretched:
            score = 8

            if c["volume"] > c["vol_avg"] * 1.35:
                score = 9

            return ("BUY", score)

    # ------------------------------------
    # SHORT SETUP
    # ------------------------------------
    if c["ema20"] < c["ema50"]:

        pullback = p["close"] >= p["ema9"] or p["close"] >= p["ema20"]

        restart = c["close"] < p["low"]

        healthy_rsi = 36 <= c["rsi"] <= 48

        volume = c["volume"] > c["vol_avg"] * 1.12

        not_stretched = ((c["ema20"] - c["close"]) / c["ema20"]) < 0.006

        if pullback and restart and healthy_rsi and volume and not_stretched:
            score = 8

            if c["volume"] > c["vol_avg"] * 1.35:
                score = 9

            return ("SELL", score)

    return None

# ==========================================================
# ENTRY
# ==========================================================
def create_trade(symbol, side, px, score):
    global open_trade

    bal = balance()

    use = STRONG_CAPITAL if score >= 9 else BASE_CAPITAL

    margin = bal * use
    pos = margin * LEVERAGE

    qty = floor_qty(pos / px, step(symbol))

    if qty <= 0:
        return

    leverage(symbol)

    order = market(symbol, side, qty)

    if not order:
        send(f"❌ Order Failed {symbol}")
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
        f"Qty: {qty}\n"
        f"Confidence: {score}/10\n"
        f"Mode: Early Trend Sniper\n"
        f"🕒 {ts()}"
    )

# ==========================================================
# EXIT
# ==========================================================
def close_trade(reason, px):
    global open_trade

    t = open_trade
    if not t:
        return

    if t["side"] == "BUY":
        pnl = ((px - t["entry"]) / t["entry"]) * t["pos"]
        side = "SELL"
    else:
        pnl = ((t["entry"] - px) / t["entry"]) * t["pos"]
        side = "BUY"

    market(t["symbol"], side, t["qty"])

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
            close_trade("TAKE PROFIT HIT", px)
            return

        if px <= t["sl"]:
            close_trade("STOP LOSS HIT", px)
            return

    else:

        if px <= t["tp"]:
            close_trade("TAKE PROFIT HIT", px)
            return

        if px >= t["sl"]:
            close_trade("STOP LOSS HIT", px)
            return

    held = int((time.time() - t["time"]) / 60)

    if held >= MAX_HOLD_MIN:
        close_trade("SMART EXIT", px)

# ==========================================================
# STARTUP
# ==========================================================
send(
    f"🚀 TRUE SCALPER v5 EARLY TREND SNIPER STARTED\n"
    f"Balance: ${round(balance(),2)}\n"
    f"Watching 24x7\n"
    f"Objective: Catch start/mid trend only\n"
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
                f"Mode: {session_name()}\n"
                f"🕒 {ts()}"
            )
            last_heartbeat = time.time()

        if open_trade:
            manage_trade()
            time.sleep(WAIT)
            continue

        for symbol in SYMBOLS:

            if symbol in last_trade:
                if time.time() - last_trade[symbol] < COOLDOWN:
                    continue

            try:
                raw = klines(symbol)

                if not raw:
                    continue

                df = pd.DataFrame(raw, columns=[
                    "time","open","high","low","close","volume",
                    "ct","qv","n","tb","tq","ig"
                ])

                df = df[["open","high","low","close","volume"]].astype(float)

                df = enrich(df)

                sig = sniper_signal(df)

                if sig:

                    side, score = sig
                    px = float(df.iloc[-1]["close"])

                    send(
                        f"🔥 GOLDEN SNIPER SIGNAL\n\n"
                        f"Coin: {symbol}\n"
                        f"Direction: {side}\n"
                        f"Price: {round(px,4)}\n"
                        f"Confidence: {score}/10\n"
                        f"Detected: Start/Mid Trend Opportunity\n"
                        f"Session: {session_name()}\n"
                        f"🕒 {ts()}"
                    )

                    create_trade(symbol, side, px, score)

                    last_trade[symbol] = time.time()
                    break

            except:
                pass

        time.sleep(WAIT)

    except:
        send("❌ AUTO RECOVERY")
        time.sleep(10)