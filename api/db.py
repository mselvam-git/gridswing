"""Thin psycopg2 wrapper for the gridswing_* tables. Connection string comes from DATABASE_URL."""
from __future__ import annotations

import json
import os
from contextlib import contextmanager

import psycopg2
import psycopg2.extras

DATABASE_URL = os.environ["DATABASE_URL"]


def _json_safe(obj):
    """json.dumps default= hook: numpy scalars aren't JSON-serializable natively."""
    if hasattr(obj, "item"):
        return obj.item()
    raise TypeError(f"not JSON serializable: {type(obj)}")


@contextmanager
def get_conn():
    conn = psycopg2.connect(DATABASE_URL)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def insert_run(conn, run: dict) -> str:
    with conn.cursor() as cur:
        cur.execute(
            """
            insert into gridswing_runs
                (symbol, start_date, end_date, capital, step, target, lot_size, mode,
                 dynamic_weights, max_deploy, idle_yield, anchor_mode, brokerage,
                 stcg_rate, ltcg_rate, status, metrics, benchmark)
            values (%(symbol)s, %(start_date)s, %(end_date)s, %(capital)s, %(step)s, %(target)s,
                    %(lot_size)s, %(mode)s, %(dynamic_weights)s, %(max_deploy)s, %(idle_yield)s,
                    %(anchor_mode)s, %(brokerage)s, %(stcg_rate)s, %(ltcg_rate)s, %(status)s,
                    %(metrics)s, %(benchmark)s)
            returning id
            """,
            {
                **run,
                "dynamic_weights": json.dumps(run["dynamic_weights"]) if run["dynamic_weights"] else None,
                "metrics": json.dumps(run["metrics"], default=_json_safe),
                "benchmark": json.dumps(run["benchmark"], default=_json_safe),
            },
        )
        return str(cur.fetchone()[0])


def insert_trades(conn, run_id: str, trades: list[dict]) -> None:
    if not trades:
        return
    with conn.cursor() as cur:
        psycopg2.extras.execute_values(
            cur,
            """insert into gridswing_trades
               (run_id, buy_date, sell_date, qty, buy_price, sell_price, gross_pnl, holding_days, tax_class, tax)
               values %s""",
            [
                (run_id, t["buy_date"], t["sell_date"], float(t["qty"]), float(t["buy_price"]),
                 float(t["sell_price"]), float(t["gross_pnl"]), int(t["holding_days"]),
                 t["tax_class"], float(t["tax"]))
                for t in trades
            ],
        )


def insert_equity_curve(conn, run_id: str, rows: list[dict]) -> None:
    if not rows:
        return
    with conn.cursor() as cur:
        psycopg2.extras.execute_values(
            cur,
            "insert into gridswing_equity_curves (run_id, date, cash, open_value, equity) values %s",
            [(run_id, r["date"], float(r["cash"]), float(r["open_value"]), float(r["equity"])) for r in rows],
        )


def fetch_run(conn, run_id: str) -> dict | None:
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("select * from gridswing_runs where id = %s", (run_id,))
        run = cur.fetchone()
        if not run:
            return None
        cur.execute("select * from gridswing_trades where run_id = %s order by sell_date", (run_id,))
        trades = cur.fetchall()
        cur.execute(
            "select date, cash, open_value, equity from gridswing_equity_curves where run_id = %s order by date",
            (run_id,),
        )
        equity_curve = cur.fetchall()
        return {"run": run, "trades": trades, "equity_curve": equity_curve}


def list_runs(conn, limit: int) -> list[dict]:
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            """select id, symbol, start_date, end_date, capital, step, target, metrics, created_at
               from gridswing_runs order by created_at desc limit %s""",
            (limit,),
        )
        return cur.fetchall()
