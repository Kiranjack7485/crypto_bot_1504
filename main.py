# ==========================================================
# TRUE SCALPER v10 ELITE FILTER SCALPER
# Dual Mode:
#   1) AUTO  -> Binance Futures Testnet demo trades
#   2) MANUAL -> Telegram signals only
#
# Core Philosophy:
# - BTC + ETH only
# - London + US prime sessions only
# - 3 to 4 elite trades/day max
# - Score 8+ only
# - After 2 consecutive losses pause system
# - Fewer trades, stronger edge
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

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# MODE = AUTO / MANUAL
MODE = os.getenv("BOT_MODE", "AUTO").upper()

# ==========================================================
# BINANCE
# ==========================================================
client = Client(API_KEY, API_SECRET)
client.FUTURES_URL = "https://testnet.binancefuture.com/fapi"

# ==========================================================
# CONFIG
# ==========================================================
SYMBOLS = ["BTCUSDT", "ETHUSDT"]

WAIT = 20
LEVERAGE = 3

BASE_CAPITAL = 0.12
STRONG_CAPITAL = 0.16

TP_PCT = 0.72
SL_PCT = 0.30

MIN_SCORE = 8
STRONG_SCORE = 9

MAX_HOLD_MIN = 35
COOLDOWN = 3600

MAX_TRADES_PER_DAY = 4
LOSS_STREAK_LIMIT = 2
PAUSE_AFTER_LOSS_MIN = 45

open_trade = None
last_trade = {}
last_heartbeat = 0

today_date = None
today_trades = 0
loss_streak = 0
pause_until = 0

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

    if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
        try:
            requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                json={"chat_id": TELEGRAM_CHAT_ID, "text": msg},
                timeout=8
            )
        except:
            pass

# ==========================================================
# SESSION ENGINE
# London + US only
# ==========================================================
def current_session():

    now = ist_now()
    mins = now.hour * 60 + now.minute

    # London Entry
    if 1065 <= mins <= 1290:   # 17:45 - 21:30
        return "LONDON"

    # US Momentum
    if 1290 <= mins <= 1425:   # 21:30 - 23:45
        return "US"

    return None

# ==========================================================
# HELPERS
# ==========================================================
def reset_daily():
    global today_date, today_trades, loss_streak

    d = ist_now().strftime("%Y-%m-%d")

    if today_date != d:
        today_date = d
        today_trades = 0
        loss_streak = 0

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
        return client.get_klines(symbol=symbol, interval=interval, limit=limit)
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
# DATA
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
# TREND
# ==========================================================
def trend(symbol, tf):

    raw = klines(symbol, tf, 160)
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
# ELITE FILTER SIGNAL ENGINE
# ==========================================================
def elite_signal(df, t15, t1h):

    c = df.iloc[-1]
    p = df.iloc[-2]
    p2 = df.iloc[-3]

    body_ratio = c["body"] / c["range"] if c["range"] > 0 else 0

    body_good = c["body"] > c["body_avg"] * 1.22
    vol_good = c["volume"] > c["vol_avg"] * 1.08

    breakout_up = (c["close"] > p["high"]) or (p["close"] > p2["high"])
    breakout_dn = (c["close"] < p["low"]) or (p["close"] < p2["low"])

    # ======================================================
    # BUY
    # ======================================================
    if t15 == "UP" and t1h == "UP":

        score = 0

        score += 2

        if c["ema20"] > c["ema50"]:
            score += 2

        if breakout_up:
            score += 2

        if body_good:
            score += 1

        if vol_good:
            score += 1

        if body_ratio > 0.60:
            score += 1

        if 52 <= c["rsi"] <= 68:
            score += 1

        stretch = (c["close"] - c["ema20"]) / c["ema20"]

        if stretch > 0.012:
            score -= 3

        if score >= MIN_SCORE:
            return ("BUY", min(score,10))

    # ======================================================
    # SELL
    # ======================================================
    if t15 == "DOWN" and t1h == "DOWN":

        score = 0

        score += 2

        if c["ema20"] < c["ema50"]:
            score += 2

        if breakout_dn:
            score += 2

        if body_good:
            score += 1

        if vol_good:
            score += 1

        if body_ratio > 0.60:
            score += 1

        if 32 <= c["rsi"] <= 48:
            score += 1

        stretch = (c["ema20"] - c["close"]) / c["ema20"]

        if stretch > 0.012:
            score -= 3

        if score >= MIN_SCORE:
            return ("SELL", min(score,10))

    return None

