import pandas as pd


def calculate_indicators(df):
    if len(df) < 50:
        return None

    df["ema20"] = df["close"].ewm(span=20).mean()
    df["ema50"] = df["close"].ewm(span=50).mean()

    df["rsi"] = compute_rsi(df["close"], 14)
    df["volume_avg"] = df["volume"].rolling(20).mean()
    df["atr"] = compute_atr(df, 14)

    df = df.dropna()

    if df.empty:
        return None

    return df


def compute_rsi(series, period):
    delta = series.diff()
    gain = (delta.where(delta > 0, 0)).rolling(period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))


def compute_atr(df, period):
    high_low = df["high"] - df["low"]
    high_close = (df["high"] - df["close"].shift()).abs()
    low_close = (df["low"] - df["close"].shift()).abs()

    ranges = pd.concat([high_low, high_close, low_close], axis=1)
    true_range = ranges.max(axis=1)

    return true_range.rolling(period).mean()


def is_valid_candle(row):
    body = abs(row["close"] - row["open"])
    total = row["high"] - row["low"]

    if total == 0:
        return False

    body_ratio = body / total

    if body_ratio < 0.6:
        return False

    if total > row["atr"] * 1.8:
        return False

    return True


def check_signal(df):
    if df is None or len(df) == 0:
        return None, "No Data"

    row = df.iloc[-1]

    if not is_valid_candle(row):
        return None, "Wick/Volatility"

    if row["volume"] < row["volume_avg"]:
        return None, "Low Volume"

    # LONG
    if row["ema20"] > row["ema50"] and 40 < row["rsi"] < 50:
        return "BUY", "Valid"

    # SHORT
    if row["ema20"] < row["ema50"] and 50 < row["rsi"] < 60:
        return "SELL", "Valid"

    return None, "No Setup"