import time
import os
import requests
import pandas as pd
from datetime import datetime
from dotenv import load_dotenv

from binance_client import get_klines, place_order
from strategy import calculate_indicators, check_signal

load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT"]

TRADE_QTY = {
    "BTCUSDT": 0.001,
    "ETHUSDT": 0.01,
    "SOLUSDT": 1,
    "BNBUSDT": 0.1,
    "XRPUSDT": 10
}

COOLDOWN = 300
LOOP_WAIT = 15   # testing mode
last_trade_time = {}


def now():
    return datetime.utcnow().strftime("%H:%M:%S UTC")


def send_telegram(msg):
    if not TELEGRAM_TOKEN or not CHAT_ID:
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        requests.post(
            url,
            json={"chat_id": CHAT_ID, "text": msg},
            timeout=8
        )
    except Exception:
        print("⚠️ Telegram send failed")


print("🚀 Bot Started")
send_telegram("🚀 Bot Started Successfully")

while True:
    cycle_start = time.time()
    print(f"\n🕒 Cycle Start | {now()}")

    for symbol in SYMBOLS:
        try:
            klines = get_klines(symbol)

            if not klines or len(klines) < 50:
                print(f"⚠️ {symbol}: no data")
                continue

            df = pd.DataFrame(klines, columns=[
                "time", "open", "high", "low", "close", "volume",
                "close_time", "qav", "trades", "taker_base",
                "taker_quote", "ignore"
            ])

            df = df[["open", "high", "low", "close", "volume"]].astype(float)

            df = calculate_indicators(df)

            if df is None:
                continue

            signal, reason = check_signal(df)

            if signal:
                current_time = time.time()

                if symbol in last_trade_time:
                    if current_time - last_trade_time[symbol] < COOLDOWN:
                        print(f"⏳ {symbol}: cooldown")
                        continue

                print(f"🔥 {symbol}: {signal}")
                send_telegram(f"🔥 SIGNAL {symbol}: {signal}")

                order = place_order(
                    symbol,
                    "BUY" if signal == "BUY" else "SELL",
                    TRADE_QTY[symbol]
                )

                if order:
                    print(f"✅ Executed {symbol} {signal}")
                    send_telegram(f"✅ EXECUTED {symbol} {signal}")
                    last_trade_time[symbol] = current_time

            else:
                if reason in ["Wick/Volatility", "Low Volume"]:
                    print(f"⚠️ {symbol}: {reason}")

        except Exception as e:
            print(f"❌ {symbol}: {str(e)}")

    took = round(time.time() - cycle_start, 2)
    print(f"✅ Cycle Complete | runtime={took}s | next in {LOOP_WAIT}s")

    time.sleep(LOOP_WAIT)