import time
import pandas as pd
from binance_client import get_klines, place_order
from strategy import calculate_indicators, check_signal

SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT"]

TRADE_QTY = {
    "BTCUSDT": 0.001,
    "ETHUSDT": 0.01,
    "SOLUSDT": 1,
    "BNBUSDT": 0.1,
    "XRPUSDT": 10
}

COOLDOWN = 300

last_trade_time = {}

print("🚀 Bot Started (Testnet Mode)...")

while True:
    print(f"\n🕒 Cycle Start")

    for symbol in SYMBOLS:
        try:
            klines = get_klines(symbol)

            if not klines or len(klines) < 50:
                print(f"⚠️ {symbol} Skipped → Not enough data")
                continue

            df = pd.DataFrame(klines, columns=[
                "time", "open", "high", "low", "close", "volume",
                "close_time", "qav", "trades", "taker_base", "taker_quote", "ignore"
            ])

            df = df[["open", "high", "low", "close", "volume"]].astype(float)

            df = calculate_indicators(df)

            if df is None:
                print(f"⚠️ {symbol} Skipped → Indicator not ready")
                continue

            signal, reason = check_signal(df)

            if signal:
                current_time = time.time()

                if symbol in last_trade_time and current_time - last_trade_time[symbol] < COOLDOWN:
                    continue

                print(f"✅ {symbol} → {signal} Signal")

                order = place_order(
                    symbol,
                    "BUY" if signal == "BUY" else "SELL",
                    TRADE_QTY[symbol]
                )

                if order:
                    print(f"🚀 Trade Executed: {symbol} {signal}")
                    last_trade_time[symbol] = current_time

            else:
                if reason not in ["No Setup", "No Data"]:
                    print(f"⚠️ {symbol} Skipped → {reason}")

        except Exception as e:
            print(f"❌ Error ({symbol}): {e}")

    time.sleep(60)