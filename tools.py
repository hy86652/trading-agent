"""Plain-Python implementations of the 4 trading tools (Task 3 version).

Task 4 moves these behind an MCP server. Keep the signatures and return shapes
stable so that migration changes how the tools are exposed, not what they do.
"""
import json
import re
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent
PRICE_DIR = ROOT / "data" / "prices"
PORTFOLIO_PATH = ROOT / "data" / "portfolio.json"

INDICATOR_PATTERN = re.compile(r"(MA|RSI)(\d+)")


def _error(message: str) -> dict[str, str]:
    """Return a tool error in one shape so callers can check for a single key."""
    return {"error": message}


def _known_tickers() -> list[str]:
    """The whitelist is whatever CSVs exist, so a ticker never maps to an arbitrary path."""
    return sorted(p.stem for p in PRICE_DIR.glob("*.csv"))


def _load_prices(ticker: str) -> pd.DataFrame | None:
    """Load one ticker's daily bars, or None if the ticker is not in the whitelist."""
    ticker = ticker.upper()
    if ticker not in _known_tickers():
        return None
    return pd.read_csv(PRICE_DIR / f"{ticker}.csv")


def _unknown_ticker_error(ticker: str) -> dict[str, str]:
    return _error(f"unknown ticker '{ticker}', valid tickers: {_known_tickers()}")


def _read_account() -> dict[str, Any]:
    with PORTFOLIO_PATH.open(encoding="utf-8") as f:
        return json.load(f)


def _write_account(account: dict[str, Any]) -> None:
    with PORTFOLIO_PATH.open("w", encoding="utf-8") as f:
        json.dump(account, f, indent=2)


def _rsi(close: pd.Series, period: int) -> pd.Series:
    """RSI with Wilder smoothing, the convention most charting tools use."""
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False).mean()
    return 100 - 100 / (1 + gain / loss)


def get_price(ticker: str, days: int = 1) -> dict[str, Any]:
    """Return the most recent `days` daily bars for `ticker`."""
    df = _load_prices(ticker)
    if df is None:
        return _unknown_ticker_error(ticker)
    if days < 1:
        return _error("days must be >= 1")
    return {"ticker": ticker.upper(), "bars": df.tail(days).to_dict(orient="records")}


def get_indicators(ticker: str, indicators: list[str]) -> dict[str, Any]:
    """Return the latest value of each requested indicator, e.g. ["MA5", "MA20", "RSI14"]."""
    df = _load_prices(ticker)
    if df is None:
        return _unknown_ticker_error(ticker)
    close = df["close"]
    values: dict[str, float | None] = {}
    for name in indicators:
        key = name.strip().upper()
        match = INDICATOR_PATTERN.fullmatch(key)
        if match is None or int(match.group(2)) < 1:
            return _error(f"unsupported indicator '{name}', supported: MA<n>, RSI<n>")
        kind, period = match.group(1), int(match.group(2))
        series = close.rolling(period).mean() if kind == "MA" else _rsi(close, period)
        latest = series.iloc[-1]
        # NaN means there are not enough bars yet; None is clearer to the LLM than NaN.
        values[key] = None if pd.isna(latest) else round(float(latest), 2)
    return {"ticker": ticker.upper(), "values": values}


def get_portfolio() -> dict[str, Any]:
    """Return cash, positions and total market value of the simulated account."""
    account = _read_account()
    cash = account["cash"]
    positions = []
    holdings_value = 0.0
    for ticker, quantity in account["positions"].items():
        price = float(_load_prices(ticker)["close"].iloc[-1])
        market_value = round(quantity * price, 2)
        holdings_value += market_value
        positions.append({"ticker": ticker, "quantity": quantity, "last_price": price, "market_value": market_value})
    return {
        "cash": round(cash, 2),
        "positions": positions,
        "total_value": round(cash + holdings_value, 2),
    }


def place_order(ticker: str, side: str, quantity: int) -> dict[str, Any]:
    """Simulated market order: fills at the latest close and updates the account.

    Stage 0 only checks what is needed to keep the account consistent. The full
    pre-hook rules (e.g. the 20% per-order cap) arrive in Task 5, and human
    approval in Task 6.
    """
    df = _load_prices(ticker)
    if df is None:
        return _unknown_ticker_error(ticker)
    side = side.lower()
    if side not in ("buy", "sell"):
        return _error("side must be 'buy' or 'sell'")
    if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity <= 0:
        return _error("quantity must be a positive integer")

    ticker = ticker.upper()
    price = float(df["close"].iloc[-1])
    cost = round(price * quantity, 2)
    account = _read_account()
    held = account["positions"].get(ticker, 0)

    if side == "buy":
        if cost > account["cash"]:
            return _error(f"insufficient cash: order costs {cost}, cash is {round(account['cash'], 2)}")
        account["cash"] = round(account["cash"] - cost, 2)
        account["positions"][ticker] = held + quantity
    else:
        if quantity > held:
            return _error(f"insufficient position: hold {held} {ticker}, tried to sell {quantity} (no shorting)")
        account["cash"] = round(account["cash"] + cost, 2)
        remaining = held - quantity
        if remaining:
            account["positions"][ticker] = remaining
        else:
            del account["positions"][ticker]

    _write_account(account)
    return {
        "status": "filled",
        "ticker": ticker,
        "side": side,
        "quantity": quantity,
        "price": price,
        "amount": cost,
        "cash": account["cash"],
    }


def _self_test() -> None:
    """Exercise each tool, including the error paths. Restores the account file afterwards."""
    original = PORTFOLIO_PATH.read_text(encoding="utf-8")
    try:
        bars = get_price("AAPL", 3)["bars"]
        assert len(bars) == 3, bars

        assert "error" in get_price("FAKE"), "unknown ticker must return an error"
        assert "error" in get_price("AAPL", 0)

        ind = get_indicators("AAPL", ["MA5", "MA20", "RSI14"])["values"]
        assert set(ind) == {"MA5", "MA20", "RSI14"}, ind
        assert 0 <= ind["RSI14"] <= 100, ind
        assert "error" in get_indicators("AAPL", ["MACD"]), "unsupported indicator must return an error"

        _write_account({"cash": 100000.0, "positions": {}})
        portfolio = get_portfolio()
        assert portfolio["total_value"] == 100000.0, portfolio

        filled = place_order("AAPL", "buy", 10)
        assert filled.get("status") == "filled", filled
        assert get_portfolio()["positions"][0]["quantity"] == 10
        assert "error" in place_order("AAPL", "sell", 11), "selling more than held must fail"
        assert "error" in place_order("AAPL", "short", 1), "side must be buy or sell"
        assert "error" in place_order("AAPL", "buy", 0), "quantity must be positive"
        assert "error" in place_order("AAPL", "buy", 10**9), "buying beyond cash must fail"
        assert "error" in place_order("FAKE", "buy", 1), "unknown ticker must fail"

        assert place_order("AAPL", "sell", 10).get("status") == "filled"
        assert get_portfolio()["positions"] == [], "fully sold position must be removed"
    finally:
        PORTFOLIO_PATH.write_text(original, encoding="utf-8")
    print("all self-tests passed")


if __name__ == "__main__":
    _self_test()
