# ==========================================================
# TRUE SCALPER PRO v2 TESTNET CAPITAL ENGINE
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
from strategy import indicators, check_signal

load_dotenv()

# ==========================================================
# ENV
# ==========================================================
API_KEY = os.getenv("API_KEY")
API_SECRET = os.getenv("API_SECRET")

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# ==========================================================
# BINANCE FUTURES TESTNET LOGIN
# ==========================================================
client = Client(API_KEY, API_SECRET)
client.FUTURES_URL = "https://testnet.binancefuture.com/fapi"

# ==========================================================
# CONFIG
# ==========================================================
SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT"]

LEVERAGE = 5
USE_CAPITAL_PCT = 0.45

TP_PCT = 0.45
SL_PCT = 0.30

MAX_HOLD_MIN = 28
WAIT = 20
COOLDOWN = 1800

last_trade = {}
open_trade = None
last_session = None
last_heartbeat = 0


# ==========================================================
# TIME
# ==========================================================
def now_utc():
    return datetime.now(timezone.utc)


def now_ist():
    return now_utc().astimezone(
        timezone(timedelta(hours=5, minutes=30))
    )


def tstr():
    return (
        f"UTC {now_utc().strftime('%H:%M:%S')} | "
        f"IST {now_ist().strftime('%H:%M:%S')}"
    )


# ==========================================================
# TELEGRAM + TERMINAL
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
# SESSION
# ==========================================================
def current_session():
    total = now_utc().hour * 60 + now_utc().minute

    # India: 07:00 to 12:00 UTC
    if 420 <= total <= 720:
        return "INDIA SESSION"

    # London + US overlap: 13:00 to 18:00 UTC
    if 780 <= total <= 1080:
        return "LONDON / US OVERLAP"

    return None


# ==========================================================
# BINANCE HELPERS
# ==========================================================
def login_check():
    try:
        bal = client.futures_account_balance()
        return True
    except:
        return False


def get_balance():
    try:
        balances = client.futures_account_balance()

        for row in balances:
            if row["asset"] == "USDT":
                return float(row["balance"])

        return 0.0
    except:
        return 0.0


def get_klines(symbol, limit=120):
    try:
        return client.get_klines(
            symbol=symbol,
            interval="5m",
            limit=limit
        )
    except:
        return None


def set_leverage(symbol):
    try:
        client.futures_change_leverage(
            symbol=symbol,
            leverage=LEVERAGE
        )
    except:
        pass


def qty_precision(symbol):
    try:
        info = client.futures_exchange_info()

        for s in info["symbols"]:
            if s["symbol"] == symbol:
                step = float(s["filters"][1]["stepSize"])
                return step

        return 0.001
    except:
        return 0.001


def round_step(qty, step):
    return math.floor(qty / step) * step


def place_order(symbol, side, qty):
    try:
        return client.futures_create_order(
            symbol=symbol,
            side=side,
            type="MARKET",
            quantity=qty
        )
    except:
        return None


def last_price(symbol):
    try:
        return float(
            client.get_symbol_ticker(symbol=symbol)["price"]
        )
    except:
        return None


# ==========================================================
# ENTRY
# ==========================================================
def create_trade(symbol, side, price):
    global open_trade

    balance = get_balance()
    used_capital = round(balance * USE_CAPITAL_PCT, 2)

    position_size = used_capital * LEVERAGE

    raw_qty = position_size / price
    step = qty_precision(symbol)
    qty = round_step(raw_qty, step)

    if qty <= 0:
        return

    set_leverage(symbol)

    order = place_order(symbol, side, qty)

    if not order:
        send(f"❌ Order Failed\n{symbol}\n🕒 {tstr()}")
        return

    if side == "BUY":
        tp = round(price * (1 + TP_PCT / 100), 4)
        sl = round(price * (1 - SL_PCT / 100), 4)
    else:
        tp = round(price * (1 - TP_PCT / 100), 4)
        sl = round(price * (1 + SL_PCT / 100), 4)

    open_trade = {
        "symbol": symbol,
        "side": side,
        "entry": price,
        "qty": qty,
        "tp": tp,
        "sl": sl,
        "position": position_size,
        "time": time.time()
    }

    send(
        f"✅ ENTRY EXECUTED\n\n"
        f"Coin: {symbol}\n"
        f"Side: {side}\n"
        f"Entry: {price}\n"
        f"Qty: {qty}\n"
        f"TP: {tp}\n"
        f"SL: {sl}\n"
        f"Capital Used: ${used_capital}\n"
        f"Leverage: {LEVERAGE}x\n"
        f"Position Size: ${round(position_size,2)}\n"
        f"🕒 {tstr()}"
    )


