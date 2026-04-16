import pandas as pd


def indicators(df):
    df["ema20"] = df["close"].ewm(span=20).mean()
    df["ema50"] = df["close"].ewm(span=50).mean()
    df["vol_avg"] = df["volume"].rolling(20).mean()
    return df.dropna()


def valid_candle(row):
    body = abs(row["close"] - row["open"])
    total = row["high"] - row["low"]

    if total == 0:
        return False

    # avoid long wick traps
    if body / total < 0.55:
        return False

    return True


def check_signal(df):
    if len(df) < 60:
        return None

    last = df.iloc[-1]
    prev = df.iloc[-2]

    # BUY logic
    if (
        last["ema20"] > last["ema50"]
        and last["low"] <= last["ema20"] * 1.002
        and last["close"] > prev["high"]
        and last["volume"] > last["vol_avg"]
        and valid_candle(last)
    ):
        return "BUY"

    # SELL logic
    if (
        last["ema20"] < last["ema50"]
        and last["high"] >= last["ema20"] * 0.998
        and last["close"] < prev["low"]
        and last["volume"] > last["vol_avg"]
        and valid_candle(last)
    ):
        return "SELL"

    return None