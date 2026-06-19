"""Entry point. Loads config, wires components, starts FastAPI + the engine."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

import uvicorn
import yaml

from execution.paper import PaperRouter
from marketdata.mt5_source import MT5Source
from server.api import WSHub, build_app
from strategy.confirmation import ConfirmationConfig
from strategy.correlation import CorrelationConfig
from strategy.engine import EngineConfig, StrategyEngine
from strategy.news import NewsConfig, NewsFilter
from strategy.risk import RiskConfig, RiskState, record_close
from telegram_notifier import TelegramNotifier
from notifications import build_notifier   # fans out to Telegram + Discord

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s  %(levelname)-7s  %(name)s  %(message)s",
)
log = logging.getLogger("server")


ROOT = Path(__file__).resolve().parent


# --------------------------------------------------------------------------- #
# Config loading
# --------------------------------------------------------------------------- #
def load_config() -> Dict[str, Any]:
    path = ROOT / "config.yaml"
    if not path.exists():
        path = ROOT / "config.example.yaml"
        log.warning("config.yaml not found — running with config.example.yaml")
    with path.open() as f:
        return yaml.safe_load(f)


# --------------------------------------------------------------------------- #
# Wiring
# --------------------------------------------------------------------------- #
def build_router(cfg: Dict[str, Any]):
    mode = cfg["account"]["mode"]
    if mode == "live":
        # Imported lazily so paper users never hit the NotImplementedError
        from execution.mt5_router import MT5Router
        return MT5Router()
    instruments = {
        i["display"]: {"pip_size": i["pip_size"], "pip_value": i["pip_value"]}
        for i in cfg["instruments"]
    }
    def _critical_alert(text: str) -> None:
        """Route CRITICAL alerts (sizing-invariant violations) to Telegram
        without blocking the trading loop."""
        tg = _RT.telegram
        if tg is None:
            return
        import threading
        threading.Thread(target=tg._send, args=(text,), daemon=True).start()

    return PaperRouter(
        starting_equity=cfg["account"]["starting_equity"],
        instruments=instruments,
        scale_out=cfg.get("risk", {}).get("scale_out", True),
        alert=_critical_alert,
        spread_stress=cfg.get("execution", {}).get("spread_stress", {}) or {},
    )


def build_news(cfg: Dict[str, Any]) -> NewsFilter:
    n = cfg["news"]
    return NewsFilter(NewsConfig(
        source=n.get("source", "forexfactory"),
        forexfactory_url=n.get("forexfactory_url", "https://www.forexfactory.com/calendar"),
        manual_events_file=n.get("manual_events_file", "state/manual_news.json"),
        block_minutes_before=n.get("block_minutes_before", 30),
        block_minutes_after=n.get("block_minutes_after", 30),
        high_impact_only=n.get("high_impact_only", True),
    ))


def build_engine(
    cfg: Dict[str, Any],
    router,
    news: NewsFilter,
    hub: WSHub,
    signals_log: List[dict],
    risk_state: RiskState,
    mt5_store: Dict[str, Any],
) -> StrategyEngine:
    e_cfg = EngineConfig(
        instruments=cfg["instruments"],
        timeframes=cfg["timeframes"],
        lookback_bars=cfg.get("lookback_bars", 300),
        poll_seconds=cfg["engine"]["poll_seconds"],
        initial_history_bars=cfg["engine"]["initial_history_bars"],
        pattern_flags=cfg["strategy"]["patterns"],
    )
    confirm_cfg = ConfirmationConfig(
        min_body_ratio=cfg["strategy"]["confirmation"]["min_body_ratio"],
        max_opposing_wick_ratio=cfg["strategy"]["confirmation"]["max_opposing_wick_ratio"],
        momentum_lookback_bars=cfg["strategy"]["confirmation"]["momentum"]["lookback_bars"],
        momentum_max_atr_ratio=cfg["strategy"]["confirmation"]["momentum"]["max_atr_ratio"],
    )
    _corr = cfg.get("strategy", {}).get("correlation", {}) or {}
    corr_cfg = CorrelationConfig(
        enabled=_corr.get("enabled", True),
        lookback_bars=_corr.get("lookback_bars", 100),
        strong_threshold=_corr.get("strong_threshold", 0.70),
        direction_lookback=_corr.get("direction_lookback", 10),
        block_on_conflict=_corr.get("block_on_conflict", True),
        ci_period=_corr.get("ci_period", 14),
        ci_choppy_threshold=_corr.get("ci_choppy_threshold", 61.8),
        dedupe_correlated=_corr.get("dedupe_correlated", True),
    )
    risk_cfg = RiskConfig(
        per_trade_pct=cfg["risk"]["per_trade_pct"],
        min_rr=cfg["risk"]["min_rr"],
        daily_trade_cap=cfg["risk"]["daily_trade_cap"],
        weekly_trade_cap=cfg["risk"]["weekly_trade_cap"],
        daily_loss_pct=cfg["risk"]["daily_loss_pct"],
        max_drawdown_pct=cfg["risk"]["max_drawdown_pct"],
        kill_switch_file=cfg["risk"]["kill_switch_file"],
    )

    tg_cfg = cfg.get("telegram", {})
    notify_signal   = tg_cfg.get("notify_signal_detected", True)
    notify_filled   = tg_cfg.get("notify_order_filled", True)

    async def emit(event: str, payload: dict) -> None:
        if event.startswith("signal:") or event.startswith("order:") or event.startswith("position:"):
            signals_log.append({"event": event, "payload": payload, "ts": _now_iso()})
            if len(signals_log) > 5000:
                del signals_log[:2500]
            _append_signal_log(event, payload)   # persist to disk for offline analysis
        await hub.emit(event, payload)

        # ── Telegram notifications ───────────────────────────────────────────
        tg = _RT.telegram
        if tg is None:
            return
        try:
            if event == "signal:accepted" and notify_signal:
                sig = payload.get("signal", {})
                import threading
                threading.Thread(
                    target=tg.signal_detected, args=(sig,), daemon=True
                ).start()
            elif event == "order:filled" and notify_filled:
                import threading
                threading.Thread(
                    target=tg.order_filled, args=(payload,), daemon=True
                ).start()
        except Exception as _tg_exc:
            log.debug("Telegram notification error: %s", _tg_exc)

    engine = StrategyEngine(
        cfg=e_cfg,
        data=MT5Source(mt5_store),
        router=router,
        risk_cfg=risk_cfg,
        risk_state=risk_state,
        confirm_cfg=confirm_cfg,
        news=news,
        emit=emit,
        corr_cfg=corr_cfg,
        mt5_store=mt5_store,
        ltf_exit_cfg=cfg.get("risk", {}).get("ltf_reversal_exit", {}),
        # Anchor to the server's own directory — the default was relative to the
        # process CWD, so starting the server from anywhere but trading-bot/
        # silently wrote journal files to the wrong place.
        journal_dir=str(ROOT / "state" / "trade_journal"),
        cooldown_cfg=_cooldown_cfg(cfg),
        htf_cfg=cfg.get("strategy", {}).get("htf_filter", {}) or {},
        min_clarity_score=cfg.get("strategy", {}).get("min_clarity_score", 0.0),
        indicator_cfg=cfg.get("strategy", {}).get("indicator_filter", {}) or {},
        volume_cfg=cfg.get("strategy", {}).get("volume_profile", {}) or {},
    )
    # Shadow mode: track gate-rejected signals to their hypothetical TP/SL.
    sh = cfg.get("strategy", {}).get("shadow_mode", {}) or {}
    if sh.get("enabled", False):
        from strategy.shadow import ShadowTracker
        engine.shadow = ShadowTracker(
            outcomes_path=str(ROOT / "state" / "shadow_outcomes.jsonl"),
            pending_path=str(ROOT / "state" / "shadow_pending.json"),
            max_bars=int(sh.get("max_bars", 100)),
        )
    return engine


def _cooldown_cfg(cfg: Dict[str, Any]):
    """CooldownConfig from config.yaml strategy.cooldown (None = disabled)."""
    from strategy.cooldown import CooldownConfig
    c = cfg.get("strategy", {}).get("cooldown", {}) or {}
    if not c.get("enabled", False):
        return None
    return CooldownConfig(
        enabled=True,
        atr_mult=float(c.get("atr_mult", 0.5)),
        bars=int(c.get("bars", 10)),
    )


# --------------------------------------------------------------------------- #
# Mode switching
# --------------------------------------------------------------------------- #
class _Runtime:
    """Mutable holder for the engine + router so /api/mode can swap them."""
    def __init__(self) -> None:
        self.engine: StrategyEngine | None = None
        self.router = None
        self.engine_task: asyncio.Task | None = None
        self.risk_state: RiskState | None = None
        self.signals_log: List[dict] = []
        self.cfg: Dict[str, Any] | None = None
        self.news: NewsFilter | None = None
        self.hub: WSHub | None = None
        self.starting_equity: float = 0.0
        self.mt5_store: Dict[str, Any] = {}   # populated by /api/mt5_feed
        self.command_queue: List[dict] = []    # populated by news_agent, polled by MT5 EA
        self.enabled_pairs: List[str] = []     # synced from dashboard Pairs Manager toggles
        self.enabled_patterns: List[str] = []  # synced from dashboard Patterns Manager toggles
        self.watch_levels: List[dict] = []     # video/manual pairs+levels to trade per strategy
        self.telegram: TelegramNotifier | None = None  # Telegram notifier (None = disabled)


_RT = _Runtime()


def _set_mode(new_mode: str) -> tuple[bool, str]:
    if _RT.cfg is None:
        return False, "Runtime not initialized"
    if new_mode == _RT.cfg["account"]["mode"]:
        return True, f"Already in {new_mode} mode"

    if new_mode == "live":
        try:
            from execution.mt5_router import MT5Router  # noqa: F401
            MT5Router()
        except NotImplementedError as exc:
            return False, str(exc)
        except Exception as exc:  # noqa: BLE001
            return False, f"MT5 init failed: {exc}"

    _RT.cfg["account"]["mode"] = new_mode

    if _RT.engine:
        _RT.engine.stop()
    return True, (
        f"Switched to {new_mode} mode. Stop and restart the bot to apply "
        "(or wait for the current tick to finish)."
    )


def _halt() -> None:
    Path(_RT.cfg["risk"]["kill_switch_file"]).parent.mkdir(parents=True, exist_ok=True)
    Path(_RT.cfg["risk"]["kill_switch_file"]).touch()


def _resume() -> None:
    p = Path(_RT.cfg["risk"]["kill_switch_file"])
    if p.exists():
        p.unlink()


def _reset() -> None:
    """Reset the paper trading session to a clean slate."""
    # Reset the paper router (clears positions, trades, restores equity)
    if hasattr(_RT.router, "reset"):
        _RT.router.reset()

    # Reset risk counters back to starting values
    if _RT.risk_state is not None and _RT.cfg is not None:
        eq = _RT.cfg["account"]["starting_equity"]
        _RT.risk_state.starting_equity   = eq
        _RT.risk_state.current_equity    = eq
        _RT.risk_state.realized_today    = 0.0
        _RT.risk_state.realized_this_week = 0.0
        _RT.risk_state.trades_today      = 0
        _RT.risk_state.trades_this_week  = 0
        _RT.risk_state.halted_for_dd     = False

    # Also remove the kill-switch if it was set
    if _RT.cfg:
        p = Path(_RT.cfg["risk"]["kill_switch_file"])
        if p.exists():
            p.unlink()

    log.info("Paper session reset — all counters and positions cleared")


def _status_snapshot() -> dict:
    # If the MT5 EA bridge has sent data, use it as the live account source.
    m5 = _RT.mt5_store
    if m5:
        acct = m5.get("account", {})
        snap: Dict[str, Any] = {
            "running": True,
            "mode": "live",
            "data_source": "mt5_feed",
            "mt5_last_update": m5.get("_received_at"),
            "equity": acct.get("equity", 0.0),
            "balance": acct.get("balance", 0.0),
            "margin": acct.get("margin", 0.0),
            "free_margin": acct.get("free_margin", 0.0),
            "currency": acct.get("currency", "USD"),
            "broker": acct.get("broker", ""),
            "login": acct.get("login"),
            "open_positions": m5.get("open_positions", []),
            "closed_trades": m5.get("closed_today", []),
            "upcoming_news": [e.to_dict() for e in (_RT.news.upcoming(24) if _RT.news else [])],
        }
        # Still include engine caps if the engine is running alongside
        if _RT.cfg:
            snap["config_summary"] = {
                "instruments": [i["display"] for i in _RT.cfg["instruments"]],
                "timeframes": _RT.cfg["timeframes"],
                "per_trade_pct": _RT.cfg["risk"]["per_trade_pct"],
                "min_rr": _RT.cfg["risk"]["min_rr"],
                "daily_trade_cap": _RT.cfg["risk"]["daily_trade_cap"],
                "weekly_trade_cap": _RT.cfg["risk"]["weekly_trade_cap"],
                "patterns_enabled": [k for k, v in _RT.cfg["strategy"]["patterns"].items() if v],
            }
        return snap

    # Paper engine running, but the EA hasn't pushed an account snapshot yet.
    # The engine's bar data still comes from MT5 (MT5Source) — there is no
    # yfinance anywhere in the pipeline.
    if _RT.engine is None or _RT.router is None or _RT.risk_state is None:
        return {"running": False, "mode": _RT.cfg["account"]["mode"] if _RT.cfg else "unknown"}
    return {
        "running": _RT.engine.running,
        "mode": _RT.router.mode,
        "data_source": "mt5",
        "equity": round(_RT.router.equity(), 2),
        "starting_equity": _RT.starting_equity,
        "halted": Path(_RT.cfg["risk"]["kill_switch_file"]).exists(),
        "trades_today": _RT.risk_state.trades_today,
        "trades_this_week": _RT.risk_state.trades_this_week,
        "realized_today": round(_RT.risk_state.realized_today, 2),
        "realized_this_week": round(_RT.risk_state.realized_this_week, 2),
        "open_positions": [p.to_dict() for p in _RT.router.open_positions()],
        "closed_trades": [p.to_dict() for p in _RT.router.closed_trades()],
        "upcoming_news": [e.to_dict() for e in (_RT.news.upcoming(24) if _RT.news else [])],
        "config_summary": {
            "instruments": [i["display"] for i in _RT.cfg["instruments"]],
            "timeframes": _RT.cfg["timeframes"],
            "per_trade_pct": _RT.cfg["risk"]["per_trade_pct"],
            "min_rr": _RT.cfg["risk"]["min_rr"],
            "daily_trade_cap": _RT.cfg["risk"]["daily_trade_cap"],
            "weekly_trade_cap": _RT.cfg["risk"]["weekly_trade_cap"],
            "patterns_enabled": [k for k, v in _RT.cfg["strategy"]["patterns"].items() if v],
        },
    }


def _agent_trades() -> dict:
    """The AGENT's own paper trades (independent of the EA's account snapshot).

    Open positions are enriched with the live current price + unrealized P&L
    using the EA's price feed, so the dashboard shows running P&L for the bot's
    simulated trades on the $1000 paper account.
    """
    if _RT.router is None:
        return {"open": [], "closed": [], "starting_equity": _RT.starting_equity,
                "equity": _RT.starting_equity}

    # live prices keyed by symbol (dashboard name) from the EA feed
    prices = {}
    for p in (_RT.mt5_store.get("prices", []) or []):
        prices[str(p.get("symbol", "")).upper()] = p

    open_out = []
    for f in _RT.router.open_positions():
        d = f.to_dict()
        entry = d.get("fill_price")
        sym = str(d.get("symbol", "")).upper()
        px = prices.get(sym)
        cur = None
        pnl = None
        if px is not None and entry:
            bid = float(px.get("bid") or 0)
            cur = bid if bid > 0 else entry
            tv = px.get("tick_value")
            ts = px.get("tick_size")
            sign = 1.0 if d.get("side") == "buy" else -1.0
            try:
                if tv and ts and float(ts) > 0:
                    pnl = round((cur - entry) * sign / float(ts) * float(tv) * float(d.get("lots", 0)), 2)
            except (TypeError, ValueError, ZeroDivisionError):
                pnl = None
        d["entry"] = entry
        d["current"] = cur if cur is not None else entry
        d["unrealized_pnl"] = pnl if pnl is not None else 0.0
        open_out.append(d)

    # Currency: prefer the live broker account currency from the EA feed, else
    # the configured account currency. So the dashboard always labels P&L in the
    # real account currency (e.g. INR).
    acct = _RT.mt5_store.get("account", {}) if _RT.mt5_store else {}
    currency = acct.get("currency") or (_RT.cfg["account"]["currency"] if _RT.cfg else "USD")
    return {
        "open": open_out,
        "closed": [p.to_dict() for p in _RT.router.closed_trades()],
        "starting_equity": _RT.starting_equity,
        "equity": round(_RT.router.equity(), 2),
        "mode": getattr(_RT.router, "mode", "paper"),
        "currency": currency,
    }


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


# --------------------------------------------------------------------------- #
# Dashboard selection persistence (pairs + patterns survive restarts)
# --------------------------------------------------------------------------- #
_PAIRS_FILE    = ROOT / "state" / "enabled_pairs.json"
_PATTERNS_FILE = ROOT / "state" / "enabled_patterns.json"


def _load_json_list(path: Path) -> List[str]:
    try:
        if path.exists():
            data = json.loads(path.read_text())
            if isinstance(data, list):
                return [str(x) for x in data]
    except Exception as exc:  # noqa: BLE001
        log.warning("Could not load %s: %s", path.name, exc)
    return []


def _save_json_list(path: Path, items: List[str]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(list(items)))
    except Exception as exc:  # noqa: BLE001
        log.warning("Could not save %s: %s", path.name, exc)


_SIGNALS_FILE = ROOT / "state" / "signals.log"
_signal_log_writes = 0


def _append_signal_log(event: str, payload: dict) -> None:
    """Append one compact JSONL record of a signal/order decision, so 'why was
    (or wasn't) a trade taken' is answerable offline (and by the daily review).
    Bounded: trimmed to the last ~6000 lines periodically."""
    global _signal_log_writes
    try:
        sig = payload.get("signal", {}) if isinstance(payload, dict) else {}
        notes = sig.get("notes") or []
        checks = payload.get("checks") or []
        failed = payload.get("failed_check")
        # The true rejection reason is the FAILING check's detail. The old
        # `notes[-1]` heuristic mislabelled candle-anatomy failures as
        # "✓ Slow approach OK" (the momentum note happened to be last).
        reason = None
        for c in checks:
            if not c.get("passed", True):
                reason = "✗ " + (c.get("detail") or c.get("name") or "")
                break
        if reason is None and notes:
            reason = notes[-1]
        rec = {
            "ts": _now_iso(),
            "event": event,                       # signal:accepted | signal:rejected | order:filled | ...
            "stage": payload.get("stage"),        # which gate rejected (confirmation/choppiness/correlation/news/risk)
            "symbol": sig.get("symbol") or payload.get("symbol"),
            "tf": sig.get("timeframe"),
            "setup": sig.get("setup"),
            "side": sig.get("side"),
            "rr": sig.get("rr"),
            "clarity": sig.get("clarity_score"),  # pattern clarity score (0-100; None pre-upgrade)
            "failed_check": failed,               # machine-readable: which check killed it
            "checks": checks or None,             # full per-check results {name,passed,value,threshold,detail}
            "reason": reason,                     # human-readable, now guaranteed to be the failure
        }
        _SIGNALS_FILE.parent.mkdir(parents=True, exist_ok=True)
        with _SIGNALS_FILE.open("a") as fh:
            fh.write(json.dumps(rec) + "\n")
        _signal_log_writes += 1
        if _signal_log_writes % 300 == 0:         # occasional trim
            lines = _SIGNALS_FILE.read_text().splitlines()
            if len(lines) > 6000:
                _SIGNALS_FILE.write_text("\n".join(lines[-6000:]) + "\n")
    except Exception as exc:  # noqa: BLE001
        log.debug("signal log write failed: %s", exc)


_LEVELS_FILE = ROOT / "state" / "watch_levels.json"


def _load_watch_levels() -> List[dict]:
    try:
        if _LEVELS_FILE.exists():
            data = json.loads(_LEVELS_FILE.read_text())
            if isinstance(data, list):
                return data
    except Exception as exc:  # noqa: BLE001
        log.warning("Could not load watch_levels.json: %s", exc)
    return []


def _persist_levels(levels: List[dict]) -> None:
    try:
        _LEVELS_FILE.parent.mkdir(parents=True, exist_ok=True)
        _LEVELS_FILE.write_text(json.dumps(list(levels), indent=2))
    except Exception as exc:  # noqa: BLE001
        log.warning("Could not save watch_levels.json: %s", exc)


def _persist_pairs(items: List[str]) -> None:
    _save_json_list(_PAIRS_FILE, items)


def _persist_patterns(items: List[str]) -> None:
    _save_json_list(_PATTERNS_FILE, items)


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
async def _run() -> None:
    cfg = load_config()
    _RT.cfg = cfg

    starting_equity = cfg["account"]["starting_equity"]
    _RT.starting_equity = starting_equity
    _RT.risk_state = RiskState(
        starting_equity=starting_equity,
        current_equity=starting_equity,
    )
    _RT.risk_state.rollover_if_needed()
    _RT.hub = WSHub()
    _RT.news = build_news(cfg)
    _RT.router = build_router(cfg)
    # Seed equity from existing paper ledger if present
    _RT.risk_state.current_equity = _RT.router.equity()

    _RT.engine = build_engine(cfg, _RT.router, _RT.news, _RT.hub, _RT.signals_log, _RT.risk_state, _RT.mt5_store)
    _RT.telegram = build_notifier(cfg)   # None if telegram.enabled: false

    # Restore the dashboard's last pair/pattern selection so it survives a
    # restart — the EA then keeps watching all chosen pairs without the
    # dashboard needing to be open, and the engine respects them from tick 1.
    saved_pairs = _load_json_list(_PAIRS_FILE)
    saved_patterns = _load_json_list(_PATTERNS_FILE)
    if saved_pairs:
        _RT.enabled_pairs[:] = saved_pairs
        _RT.engine.set_enabled_pairs(saved_pairs)
        log.info("Restored %d enabled pairs from dashboard selection", len(saved_pairs))
    if saved_patterns:
        _RT.enabled_patterns[:] = saved_patterns
        _RT.engine.set_enabled_patterns(saved_patterns)
        log.info("Restored enabled patterns: %s", ", ".join(saved_patterns))

    # Restore video/manual watch levels and hand them to the engine.
    _RT.watch_levels[:] = _load_watch_levels()
    _RT.engine.set_watch_levels(_RT.watch_levels)
    _RT.engine._notify_levels_changed = _persist_levels
    if _RT.watch_levels:
        log.info("Restored %d watch level(s)", len([l for l in _RT.watch_levels if not l.get('consumed')]))

    def _apply_paper_sl(cmd: dict) -> bool:
        """Apply a news-agent command directly to a PAPER position:
        move_sl_be / trail_sl → modify the stop; close_position → close the
        full remaining size at the current market quote (cut-loss).
        Returns True only when the ticket matched a paper position."""
        router = _RT.router
        if router is None or getattr(router, "mode", "") != "paper":
            return False
        try:
            ticket = int(cmd.get("ticket", 0))
        except (TypeError, ValueError):
            return False
        if ticket <= 0:
            return False
        reason = str(cmd.get("reason", ""))[:120]

        if cmd.get("type") == "close_position":
            # Current bid from the EA price feed for this symbol.
            sym = str(cmd.get("symbol", "")).upper().split(".")[0]
            bid = 0.0
            for p in (_RT.mt5_store.get("prices", []) or []):
                if str(p.get("symbol", "")).upper().split(".")[0] == sym:
                    try:
                        bid = float(p.get("bid") or 0.0)
                    except (TypeError, ValueError):
                        bid = 0.0
                    break
            if bid <= 0:
                log.warning("Cut-loss for #%s: no live bid for %s — not applied", ticket, sym)
                return False
            ups = router.close_at_market(ticket, bid, reason="news_cut_loss")
            for u in ups:
                record_close(_RT.risk_state, u.pnl)
                if _RT.engine is not None:
                    try:
                        _RT.engine._journal_closure(u)
                    except Exception:  # noqa: BLE001
                        pass
                try:
                    asyncio.get_running_loop().create_task(
                        _RT.hub.emit("position:closed", u.to_dict()))
                except RuntimeError:
                    pass
            return bool(ups)

        try:
            new_sl = float(cmd.get("new_sl", 0) or 0)
        except (TypeError, ValueError):
            return False
        if new_sl <= 0:
            return False
        return bool(router.modify_sl(ticket, new_sl, reason=reason))

    dashboard_dir = (ROOT.parent / "trading-dashboard").resolve()
    serve_dashboard = cfg["server"].get("serve_dashboard", True) and dashboard_dir.exists()

    app = build_app(
        config=cfg,
        state_provider=_status_snapshot,
        set_mode=_set_mode,
        halt=_halt,
        resume=_resume,
        reset=_reset,
        hub=_RT.hub,
        signals_log=_RT.signals_log,
        agent_trades_provider=_agent_trades,
        mt5_store=_RT.mt5_store,
        command_queue=_RT.command_queue,
        enabled_pairs=_RT.enabled_pairs,
        enabled_patterns=_RT.enabled_patterns,
        notify_pairs_changed=lambda pairs: _RT.engine.set_enabled_pairs(pairs) if _RT.engine else None,
        notify_patterns_changed=lambda pats: _RT.engine.set_enabled_patterns(pats) if _RT.engine else None,
        persist_pairs=_persist_pairs,
        persist_patterns=_persist_patterns,
        watch_levels=_RT.watch_levels,
        notify_levels_changed=lambda lv: _RT.engine.set_watch_levels(lv) if _RT.engine else None,
        persist_levels=_persist_levels,
        apply_paper_sl=_apply_paper_sl,
        dashboard_dir=dashboard_dir if serve_dashboard else None,
    )

    _RT.engine_task = asyncio.create_task(_RT.engine.start())

    cfg_server = cfg["server"]
    uv_cfg = uvicorn.Config(
        app,
        host=cfg_server.get("host", "127.0.0.1"),
        port=cfg_server.get("port", 8765),
        log_level="info",
    )
    server = uvicorn.Server(uv_cfg)
    log.info("Server listening on http://%s:%d", uv_cfg.host, uv_cfg.port)
    if serve_dashboard:
        log.info("Dashboard served at the same URL")

    try:
        await server.serve()
    finally:
        if _RT.engine:
            _RT.engine.stop()
        if _RT.engine_task:
            await _RT.engine_task


def main() -> int:
    try:
        asyncio.run(_run())
        return 0
    except KeyboardInterrupt:
        log.info("Interrupted by user")
        return 0


if __name__ == "__main__":
    sys.exit(main())
