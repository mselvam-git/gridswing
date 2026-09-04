"""Trade ledger: records each sell as a closed round-trip with tax classification."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Trade:
    buy_date: object
    sell_date: object
    symbol: str
    qty: float
    buy_price: float
    sell_price: float
    gross_pnl: float
    holding_days: int
    tax_class: str
    tax: float


@dataclass
class Ledger:
    symbol: str
    stcg_rate: float
    ltcg_rate: float
    trades: list[Trade] = field(default_factory=list)

    def record_sell(self, buy_date, sell_date, buy_price: float, sell_price: float, qty: float) -> Trade:
        holding_days = (sell_date - buy_date).days
        tax_class = "STCG" if holding_days < 365 else "LTCG"
        gross_pnl = (sell_price - buy_price) * qty
        rate = self.stcg_rate if tax_class == "STCG" else self.ltcg_rate
        tax = gross_pnl * rate / 100 if gross_pnl > 0 else 0.0
        trade = Trade(
            buy_date=buy_date, sell_date=sell_date, symbol=self.symbol, qty=qty,
            buy_price=buy_price, sell_price=sell_price, gross_pnl=gross_pnl,
            holding_days=holding_days, tax_class=tax_class, tax=tax,
        )
        self.trades.append(trade)
        return trade

    def total_tax(self) -> float:
        return sum(t.tax for t in self.trades)

    def tax_by_class(self) -> dict[str, float]:
        out = {"STCG": 0.0, "LTCG": 0.0}
        for t in self.trades:
            out[t.tax_class] += t.tax
        return out

    def to_dataframe(self):
        import pandas as pd
        if not self.trades:
            return pd.DataFrame(columns=[
                "buy_date", "sell_date", "symbol", "qty", "buy_price", "sell_price",
                "gross_pnl", "holding_days", "tax_class", "tax",
            ])
        return pd.DataFrame([vars(t) for t in self.trades])
