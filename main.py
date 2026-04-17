# ==========================================================
# TRUE SCALPER v4 CAPITAL PRESERVER
# Risk-first upgrade focused on preserving balance curve
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
# CLIENT (FUTURES TESTNET)
# ==========================================================
client = Client(API_KEY, API_SECRET)
client.FUTURES_URL = "https://testnet.binancefuture.com/fapi"

# ==========================================================
# CONFIG
# ==========================================================
SYMBOLS = ["BTCUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT"]  # ETH removed for now

# Capital preservation sizing
BASE_CAPITAL_PCT = 0.10      # normal signal uses 10%
STRONG_CAPITAL_PCT = 0.15    # strongest signal uses 15%
LEVERAGE = 3

# Reward > Risk
TP_PCT = 0.55
SL_PCT = 0.28

# Engine
WAIT = 30
COOLDOWN = 2700             # 45 min per symbol
MAX_HOLD_MIN = 22
MAX_TRADES_PER_SESSION = 3

# Daily controls
DAILY_MAX_LOSS_PCT = 1.5
SESSION_LOCK_PROFIT_PCT = 1.2
LOSS_STREAK_PAUSE_MIN = 90

# ==========================================================
# STATE
# ==========================================================
open_trade = None
last_trade = {}
last_session = None
last_heartbeat = 0

session_trades = 0
consecutive_losses = 0
pause_until = 0

day_start_balance = None
session_start_balance = None
realized_pnl_today = 0.0

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

def day_key():
    return ist_now().strftime("%Y-%m-%d")

current_day = day_key()

# ==========================================================
# TELEGRAM + LOG
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
# SESSION WINDOWS
# ==========================================================
def current_session():
    total = utc_now().hour * 60 + utc_now().minute

    # India focus: 07:15 to 11:15 UTC
    if 435 <= total <= 675:
        return "INDIA SESSION"

    # London/US overlap: 13:30 to 17:15 UTC
    if 810 <= total <= 1035:
        return "LONDON / US OVERLAP"

    return None

# ==========================================================
# EXCHANGE HELPERS
# ==========================================================
def get_balance():
    try:
        for row in client.futures_account_balance():
            if row["asset"] == "USDT":
                return float(row["balance"])
    except:
        return 0.0
    return 0.0

def get_klines(symbol, limit=120):
    try:
        return client.get_klines(symbol=symbol, interval="5m", limit=limit)
    except:
        return None

def get_price(symbol):
    try:
        return float(client.get_symbol_ticker(symbol=symbol)["price"])
    except:
        return None

def set_leverage(symbol):
    try:
        client.futures_change_leverage(symbol=symbol, leverage=LEVERAGE)
    except:
        pass

def step_size(symbol):
    try:
        info = client.futures_exchange_info()
        for s in info["symbols"]:
            if s["symbol"] == symbol:
                for f in s["filters"]:
                    if f["filterType"] == "LOT_SIZE":
                        return float(f["stepSize"])
    except:
        pass
    return 0.001

def round_step(qty, step):
    return math.floor(qty / step) * step

def place_market(symbol, side, qty):
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
# DAILY RESET
# ==========================================================
def reset_day_if_needed():
    global current_day, day_start_balance, realized_pnl_today, consecutive_losses

    today = day_key()
    if today != current_day:
        current_day = today
        day_start_balance = get_balance()
        realized_pnl_today = 0.0
        consecutive_losses = 0
        send(
            f"🌅 NEW TRADING DAY RESET\n"
            f"Start Balance: ${round(day_start_balance,2)}\n"
            f"🕒 {ts()}"
        )

# ==========================================================
# RISK GATES
# ==========================================================
def daily_loss_hit():
    bal = get_balance()
    if day_start_balance is None or day_start_balance <= 0:
        return False
    dd = ((bal - day_start_balance) / day_start_balance) * 100
    return dd <= -DAILY_MAX_LOSS_PCT

