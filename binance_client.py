import os
from binance.client import Client
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv("API_KEY")
API_SECRET = os.getenv("API_SECRET")
BASE_URL = os.getenv("BASE_URL")

client = Client(API_KEY, API_SECRET)
client.FUTURES_URL = BASE_URL


def get_klines(symbol, interval="5m", limit=100):
    return client.futures_klines(symbol=symbol, interval=interval, limit=limit)


def place_order(symbol, side, quantity):
    try:
        order = client.futures_create_order(
            symbol=symbol,
            side=side,
            type="MARKET",
            quantity=quantity
        )
        return order
    except Exception as e:
        print(f"❌ Order Error: {e}")
        return None