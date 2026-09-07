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

from gridswing import analysis, engine, metrics as metrics_mod
from gridswing.data import get_ohlc

from . import db

app = FastAPI(title="GridSwing API")
app.add_middleware(
    CORSMiddleware,
    # gridswing.pages.dev is the stable alias; *.gridswing.pages.dev covers each
    # per-deploy preview URL (e.g. 54459db4.gridswing.pages.dev).
    allow_origin_regex=r"https://([a-z0-9-]+\.)?gridswing\.pages\.dev",
    allow_methods=["*"], allow_headers=["*"],
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
    fills: str = "intraday"
    slippage: float = 0.1
    fill_buffer: float = 0.0005


class WalkForwardRequest(RunRequest):
    in_sample_end: str | None = None
    out_sample_start: str | None = None


def _json_safe(obj):
    """Recursively coerce numpy scalars (int64/float64/bool_) to native Python types
    so plain dict/list responses don't trip FastAPI's JSON encoding, and NaN (e.g. a
    daily ledger row with no next buy level) to None so the wire format stays valid JSON."""
    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_safe(v) for v in obj]
    if hasattr(obj, "item"):
        obj = obj.item()
    if isinstance(obj, float) and obj != obj:  # NaN
        return None
    return obj


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
            fills=req.fills, slippage=req.slippage, fill_buffer=req.fill_buffer,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    m = metrics_mod.compute_metrics(result)
    bh = metrics_mod.benchmark_buy_hold(ohlc, req.capital, req.ltcg_rate)
    spacing = analysis.suggest_spacing(ohlc, req.step)
    verdict = analysis.verdict(m, bh, spacing)

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

    daily_ledger = [
        {"date": idx.date().isoformat(), **row}
        for idx, row in result.daily_ledger.to_dict(orient="index").items()
    ]

    return {
        "run_id": run_id, "metrics": m, "benchmark": bh, "verdict": verdict,
        "daily_ledger": _json_safe(daily_ledger),
    }


@app.post("/sweep")
def run_sweep(req: RunRequest):
    end = req.end or date.today().isoformat()
    try:
        ohlc = get_ohlc(req.symbol, req.start, end)
        df = analysis.sweep(
            req.symbol, ohlc, req.capital, req.lot_size, req.max_deploy,
            stcg_rate=req.stcg_rate, ltcg_rate=req.ltcg_rate, fills=req.fills,
            slippage=req.slippage, fill_buffer=req.fill_buffer,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"rows": _json_safe(df.to_dict(orient="records"))}


@app.post("/walkforward")
def run_walkforward(req: WalkForwardRequest):
    end = req.end or date.today().isoformat()
    kwargs = {}
    if req.in_sample_end:
        kwargs["in_sample_end"] = req.in_sample_end
    if req.out_sample_start:
        kwargs["out_sample_start"] = req.out_sample_start
    try:
        ohlc = get_ohlc(req.symbol, req.start, end)
        result = analysis.walk_forward(
            req.symbol, ohlc, req.capital, req.lot_size, req.max_deploy,
            fills=req.fills, slippage=req.slippage, fill_buffer=req.fill_buffer, **kwargs,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return _json_safe(result)


@app.post("/blend")
def run_blend(req: RunRequest):
    end = req.end or date.today().isoformat()
    try:
        ohlc = get_ohlc(req.symbol, req.start, end)
        result = analysis.blend_5050(
            req.symbol, ohlc, req.capital, step=req.step, target=req.target, lot_size=req.lot_size,
            max_deploy=req.max_deploy, stcg_rate=req.stcg_rate, ltcg_rate=req.ltcg_rate, fills=req.fills,
            slippage=req.slippage, fill_buffer=req.fill_buffer,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    curve = result.pop("combined_equity_curve")
    equity_curve = [{"date": idx.date().isoformat(), "equity": float(v)} for idx, v in curve.items()]
    return {**_json_safe(result), "combined_equity_curve": equity_curve}


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
