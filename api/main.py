"""FastAPI wrapper around the GridSwing backtest engine, persisted to Postgres (Supabase).

POST /run   -> runs a backtest synchronously, persists it, returns id + metrics
GET  /runs/{id} -> fetches a persisted run (metrics, trades, equity curve)
GET  /runs  -> lists recent runs
"""
from __future__ import annotations

from datetime import date

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from gridswing import engine, metrics as metrics_mod
from gridswing.data import get_ohlc

from . import db

app = FastAPI(title="GridSwing API")
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)


class RunRequest(BaseModel):
    symbol: str
    start: str
    end: str | None = None
    capital: float
    step: float = 1.0
    target: float = 1.0
    lot_size: float = 10000.0
    mode: str = "classic"
    dynamic_weights: list[float] | None = None
    max_deploy: float = 100.0
    idle_yield: float = 0.0
    anchor_mode: str = "first_close"
    brokerage: float = 0.0
    stcg_rate: float = 30.0
    ltcg_rate: float = 12.5


@app.post("/run")
def run_backtest(req: RunRequest):
    end = req.end or date.today().isoformat()
    try:
        ohlc = get_ohlc(req.symbol, req.start, end)
        result = engine.run(
            req.symbol, ohlc, capital=req.capital, step=req.step, target=req.target,
            lot_size=req.lot_size, mode=req.mode, dynamic_weights=req.dynamic_weights,
            max_deploy=req.max_deploy, idle_yield=req.idle_yield, anchor_mode=req.anchor_mode,
            brokerage=req.brokerage, stcg_rate=req.stcg_rate, ltcg_rate=req.ltcg_rate,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    m = metrics_mod.compute_metrics(result)
    bh = metrics_mod.benchmark_buy_hold(ohlc, req.capital, req.ltcg_rate)

    with db.get_conn() as conn:
        run_id = db.insert_run(conn, {
            "symbol": req.symbol, "start_date": req.start, "end_date": end, "capital": req.capital,
            "step": req.step, "target": req.target, "lot_size": req.lot_size, "mode": req.mode,
            "dynamic_weights": req.dynamic_weights, "max_deploy": req.max_deploy,
            "idle_yield": req.idle_yield, "anchor_mode": req.anchor_mode, "brokerage": req.brokerage,
            "stcg_rate": req.stcg_rate, "ltcg_rate": req.ltcg_rate, "status": "completed",
            "metrics": m, "benchmark": bh,
        })
        trades = [
            {
                "buy_date": r.buy_date.date(), "sell_date": r.sell_date.date(), "qty": r.qty,
                "buy_price": r.buy_price, "sell_price": r.sell_price, "gross_pnl": r.gross_pnl,
                "holding_days": r.holding_days, "tax_class": r.tax_class, "tax": r.tax,
            }
            for r in result.trades.itertuples()
        ]
        db.insert_trades(conn, run_id, trades)
        equity_rows = [
            {"date": idx.date(), "cash": row.cash, "open_value": row.open_value, "equity": row.equity}
            for idx, row in result.equity_curve.iterrows()
        ]
        db.insert_equity_curve(conn, run_id, equity_rows)

    return {"run_id": run_id, "metrics": m, "benchmark": bh}


@app.get("/runs/{run_id}")
def get_run(run_id: str):
    with db.get_conn() as conn:
        result = db.fetch_run(conn, run_id)
    if result is None:
        raise HTTPException(status_code=404, detail="run not found")
    return result


@app.get("/runs")
def list_runs(limit: int = 20):
    with db.get_conn() as conn:
        return db.list_runs(conn, limit)


@app.get("/health")
def health():
    return {"status": "ok"}
