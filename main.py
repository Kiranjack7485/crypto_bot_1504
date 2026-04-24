# ==========================================================
# TRUE SCALPER V6 - SNIPER EDITION
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

SYMBOLS = ["BTCUSDT","ETHUSDT","SOLUSDT","BNBUSDT","XRPUSDT"]

WAIT = 20
LEVERAGE = 3
TP = 0.6
SL = 0.3
CAPITAL = 0.12

open_trade = None
last_heartbeat = 0
last_trade_time = {}
loss_streak = {}

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

# ==========================================================
# INDICATORS
# ==========================================================
def enrich(df):
    df["ema20"] = df["close"].ewm(span=20).mean()
    df["ema50"] = df["close"].ewm(span=50).mean()

    df["body"] = abs(df["close"] - df["open"])
    df["body_avg"] = df["body"].rolling(20).mean()

    df["vol_avg"] = df["volume"].rolling(20).mean()

    # RSI
    delta = df["close"].diff()
    gain = (delta.where(delta > 0, 0)).rolling(14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
    rs = gain / loss
    df["rsi"] = 100 - (100 / (1 + rs))

    return df

# ==========================================================
# SNIPER SIGNAL
# ==========================================================
def signal(df):

    c = df.iloc[-1]
    p = df.iloc[-2]

    trend_up = c["ema20"] > c["ema50"]
    trend_down = c["ema20"] < c["ema50"]

    strong_body = c["body"] > 1.2 * df["body_avg"].iloc[-1]
    high_volume = c["volume"] > 1.5 * df["vol_avg"].iloc[-1]

    trend_strength = abs(c["ema20"] - c["ema50"]) / c["close"]

    if trend_strength < 0.001:
        return None

    # BUY
    if trend_up and strong_body and high_volume:
        if c["close"] > p["high"] and c["rsi"] < 65:
            return "BUY"

    # SELL
    if trend_down and strong_body and high_volume:
        if c["close"] < p["low"] and c["rsi"] > 35:
            return "SELL"

    return None

# ==========================================================
# PRECISION
# ==========================================================
symbol_filters = {}

def load_filters():
    info = client.futures_exchange_info()
    for s in info["symbols"]:
        symbol = s["symbol"]
        filters = {f["filterType"]: f for f in s["filters"]}
        symbol_filters[symbol] = {
            "step": float(filters["LOT_SIZE"]["stepSize"]),
            "tick": float(filters["PRICE_FILTER"]["tickSize"])
        }

def round_qty(symbol, qty):
    step = symbol_filters[symbol]["step"]
    return round(qty - (qty % step), 8)

def round_price(symbol, price):
    tick = symbol_filters[symbol]["tick"]
    return round(price - (price % tick), 8)

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
        symbol=symbol,
        side=side,
        type="MARKET",
        quantity=qty
    )

def open_pos(symbol, side, px):
    global open_trade

    # cooldown after loss streak
    if symbol in loss_streak and loss_streak[symbol] >= 2:
        if time.time() - last_trade_time.get(symbol, 0) < 1800:
            return

    # anti spam
    if time.time() - last_trade_time.get(symbol, 0) < 600:
        return

    bal = balance()
    pos = bal * CAPITAL * LEVERAGE

    qty = round_qty(symbol, pos / px)

    if qty <= 0:
        return

    try:
        order(symbol, side, qty)
    except Exception as e:
        send(f"❌ ORDER ERROR {symbol}: {str(e)}")
        return

    if side == "BUY":
        tp = px * (1 + TP/100)
        sl = px * (1 - SL/100)
    else:
        tp = px * (1 - TP/100)
        sl = px * (1 + SL/100)

    tp = round_price(symbol, tp)
    sl = round_price(symbol, sl)

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

    last_trade_time[symbol] = time.time()

    send(f"🚀 {symbol} {side} @ {round(px,4)}")

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
            return close("TP", px)
        if px <= t["sl"]:
            return close("SL", px)
    else:
        if px <= t["tp"]:
            return close("TP", px)
        if px >= t["sl"]:
            return close("SL", px)

    if (time.time()-t["time"])/60 > 25:
        close("TIME", px)

def close(reason, px):
    global open_trade

    t = open_trade

    pnl = ((px - t["entry"]) / t["entry"]) * t["pos"]
    if t["side"] == "SELL":
        pnl = -pnl

    try:
        order(t["symbol"], "SELL" if t["side"]=="BUY" else "BUY", t["qty"])
    except:
        return

    sym = t["symbol"]

    if pnl < 0:
        loss_streak[sym] = loss_streak.get(sym, 0) + 1
    else:
        loss_streak[sym] = 0

    send(f"{reason} | {sym} | PnL: {round(pnl,2)}")

    open_trade = None

# ==========================================================
# START
# ==========================================================
load_filters()
send(f"✅ TRUE SCALPER V6 STARTED | {ts()}")

# ==========================================================
# LOOP
# ==========================================================
while True:

    try:
        if time.time() - last_heartbeat > 3600:
            send(f"💓 ACTIVE {ts()}")
            last_heartbeat = time.time()

        if open_trade:
            manage()
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
                send(f"🔥 {s} {sig}")
                open_pos(s, sig, px)
                break

        time.sleep(WAIT)

    except Exception as e:
        send(f"❌ ERROR: {str(e)}")
        time.sleep(10)