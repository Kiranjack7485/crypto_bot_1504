import time
import math
from datetime import datetime
from binance.client import Client
import pandas as pd
import requests

# ================= CONFIG =================
API_KEY = "YOUR_KEY"
API_SECRET = "YOUR_SECRET"

TELEGRAM_TOKEN = "YOUR_TOKEN"
TELEGRAM_CHAT_ID = "YOUR_CHAT_ID"

SYMBOLS = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT"]
INTERVAL = Client.KLINE_INTERVAL_15MINUTE
RISK_PER_TRADE = 0.02

client = Client(API_KEY, API_SECRET)

# ================= TELEGRAM =================
def send_telegram(msg):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        requests.post(url, data={"chat_id": TELEGRAM_CHAT_ID, "text": msg})
    except:
        pass

# ================= SESSION ENGINE =================
def get_session():
    hour = datetime.utcnow().hour + 5.5  # IST

    if 1.5 <= hour < 4.5:
        return "AVOID"
    elif 4.5 <= hour < 9:
        return "LOW"
    elif 9 <= hour < 15:
        return "MID"
    elif 15 <= hour < 21:
        return "HIGH"
    else:
        return "BEST"

# ================= PRECISION FIX =================
def adjust_precision(symbol, qty):
    info = client.futures_exchange_info()
    for s in info['symbols']:
        if s['symbol'] == symbol:
            step_size = float([f for f in s['filters'] if f['filterType'] == 'LOT_SIZE'][0]['stepSize'])
            precision = int(round(-math.log(step_size, 10), 0))
            return round(qty, precision)
    return qty

# ================= DATA =================
def get_data(symbol):
    klines = client.futures_klines(symbol=symbol, interval=INTERVAL, limit=100)
    df = pd.DataFrame(klines)
    df.columns = ["time","o","h","l","c","v","ct","q","n","tbb","tbq","ig"]
    df["c"] = df["c"].astype(float)
    df["v"] = df["v"].astype(float)
    return df

# ================= INDICATORS =================
def indicators(df):
    df["ema20"] = df["c"].ewm(span=20).mean()
    df["ema50"] = df["c"].ewm(span=50).mean()

    delta = df["c"].diff()
    gain = (delta.where(delta > 0, 0)).rolling(14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
    rs = gain / loss
    df["rsi"] = 100 - (100 / (1 + rs))

    return df

# ================= TREND =================
def trend(df):
    if df["ema20"].iloc[-1] > df["ema50"].iloc[-1]:
        return "UP"
    elif df["ema20"].iloc[-1] < df["ema50"].iloc[-1]:
        return "DOWN"
    return "SIDE"

# ================= ENTRY LOGIC =================
def entry_signal(df):
    last = df.iloc[-1]
    prev = df.iloc[-2]

    breakout_up = last["c"] > prev["h"]
    breakout_down = last["c"] < prev["l"]

    rsi_strong = last["rsi"] > 55 or last["rsi"] < 45
    volume_spike = last["v"] > df["v"].rolling(20).mean().iloc[-1]

    score = 0
    reason = []

    if breakout_up:
        score += 30
        reason.append("Breakout Up")
    if breakout_down:
        score += 30
        reason.append("Breakout Down")
    if rsi_strong:
        score += 20
        reason.append("RSI")
    if volume_spike:
        score += 20
        reason.append("Volume")

    return score, reason, breakout_up, breakout_down

# ================= RISK =================
def get_levels(price, direction):
    sl_pct = 0.4 / 100
    tp_pct = 0.8 / 100  # 1:2 RR

    if direction == "BUY":
        sl = price * (1 - sl_pct)
        tp = price * (1 + tp_pct)
    else:
        sl = price * (1 + sl_pct)
        tp = price * (1 - tp_pct)

    return sl, tp

# ================= POSITION SIZE =================
def get_qty(symbol, price):
    balance = 100  # demo
    risk = balance * RISK_PER_TRADE
    qty = risk / price
    return adjust_precision(symbol, qty)

# ================= STATS =================
stats = {
    "trades": 0,
    "wins": 0,
    "loss": 0,
    "net": 0,
    "session": {}
}

# ================= TRADE EXECUTION =================
def execute_trade(symbol):
    df = indicators(get_data(symbol))
    t = trend(df)

    score, reason, up, down = entry_signal(df)
    session = get_session()

    # Session filter
    if session == "AVOID":
        return

    if session == "LOW" and score < 85:
        return

    if session in ["MID","HIGH","BEST"] and score < 70:
        return

    price = df["c"].iloc[-1]

    # Trend alignment
    if up and t != "UP":
        return
    if down and t != "DOWN":
        return

    direction = "BUY" if up else "SELL"
    sl, tp = get_levels(price, direction)
    qty = get_qty(symbol, price)

    try:
        client.futures_create_order(
            symbol=symbol,
            side="BUY" if direction=="BUY" else "SELL",
            type="MARKET",
            quantity=qty
        )
    except Exception as e:
        print("ORDER ERROR:", e)
        return

    send_telegram(
        f"🚀 {symbol} {direction}\n"
        f"Entry: {price}\nTP: {tp}\nSL: {sl}\n"
        f"Score: {score}\nSession: {session}\nReason: {', '.join(reason)}"
    )

    manage_trade(symbol, price, tp, sl, direction, session)

# ================= TRADE MANAGEMENT =================
def manage_trade(symbol, entry, tp, sl, direction, session):
    global stats

    partial_done = False

    while True:
        price = float(client.futures_symbol_ticker(symbol=symbol)["price"])

        # Partial profit
        if not partial_done:
            if (direction=="BUY" and price >= (entry + (tp-entry)/2)) or \
               (direction=="SELL" and price <= (entry - (entry-tp)/2)):
                partial_done = True
                sl = entry  # move SL to breakeven
                send_telegram(f"⚡ Partial Profit {symbol} | SL moved to BE")

        # TP
        if (direction=="BUY" and price >= tp) or (direction=="SELL" and price <= tp):
            pnl = abs(tp-entry)
            stats["wins"] += 1
            stats["net"] += pnl
            result = "TP"

        # SL
        elif (direction=="BUY" and price <= sl) or (direction=="SELL" and price >= sl):
            pnl = -abs(entry-sl)
            stats["loss"] += 1
            stats["net"] += pnl
            result = "SL"
        else:
            time.sleep(2)
            continue

        stats["trades"] += 1

        # Session tracking
        stats["session"].setdefault(session, 0)
        stats["session"][session] += pnl

        send_telegram(
            f"🎯 EXIT {symbol} | {result}\nPnL: {round(pnl,2)}\n\n"
            f"Trades: {stats['trades']} | Winrate: {round(stats['wins']/stats['trades']*100,1)}%\n"
            f"Net: {round(stats['net'],2)}\nSession Stats: {stats['session']}"
        )

        break

# ================= MAIN LOOP =================
print("✅ SNIPER V8 STARTED")

while True:
    for s in SYMBOLS:
        try:
            execute_trade(s)
        except Exception as e:
            print("Error:", e)
    time.sleep(10)