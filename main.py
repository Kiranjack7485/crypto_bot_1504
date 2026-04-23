# ==========================================================
# TRUE SCALPER v4 - CLEAN PRO VERSION
# Strategy unchanged (your best performer)
# Only improvements:
# - Clean logs
# - Structured Telegram alerts
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

SYMBOLS = ["BTCUSDT","ETHUSDT","SOLUSDT","BNBUSDT","XRPUSDT"]

WAIT = 20
LEVERAGE = 3

TP = 0.6
SL = 0.3

CAPITAL = 0.12

open_trade = None
last_heartbeat = 0

IST = timezone(timedelta(hours=5, minutes=30))

# ==========================================================
# TIME
# ==========================================================
def ts():
    now = datetime.now(timezone.utc)
    ist = now.astimezone(IST)
    return f"{now.strftime('%H:%M:%S')} UTC | {ist.strftime('%H:%M:%S')} IST"

# ==========================================================
# TELEGRAM
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
        except:
            pass

# ==========================================================
# SESSION
# ==========================================================
def session():

    mins = datetime.now(IST).hour * 60 + datetime.now(IST).minute

    if 1065 <= mins <= 1425:
        return "ACTIVE"
    return None

# ==========================================================
# DATA
# ==========================================================
def klines(symbol):
    try:
        return client.get_klines(symbol=symbol, interval="5m", limit=120)
    except:
        return None

def frame(raw):
    df = pd.DataFrame(raw, columns=[
        "t","o","h","l","c","v","ct","qv","n","tb","tq","ig"])
    df = df[["o","h","l","c","v"]].astype(float)
    df.columns = ["open","high","low","close","volume"]
    return df

def enrich(df):
    df["ema20"] = df["close"].ewm(span=20).mean()
    df["ema50"] = df["close"].ewm(span=50).mean()

    df["body"] = abs(df["close"] - df["open"])
    df["body_avg"] = df["body"].rolling(20).mean()

    return df

# ==========================================================
# SIGNAL (UNCHANGED CORE)
# ==========================================================
def signal(df):

    c = df.iloc[-1]
    p = df.iloc[-2]

    if c["ema20"] > c["ema50"]:
        if c["close"] > p["high"] and c["body"] > df["body_avg"].iloc[-1]:
            return "BUY"

    if c["ema20"] < c["ema50"]:
        if c["close"] < p["low"] and c["body"] > df["body_avg"].iloc[-1]:
            return "SELL"

    return None

# ==========================================================
# EXECUTION
# ==========================================================
def price(symbol):
    return float(client.get_symbol_ticker(symbol=symbol)["price"])

def balance():
    for x in client.futures_account_balance():
        if x["asset"] == "USDT":
            return float(x["balance"])

def order(symbol, side, qty):
    return client.futures_create_order(
        symbol=symbol, side=side, type="MARKET", quantity=qty)

def open_pos(symbol, side, px):

    global open_trade

    bal = balance()
    pos = bal * CAPITAL * LEVERAGE
    qty = round(pos / px, 3)

    order(symbol, side, qty)

    if side == "BUY":
        tp = px * (1 + TP/100)
        sl = px * (1 - SL/100)
    else:
        tp = px * (1 - TP/100)
        sl = px * (1 + SL/100)

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
        f"🚀 ENTRY EXECUTED\n\n"
        f"{symbol} | {side}\n"
        f"Entry: {round(px,4)}\n"
        f"TP: {round(tp,4)}\n"
        f"SL: {round(sl,4)}\n"
        f"Capital: ${round(pos,2)}\n"
        f"Leverage: {LEVERAGE}x\n"
        f"🕒 {ts()}"
    )

# ==========================================================
# EXIT
# ==========================================================
def manage():

    global open_trade

    if not open_trade:
        return

    px = price(open_trade["symbol"])
    t = open_trade

    if t["side"] == "BUY":
        if px >= t["tp"]:
            return close("TP HIT", px)
        if px <= t["sl"]:
            return close("SL HIT", px)

    else:
        if px <= t["tp"]:
            return close("TP HIT", px)
        if px >= t["sl"]:
            return close("SL HIT", px)

    if (time.time()-t["time"])/60 > 30:
        close("TIME EXIT", px)

def close(reason, px):

    global open_trade

    t = open_trade

    pnl = ((px - t["entry"]) / t["entry"]) * t["pos"]
    if t["side"] == "SELL":
        pnl = -pnl

    order(t["symbol"], "SELL" if t["side"]=="BUY" else "BUY", t["qty"])

    send(
        f"{'🎯' if pnl>=0 else '🛑'} {reason}\n\n"
        f"{t['symbol']} {t['side']}\n"
        f"Entry: {round(t['entry'],4)}\n"
        f"Exit: {round(px,4)}\n"
        f"PnL: ${round(pnl,2)}\n"
        f"🕒 {ts()}"
    )

    open_trade = None

# ==========================================================
# START
# ==========================================================
send(f"✅ TRUE SCALPER v4 STARTED\n🕒 {ts()}")

# ==========================================================
# LOOP
# ==========================================================
while True:

    try:

        if time.time() - last_heartbeat > 3600:
            send(f"💓 BOT ACTIVE | {ts()}")
            last_heartbeat = time.time()

        if open_trade:
            manage()
            time.sleep(WAIT)
            continue

        if not session():
            time.sleep(WAIT)
            continue

        for s in SYMBOLS:

            raw = klines(s)
            if not raw:
                continue

            df = enrich(frame(raw))
            sig = signal(df)

            if sig:
                px = df.iloc[-1]["close"]

                send(
                    f"🔥 SIGNAL DETECTED\n"
                    f"{s} | {sig}\n"
                    f"Price: {round(px,4)}\n"
                    f"🕒 {ts()}"
                )

                open_pos(s, sig, px)
                break

        time.sleep(WAIT)

    except:
        send("❌ ERROR RECOVERY")
        time.sleep(10)