def session_profit_locked():
    global session_start_balance
    if session_start_balance is None or session_start_balance <= 0:
        return False
    bal = get_balance()
    gain = ((bal - session_start_balance) / session_start_balance) * 100
    return gain >= SESSION_LOCK_PROFIT_PCT

# ==========================================================
# ENTRY
# ==========================================================
def open_position(symbol, side, price, confidence):
    global open_trade, session_trades

    balance = get_balance()
    cap_pct = STRONG_CAPITAL_PCT if confidence >= 9 else BASE_CAPITAL_PCT

    used_margin = balance * cap_pct
    position_size = used_margin * LEVERAGE

    qty_raw = position_size / price
    step = step_size(symbol)
    qty = round_step(qty_raw, step)

    if qty <= 0:
        return False

    set_leverage(symbol)

    order = place_market(symbol, side, qty)
    if not order:
        send(f"❌ ORDER FAILED\n{symbol}\n🕒 {ts()}")
        return False

    if side == "BUY":
        tp = round(price * (1 + TP_PCT / 100), 6)
        sl = round(price * (1 - SL_PCT / 100), 6)
    else:
        tp = round(price * (1 - TP_PCT / 100), 6)
        sl = round(price * (1 + SL_PCT / 100), 6)

    open_trade = {
        "symbol": symbol,
        "side": side,
        "entry": price,
        "qty": qty,
        "tp": tp,
        "sl": sl,
        "position_size": position_size,
        "used_margin": used_margin,
        "opened_at": time.time()
    }

    session_trades += 1

    send(
        f"✅ ENTRY EXECUTED\n\n"
        f"Coin: {symbol}\n"
        f"Side: {side}\n"
        f"Entry: {price}\n"
        f"Qty: {qty}\n"
        f"TP: {tp}\n"
        f"SL: {sl}\n"
        f"Margin Used: ${round(used_margin,2)}\n"
        f"Leverage: {LEVERAGE}x\n"
        f"Position Size: ${round(position_size,2)}\n"
        f"Confidence: {confidence}/10\n"
        f"🕒 {ts()}"
    )

    return True

# ==========================================================
# EXITS
# ==========================================================
def close_position(reason, exit_price):
    global open_trade, realized_pnl_today, consecutive_losses, pause_until

    if not open_trade:
        return

    side = open_trade["side"]
    entry = open_trade["entry"]
    pos = open_trade["position_size"]

    if side == "BUY":
        pnl = ((exit_price - entry) / entry) * pos
        close_side = "SELL"
    else:
        pnl = ((entry - exit_price) / entry) * pos
        close_side = "BUY"

    place_market(open_trade["symbol"], close_side, open_trade["qty"])

    held = int((time.time() - open_trade["opened_at"]) / 60)
    realized_pnl_today += pnl

    if pnl < 0:
        consecutive_losses += 1
    else:
        consecutive_losses = 0

    if consecutive_losses >= 2:
        pause_until = time.time() + LOSS_STREAK_PAUSE_MIN * 60

    emoji = "🎯" if pnl >= 0 else "🛑"

    send(
        f"{emoji} {reason}\n\n"
        f"{open_trade['symbol']} {side}\n"
        f"Entry: {entry}\n"
        f"Exit: {exit_price}\n"
        f"PnL: ${round(pnl,2)}\n"
        f"Held: {held} mins\n"
        f"Today PnL: ${round(realized_pnl_today,2)}\n"
        f"Loss Streak: {consecutive_losses}\n"
        f"🕒 {ts()}"
    )

    open_trade = None

def manage_open_trade():
    if not open_trade:
        return

    price = get_price(open_trade["symbol"])
    if not price:
        return

    side = open_trade["side"]
    held = int((time.time() - open_trade["opened_at"]) / 60)

    if side == "BUY":
        if price >= open_trade["tp"]:
            close_position("TAKE PROFIT HIT", price)
            return
        if price <= open_trade["sl"]:
            close_position("STOP LOSS HIT", price)
            return
    else:
        if price <= open_trade["tp"]:
            close_position("TAKE PROFIT HIT", price)
            return
        if price >= open_trade["sl"]:
            close_position("STOP LOSS HIT", price)
            return

    if held >= MAX_HOLD_MIN:
        close_position("SMART EXIT (TIME RELEASE)", price)

