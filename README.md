# GridSwing

Backtesting engine for a long-only grid / DCA swing-trading strategy on Indian ETFs
(GoldBees, NiftyBees, or any NSE symbol yfinance supports).

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

## Example runs

```bash
.venv/bin/python -m gridswing run --symbol GOLDBEES.NS --start 2022-01-01 --capital 100000 \
  --step 1.0 --target 1.0 --mode classic --lot-size 10000 \
  --max-deploy 100 --idle-yield 6.5 --stcg-rate 30 --ltcg-rate 12.5

.venv/bin/python -m gridswing sweep --symbol GOLDBEES.NS --start 2022-01-01 --capital 100000
.venv/bin/python -m gridswing walkforward --symbol GOLDBEES.NS --start 2022-01-01 --capital 100000
.venv/bin/python -m gridswing blend --symbol GOLDBEES.NS --start 2022-01-01 --capital 100000
```

`run` writes `output/<symbol>_<timestamp>/` with `trades.csv`, `equity_curve.csv`,
`metrics.json`, `summary.md`, `equity.png`, `drawdown.png`.

## Tests

```bash
.venv/bin/python -m pytest
```

## Phase 2: API + frontend

`api/main.py` is a FastAPI wrapper (`POST /run`, `GET /runs/{id}`, `GET /runs`) that
persists runs/trades/equity_curves to a Postgres database (Supabase-hosted) via a
direct `DATABASE_URL` connection. `web/index.html` is a static, dependency-free
frontend (Chart.js from CDN) with an input form, results table, and equity/drawdown
charts — set `window.GRIDSWING_API_BASE` to the deployed API URL before `Chart.js`
runs, or edit the fallback in the script tag.

Run the API locally:

```bash
DATABASE_URL=postgresql://... .venv/bin/uvicorn api.main:app --reload
```

## Interpreting the verdict

The grid strategy only buys below its anchor price (fixed at the first close, unless
`--anchor trailing`). It profits from range-bound/sideways chop — each dip triggers a
buy, each bounce back to target triggers a sell. In a strong sustained bull run the
grid mostly sits idle below the price and buy-and-hold wins by a wide margin; the
`sweep`/`walkforward`/`blend` commands and the printed verdict text help size that
trade-off for a given symbol and period.

## Note on the acceptance test

The spec's hand-verified reference numbers (~37%/~42% absolute return for
GOLDBEES.NS from 2022-01-01) were computed against data as of whenever the spec was
written. Since `--end` defaults to today and yfinance only serves current market
history, re-running with today's data gives different numbers — GOLDBEES has been in
a strong bull run since 2022, so the grid (which only trades below its fixed anchor)
goes idle after its first couple of trades and returns closer to buy-and-hold's gross
gain being missed. This was verified against unit tests with deterministic synthetic
data instead (`tests/`), which pin down the actual mechanics: level math, buy/sell
triggers, lot independence, tax classification at 365 days, idle-yield accrual,
max-deploy cap, and capital-exhaustion events.