# ==========================================================
# EXIT MANAGER
# ==========================================================
def close_trade(reason, price):
    global open_trade

    if not open_trade:
        return

    side = open_trade["side"]
    entry = open_trade["entry"]
    pos = open_trade["position"]

    if side == "BUY":
        pnl = ((price - entry) / entry) * pos
        close_side = "SELL"
    else:
        pnl = ((entry - price) / entry) * pos
        close_side = "BUY"

    place_order(
        open_trade["symbol"],
        close_side,
        open_trade["qty"]
    )

    held = int((time.time() - open_trade["time"]) / 60)

    emoji = "🎯" if pnl >= 0 else "🛑"

    send(
        f"{emoji} {reason}\n\n"
        f"{open_trade['symbol']} {side}\n"
        f"Entry: {entry}\n"
        f"Exit: {price}\n"
        f"PnL: ${round(pnl,2)}\n"
        f"Held: {held} mins\n"
        f"🕒 {tstr()}"
    )

    open_trade = None


def manage_trade():
    if not open_trade:
        return

    price = last_price(open_trade["symbol"])

    if not price:
        return

    held = int((time.time() - open_trade["time"]) / 60)

    if open_trade["side"] == "BUY":

        if price >= open_trade["tp"]:
            close_trade("TAKE PROFIT HIT", price)
            return

        if price <= open_trade["sl"]:
            close_trade("STOP LOSS HIT", price)
            return

    else:

        if price <= open_trade["tp"]:
            close_trade("TAKE PROFIT HIT", price)
            return

        if price >= open_trade["sl"]:
            close_trade("STOP LOSS HIT", price)
            return

    if held >= MAX_HOLD_MIN:
        close_trade("SMART EXIT (Linear Market)", price)


# ==========================================================
# STARTUP
# ==========================================================
if login_check():
    bal = get_balance()

    send(
        f"🚀 TRUE SCALPER PRO v2 STARTED\n"
        f"✅ Binance Futures Testnet Login Success\n"
        f"Available Balance: ${bal}\n"
        f"Watching BTC ETH SOL BNB XRP\n"
        f"🕒 {tstr()}"
    )
else:
    send(
        f"❌ Binance Login Failed\n"
        f"Check API Keys\n"
        f"🕒 {tstr()}"
    )


# ==========================================================
# LOOP
# ==========================================================
while True:

    try:
        # ----------------------------------
        # Heartbeat every 1 hour
        # ----------------------------------
        if time.time() - last_heartbeat > 3600:
            send(
                f"💓 BOT ACTIVE\n"
                f"Open Trade: {'YES' if open_trade else 'NO'}\n"
                f"🕒 {tstr()}"
            )
            last_heartbeat = time.time()

        # ----------------------------------
        # Session Alert
        # ----------------------------------
        sess = current_session()

        if sess != last_session:

            if sess:
                bal = get_balance()

                send(
                    f"🟢 SESSION STARTED\n"
                    f"{sess}\n"
                    f"Login Verified ✅\n"
                    f"Futures Balance: ${bal}\n"
                    f"Now scanning strongest setups\n"
                    f"🕒 {tstr()}"
                )

            elif last_session:
                bal = get_balance()

                send(
                    f"🔴 SESSION CLOSED\n"
                    f"{last_session}\n"
                    f"Remaining Balance: ${bal}\n"
                    f"Scanning paused\n"
                    f"🕒 {tstr()}"
                )

            last_session = sess

        # ----------------------------------
        # Manage Open Trade
        # ----------------------------------
        if open_trade:
            manage_trade()
            time.sleep(WAIT)
            continue

        # ----------------------------------
        # Scan only in session
        # ----------------------------------
        if not sess:
            time.sleep(WAIT)
            continue

        # ----------------------------------
        # Scan Coins
        # ----------------------------------
        for symbol in SYMBOLS:

            if symbol in last_trade:
                if time.time() - last_trade[symbol] < COOLDOWN:
                    continue

            try:
                k = get_klines(symbol)

                if not k:
                    continue

                df = pd.DataFrame(k, columns=[
                    "time","open","high","low","close","volume",
                    "ct","qv","n","tb","tq","ig"
                ])

                df = df[["open","high","low","close","volume"]].astype(float)

                df = indicators(df)

                signal = check_signal(df)

                if signal:

                    price = float(df.iloc[-1]["close"])

                    bal = get_balance()
                    use_cap = round(bal * USE_CAPITAL_PCT, 2)

                    send(
                        f"🔥 GOLDEN SIGNAL FOUND\n\n"
                        f"Coin: {symbol}\n"
                        f"Direction: {signal}\n"
                        f"Current Price: {price}\n"
                        f"Available Balance: ${bal}\n"
                        f"Capital To Use: ${use_cap}\n"
                        f"Leverage: {LEVERAGE}x\n"
                        f"Session: {sess}\n"
                        f"🕒 {tstr()}"
                    )

                    create_trade(symbol, signal, price)

                    last_trade[symbol] = time.time()
                    break

            except:
                pass

        time.sleep(WAIT)

    except:
        send("❌ Auto Recovering after issue")
        time.sleep(10)