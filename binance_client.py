import os
from dotenv import load_dotenv
from binance.client import Client

load_dotenv()

API_KEY = os.getenv("API_KEY")
API_SECRET = os.getenv("API_SECRET")

client = Client(API_KEY, API_SECRET)

# Live public data used automatically


def get_klines(symbol, interval="5m", limit=120):
    try:
        return client.get_klines(
            symbol=symbol,
            interval=interval,
            limit=limit
        )
    except:
        return None


def place_order(symbol, side, quantity):
    try:
        # Testnet / paper placeholder
        return {
            "status": "FILLED",
            "symbol": symbol,
            "side": side,
            "qty": quantity
        }
    except:
        return None