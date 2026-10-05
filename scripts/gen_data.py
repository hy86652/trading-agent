"""Generate mock daily price CSVs for the simulated trading agent.

Run once with `python scripts/gen_data.py`. The fixed seed and start date make
the output reproducible, so re-running never changes the data under the agent.
"""
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
PRICE_DIR = ROOT / "data" / "prices"

SEED = 42
N_DAYS = 100
START_DATE = "2026-01-02"
TICKERS = {
    "AAPL": 190.0,
    "MSFT": 410.0,
    "NVDA": 120.0,
    "GOOGL": 165.0,
    "AMZN": 185.0,
}


def generate_ticker(rng: np.random.Generator, start_price: float, dates: pd.DatetimeIndex) -> pd.DataFrame:
    """Build one ticker's OHLCV bars from a geometric random walk."""
    # Geometric walk keeps prices strictly positive; the drift and volatility are arbitrary mock values.
    returns = rng.normal(loc=0.0005, scale=0.02, size=len(dates))
    close = start_price * np.exp(np.cumsum(returns))
    # Each day opens at the previous close, so the bars form a continuous series.
    open_ = np.concatenate([[start_price], close[:-1]])
    spread = rng.uniform(0.002, 0.02, size=len(dates))
    high = np.maximum(open_, close) * (1 + spread)
    low = np.minimum(open_, close) * (1 - spread)
    volume = rng.integers(1_000_000, 10_000_000, size=len(dates))
    return pd.DataFrame(
        {
            "date": dates.strftime("%Y-%m-%d"),
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
        }
    ).round(2)


def main() -> None:
    """Write one CSV per ticker into data/prices/."""
    PRICE_DIR.mkdir(parents=True, exist_ok=True)
    # Weekday dates only: this is an approximation and ignores exchange holidays.
    dates = pd.bdate_range(START_DATE, periods=N_DAYS)
    rng = np.random.default_rng(SEED)
    for ticker, start_price in TICKERS.items():
        df = generate_ticker(rng, start_price, dates)
        df.to_csv(PRICE_DIR / f"{ticker}.csv", index=False)
        print(f"wrote {ticker}: {len(df)} rows")


if __name__ == "__main__":
    main()
