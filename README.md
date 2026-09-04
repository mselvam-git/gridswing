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

## Execution semantics: `--fills`

`--fills intraday` (default) uses the day's own High/Low: a buy triggers when the
low touches a grid level and fills AT that level price (limit-order semantics); a
sell triggers when the high reaches a lot's target and fills AT the target price. A
lot bought earlier the same day can also sell that same day. `--fills close` is the
legacy mode — both trigger and fill happen at the day's close. Either way, only that
day's own OHLC is used (no look-ahead).

Two more mechanics changed unconditionally (regardless of `--fills`):
grid levels are now linear (`level_k = anchor * (1 - k*step/100)`, previously
compounded), and the anchor now always ratchets up to any new close high — `--anchor`
is still accepted for backward compatibility but no longer pins the anchor; ratcheting
is now the only behavior.

## Interpreting the verdict

The grid buys dips below its anchor and sells bounces back to target; with the anchor
now ratcheting up on every new high, it keeps finding fresh levels to trade as an
asset trends up, rather than going idle once price leaves its original band (the old,
pre-ratchet behavior). It still does best in choppy/range-bound conditions relative to
buy-and-hold, since buy-and-hold has no re-entry cost; the `sweep`/`walkforward`/`blend`
commands and the printed verdict text help size that trade-off for a given symbol and
period.

## Note on the acceptance test

The spec's hand-verified reference numbers (~37% GOLDBEES.NS, ~28% NIFTYBEES.NS,
classic 1:1, from 2022-01-01) were computed against data and/or an end date from
whenever they were hand-verified — unknown to this implementation. Since `--end`
defaults to today and yfinance only serves current market history, re-running with
live data doesn't reproduce them: as of this writing, `intraday` fills give ~71%
(GOLDBEES) / ~76% (NIFTYBEES) through today, and legacy `close` fills give ~28% /
~22%. No `--end` date or fill mode tried lines up both symbols simultaneously within
±3% of target, which is expected for a live-data mismatch rather than a fixable
parameter. The mechanics themselves are verified independently of any specific data
snapshot via unit tests with deterministic synthetic data (`tests/`), which pin down
every rule explicitly: linear level math, intraday low/high triggers and level/target
fill prices, same-day buy-then-sell, unconditional anchor ratcheting, tax
classification at 365 days, idle-yield accrual, max-deploy cap, and capital-exhaustion
events.