# ==========================================================
# STARTUP
# ==========================================================
day_start_balance = get_balance()

send(
    f"🚀 TRUE SCALPER v4 CAPITAL PRESERVER STARTED\n"
    f"Balance: ${round(day_start_balance,2)}\n"
    f"Coins: BTC SOL BNB XRP\n"
    f"Risk Mode: Conservative\n"
    f"🕒 {ts()}"
)

# ==========================================================
# MAIN LOOP
# ==========================================================
while True:
    try:
        reset_day_if_needed()

        # heartbeat hourly
        if time.time() - last_heartbeat > 3600:
            send(
                f"💓 BOT ACTIVE\n"
                f"Open Trade: {'YES' if open_trade else 'NO'}\n"
                f"Today PnL: ${round(realized_pnl_today,2)}\n"
                f"🕒 {ts()}"
            )
            last_heartbeat = time.time()

        # session start/end alerts
        sess = current_session()
        if sess != last_session:
            if sess:
                session_trades = 0
                session_start_balance = get_balance()
                send(
                    f"🟢 SESSION STARTED\n"
                    f"{sess}\n"
                    f"Balance: ${round(session_start_balance,2)}\n"
                    f"Max Trades: {MAX_TRADES_PER_SESSION}\n"
                    f"🕒 {ts()}"
                )
            elif last_session:
                send(
                    f"🔴 SESSION CLOSED\n"
                    f"{last_session}\n"
                    f"Balance: ${round(get_balance(),2)}\n"
                    f"🕒 {ts()}"
                )
            last_session = sess

        # manage live trade first
        if open_trade:
            manage_open_trade()
            time.sleep(WAIT)
            continue

        # no trading outside session
        if not sess:
            time.sleep(WAIT)
            continue

        # risk locks
        if daily_loss_hit():
            send(f"🛑 DAILY LOSS LIMIT HIT\nTrading Paused Today\n🕒 {ts()}")
            time.sleep(300)
            continue

        if session_profit_locked():
            send(f"🎯 SESSION PROFIT LOCKED\nNo More Trades This Session\n🕒 {ts()}")
            time.sleep(300)
            continue

        if session_trades >= MAX_TRADES_PER_SESSION:
            time.sleep(WAIT)
            continue

        if time.time() < pause_until:
            time.sleep(WAIT)
            continue

        # scan symbols
        for symbol in SYMBOLS:
            if symbol in last_trade and time.time() - last_trade[symbol] < COOLDOWN:
                continue

            try:
                raw = get_klines(symbol)
                if not raw:
                    continue

                df = pd.DataFrame(raw, columns=[
                    "time","open","high","low","close","volume",
                    "ct","qv","n","tb","tq","ig"
                ])
                df = df[["open","high","low","close","volume"]].astype(float)

                df = indicators(df)
                result = check_signal(df)

                # supports "BUY"/"SELL" or tuple(signal, confidence)
                signal = None
                confidence = 8

                if isinstance(result, tuple):
                    signal, confidence = result
                else:
                    signal = result

                if signal:
                    price = float(df.iloc[-1]["close"])

                    send(
                        f"🔥 HIGH QUALITY SIGNAL\n\n"
                        f"Coin: {symbol}\n"
                        f"Direction: {signal}\n"
                        f"Price: {price}\n"
                        f"Confidence: {confidence}/10\n"
                        f"Session: {sess}\n"
                        f"🕒 {ts()}"
                    )

                    if open_position(symbol, signal, price, confidence):
                        last_trade[symbol] = time.time()
                        break

            except:
                pass

        time.sleep(WAIT)

    except:
        send("❌ AUTO RECOVERY TRIGGERED")
        time.sleep(10)