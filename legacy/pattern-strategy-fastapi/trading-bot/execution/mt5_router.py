"""MT5 live order router — STUB for v2.

When you set up MT5 on a Windows VPS:

1. pip install MetaTrader5
2. In server.py, after loading config, replace `PaperRouter` with `MT5Router`
   when `config.account.mode == 'live'`.
3. Implement the methods below using:

       import MetaTrader5 as mt5

       mt5.initialize(login=..., server=..., password=...)
       mt5.order_send({
           "action":     mt5.TRADE_ACTION_DEAL,
           "symbol":     symbol,
           "volume":     lots,
           "type":       mt5.ORDER_TYPE_BUY if side == 'buy' else mt5.ORDER_TYPE_SELL,
           "price":      mt5.symbol_info_tick(symbol).ask,
           "sl":         sl,
           "tp":         tp,
           "deviation":  slippage_pips * 10,
           "magic":      magic_number,
           "comment":    f"{setup}-{timeframe}",
           "type_time":  mt5.ORDER_TIME_GTC,
           "type_filling": mt5.ORDER_FILLING_IOC,
       })

4. Use mt5.positions_get() for open_positions, mt5.history_deals_get() for closed.

Until then this router is intentionally non-functional. Switching to live mode
without implementing it will surface a clear error, so paper users can't
accidentally fire real orders.
"""
from __future__ import annotations

from typing import List, Optional

from marketdata.base import Bar
from .base import OrderFill, OrderRequest, OrderRouter, PositionUpdate


class MT5Router:
    mode = "live"

    def __init__(self, *_, **__) -> None:
        raise NotImplementedError(
            "Live MT5 router not yet implemented. See execution/mt5_router.py "
            "for the wiring checklist. The bot will refuse to start in live "
            "mode until this is filled in."
        )

    def submit(self, req: OrderRequest) -> Optional[OrderFill]:  # pragma: no cover
        raise NotImplementedError

    def open_positions(self) -> List[OrderFill]:  # pragma: no cover
        raise NotImplementedError

    def on_bar(self, symbol: str, bar: Bar) -> List[PositionUpdate]:  # pragma: no cover
        raise NotImplementedError

    def flatten_all(self, reason: str) -> List[PositionUpdate]:  # pragma: no cover
        raise NotImplementedError

    def equity(self) -> float:  # pragma: no cover
        raise NotImplementedError

    def closed_trades(self) -> list:  # pragma: no cover
        raise NotImplementedError
