# ==========================================================
# SNIPER V7.1 FINAL - TEST MODE + ANALYTICS + PRECISION FIX
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

SYMBOLS = ["BTCUSDT","ETHUSDT","BNBUSDT","SOLUSDT","XRPUSDT"]

LEVERAGE = 3
CAPITAL = 0.10
TP = 0.8
SL = 0.4
WAIT = 15

open_trade = None
last_signal = {}

stats = {
    "total": 0,
    "wins": 0,
    "loss": 0,
    "pnl": 0.0
}

IST = timezone(timedelta(hours=5, minutes=30))

# ================= TIME =================
def ts():
    now = datetime.now(timezone.utc)
    ist = now.astimezone(IST)
    return f"{now.strftime('%H:%M:%S')} | {ist.strftime('%H:%M:%S')} IST"

# ================= TELEGRAM =================
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

# ================= DATA =================
def get_klines(symbol, tf):
    return client.get_klines(symbol=symbol, interval=tf, limit=100)

def df_format(raw):
    df = pd.DataFrame(raw, columns=["t","o","h","l","c","v","ct","qv","n","tb","tq","ig"])
    df = df[["o","h","l","c","v"]].astype(float)
    df.columns = ["open","high","low","close","volume"]
    return df

def indicators(df):
    df["ema20"] = df["close"].ewm(span=20).mean()
    df["ema50"] = df["close"].ewm(span=50).mean()

    delta = df["close"].diff()
    gain = (delta.where(delta > 0, 0)).rolling(14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
    rs = gain / loss
    df["rsi"] = 100 - (100 / (1 + rs))

    return df

# ================= SIGNAL =================
def analyze(symbol):

    df15 = indicators(df_format(get_klines(symbol,"15m")))
    df5  = indicators(df_format(get_klines(symbol,"5m")))

    c = df15.iloc[-1]
    p = df15.iloc[-2]

    score = 0
    reasons = []

    if c["ema20"] > c["ema50"]:
        trend = "UP"
        score += 30
    else:
        trend = "DOWN"
        score += 30

    if trend == "UP" and c["close"] > p["high"]:
        score += 20
        reasons.append("Breakout")
    elif trend == "DOWN" and c["close"] < p["low"]:
        score += 20
        reasons.append("Breakdown")

    if 50 < c["rsi"] < 70:
        score += 15
        reasons.append("RSI Strength")

    if c["volume"] > df15["volume"].rolling(20).mean().iloc[-1]:
        score += 15
        reasons.append("Volume Spike")

    c5 = df5.iloc[-1]
    if trend == "UP" and c5["ema20"] > c5["ema50"]:
        score += 20
    elif trend == "DOWN" and c5["ema20"] < c5["ema50"]:
        score += 20

    if score >= 70:
        side = "BUY" if trend == "UP" else "SELL"
        return side, score, reasons

    return None, score, []

# ================= EXECUTION =================
def price(symbol):
    return float(client.get_symbol_ticker(symbol=symbol)["price"])

def balance():
    for x in client.futures_account_balance():
        if x["asset"] == "USDT":
            return float(x["balance"])

def get_qty(symbol, px):

    info = client.futures_exchange_info()
    symbol_info = next(s for s in info["symbols"] if s["symbol"] == symbol)

    lot_filter = next(f for f in symbol_info["filters"] if f["filterType"] == "LOT_SIZE")

    step_size = float(lot_filter["stepSize"])
    min_qty   = float(lot_filter["minQty"])

    qty = (balance() * CAPITAL * LEVERAGE) / px

    precision = int(round(-math.log(step_size, 10), 0))

    qty = round(qty, precision)
    qty = math.floor(qty / step_size) * step_size

    if qty < min_qty:
        return None

    return float(f"{qty:.{precision}f}")

def order(symbol, side, qty):
    return client.futures_create_order(
        symbol=symbol,
        side=side,
        type="MARKET",
        quantity=qty
    )

def open_position(symbol, side, score, reasons):

    global open_trade

    px = price(symbol)
    qty = get_qty(symbol, px)

    if not qty:
        send(f"⚠️ QTY TOO LOW: {symbol}")
        return

    try:
        order(symbol, side, qty)
    except Exception as e:
        send(f"❌ ORDER ERROR: {e}")
        return

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
        "score": score,
        "reasons": reasons,
        "time": time.time()
    }

    send(
        f"🚀 ENTRY\n{symbol} {side}\n"
        f"Entry: {round(px,4)} | TP: {round(tp,4)} | SL: {round(sl,4)}\n"
        f"Qty: {qty} | Score: {score}\n"
        f"Reason: {', '.join(reasons)}\n🕒 {ts()}"
    )

# ================= EXIT =================
def manage():

    global open_trade

    if not open_trade:
        return

    t = open_trade
    px = price(t["symbol"])

    hit = None

    if t["side"] == "BUY":
        if px >= t["tp"]:
            hit = "TP"
        elif px <= t["sl"]:
            hit = "SL"
    else:
        if px <= t["tp"]:
            hit = "TP"
        elif px >= t["sl"]:
            hit = "SL"

    if hit or (time.time()-t["time"])/60 > 25:
        close_trade(hit if hit else "TIME", px)

def close_trade(reason, px):

    global open_trade, stats

    t = open_trade

    pnl = ((px - t["entry"]) / t["entry"]) * (balance() * CAPITAL * LEVERAGE)
    if t["side"] == "SELL":
        pnl = -pnl

    order(t["symbol"], "SELL" if t["side"]=="BUY" else "BUY", t["qty"])

    stats["total"] += 1
    stats["pnl"] += pnl

    if pnl >= 0:
        stats["wins"] += 1
    else:
        stats["loss"] += 1

    winrate = (stats["wins"]/stats["total"])*100

    send(
        f"{'🎯' if pnl>=0 else '🛑'} EXIT {reason}\n"
        f"{t['symbol']} {t['side']} | PnL: ${round(pnl,2)}\n\n"
        f"📊 Trades: {stats['total']} | Winrate: {round(winrate,1)}%\n"
        f"W: {stats['wins']} | L: {stats['loss']} | Net: ${round(stats['pnl'],2)}\n"
        f"🧠 Reason: {', '.join(t['reasons'])}\n🕒 {ts()}"
    )

    open_trade = None

# ================= START =================
send(f"✅ SNIPER V7.1 FINAL STARTED | {ts()}")

# ================= LOOP =================
while True:

    try:

        if open_trade:
            manage()
            time.sleep(WAIT)
            continue

        for s in SYMBOLS:

            now = time.time()

            if s in last_signal and now - last_signal[s] < 300:
                continue

            side, score, reasons = analyze(s)

            if side:
                send(f"📊 SIGNAL {s} {side} | Score {score}")
                last_signal[s] = now
                open_position(s, side, score, reasons)
                break

        time.sleep(WAIT)

    except Exception as e:
        send(f"❌ ERROR: {e}")
        time.sleep(5)