# ==========================================================
# ENTRY
# ==========================================================
def open_position(symbol, side, px, score):
    global open_trade, today_trades

    bal = balance()

    use = STRONG_CAPITAL if score >= STRONG_SCORE else BASE_CAPITAL

    margin = bal * use
    pos = margin * LEVERAGE

    qty = floor_qty(pos / px, step(symbol))

    if qty <= 0:
        return

    if side == "BUY":
        tp = px * (1 + TP_PCT / 100)
        sl = px * (1 - SL_PCT / 100)
    else:
        tp = px * (1 - TP_PCT / 100)
        sl = px * (1 + SL_PCT / 100)

    # ======================================================
    # MANUAL MODE ONLY SIGNAL
    # ======================================================
    if MODE == "MANUAL":

        send(
            f"📢 MANUAL TRADE SIGNAL\n\n"
            f"Coin: {symbol}\n"
            f"Trend: {side}\n"
            f"Entry: {round(px,4)}\n"
            f"Book Profit: {round(tp,4)}\n"
            f"Stop Loss: {round(sl,4)}\n"
            f"Leverage: {LEVERAGE}x\n"
            f"Confidence: {score}/10\n"
            f"Session Sniper Elite\n"
            f"🕒 {ts()}"
        )

        today_trades += 1
        return

    # ======================================================
    # AUTO MODE
    # ======================================================
    set_leverage(symbol)

    order = market(symbol, side, qty)

    if not order:
        send(f"❌ ORDER FAILED {symbol}")
        return

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

    today_trades += 1

    send(
        f"✅ AUTO ENTRY EXECUTED\n\n"
        f"Coin: {symbol}\n"
        f"Side: {side}\n"
        f"Entry: {round(px,4)}\n"
        f"TP: {round(tp,4)}\n"
        f"SL: {round(sl,4)}\n"
        f"Leverage: {LEVERAGE}x\n"
        f"Capital Used: ${round(margin,2)}\n"
        f"Score: {score}/10\n"
        f"🕒 {ts()}"
    )

# ==========================================================
# EXIT
# ==========================================================
def close_position(reason, px):
    global open_trade, loss_streak, pause_until

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

    if pnl < 0:
        loss_streak += 1
    else:
        loss_streak = 0

    if loss_streak >= LOSS_STREAK_LIMIT:
        pause_until = time.time() + (PAUSE_AFTER_LOSS_MIN * 60)

        send(
            f"⏸️ LOSS PROTECTION ENABLED\n"
            f"Paused for {PAUSE_AFTER_LOSS_MIN} mins\n"
            f"🕒 {ts()}"
        )

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

    held = int((time.time() - t["time"]) / 60)

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

    if held >= MAX_HOLD_MIN and held >= 15:
        close_position("SMART EXIT", px)

# ==========================================================
# STARTUP
# ==========================================================
send(
    f"🚀 TRUE SCALPER v10 ELITE FILTER STARTED\n"
    f"Mode: {MODE}\n"
    f"Balance: ${round(balance(),2)}\n"
    f"Watching: BTC + ETH Only\n"
    f"Sessions: London + US\n"
    f"Max Trades/Day: {MAX_TRADES_PER_DAY}\n"
    f"🕒 {ts()}"
)

# ==========================================================
# LOOP
# ==========================================================
while True:

    try:
        reset_daily()

        if time.time() - last_heartbeat > 3600:
            send(
                f"💓 BOT ACTIVE\n"
                f"Mode: {MODE}\n"
                f"Trades Today: {today_trades}/{MAX_TRADES_PER_DAY}\n"
                f"Open Trade: {'YES' if open_trade else 'NO'}\n"
                f"🕒 {ts()}"
            )
            last_heartbeat = time.time()

        if time.time() < pause_until:
            time.sleep(WAIT)
            continue

        if open_trade:
            manage_trade()
            time.sleep(WAIT)
            continue

        if today_trades >= MAX_TRADES_PER_DAY:
            time.sleep(WAIT)
            continue

        session = current_session()

        if not session:
            time.sleep(WAIT)
            continue

        for symbol in SYMBOLS:

            if symbol in last_trade:
                if time.time() - last_trade[symbol] < COOLDOWN:
                    continue

            try:
                raw = klines(symbol, "5m", 220)
                if not raw:
                    continue

                df = enrich(frame(raw))

                t15 = trend(symbol, "15m")
                t1h = trend(symbol, "1h")

                if not t15 or not t1h:
                    continue

                sig = elite_signal(df, t15, t1h)

                if sig:

                    side, score = sig
                    px = float(df.iloc[-1]["close"])

                    send(
                        f"🔥 ELITE SIGNAL FOUND\n\n"
                        f"Coin: {symbol}\n"
                        f"Direction: {side}\n"
                        f"Price: {round(px,4)}\n"
                        f"Score: {score}/10\n"
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