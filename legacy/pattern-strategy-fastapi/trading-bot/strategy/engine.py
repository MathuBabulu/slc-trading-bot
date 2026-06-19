"""Main orchestration loop.

Polls market data on a schedule, runs detectors + confirmation + risk + news,
sends accepted signals to the active OrderRouter, and emits events to
subscribers (the WebSocket server).
"""
from __future__ import annotations

import asyncio
import itertools
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional

from marketdata.base import Bar, DataSource
from execution.base import OrderRequest, OrderRouter
from . import patterns
from . import correlation as corr
from .confirmation import CheckResult, ConfirmationConfig, confirm, failed_check, _atr as _atr_list
from .cooldown import CooldownConfig, LevelCooldown
from .correlation import CorrelationConfig
from .htf import htf_trend_conflict
from .indicators import compute_context, indicator_clarity_bonus, IndicatorContext
from .volume_profile import volume_clarity_bonus
from .session import is_forex_open, market_session
from .news import NewsFilter
from .patterns import Signal
from .journal import TradeJournal
from .risk import (
    Instrument,
    RiskConfig,
    RiskState,
    evaluate_signal,
    record_close,
    record_fill,
)

log = logging.getLogger(__name__)


EventCallback = Callable[[str, dict], Awaitable[None]]


@dataclass
class EngineConfig:
    instruments: List[Dict[str, Any]]      # from config.instruments
    timeframes: List[str]
    lookback_bars: int = 300
    poll_seconds: int = 60
    initial_history_bars: int = 200
    pattern_flags: Dict[str, bool] = field(default_factory=dict)


class StrategyEngine:
    def __init__(
        self,
        cfg: EngineConfig,
        data: DataSource,
        router: OrderRouter,
        risk_cfg: RiskConfig,
        risk_state: RiskState,
        confirm_cfg: ConfirmationConfig,
        news: NewsFilter,
        emit: Optional[EventCallback] = None,
        corr_cfg: Optional[CorrelationConfig] = None,
        mt5_store: Optional[Dict[str, Any]] = None,
        watch_levels: Optional[List[dict]] = None,
        notify_levels_changed=None,
        ltf_exit_cfg: Optional[Dict[str, Any]] = None,
        journal_dir: str = "state/trade_journal",
        cooldown_cfg: Optional[CooldownConfig] = None,
        htf_cfg: Optional[Dict[str, Any]] = None,
        min_clarity_score: float = 0.0,
        indicator_cfg: Optional[Dict[str, Any]] = None,
        volume_cfg: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.cfg = cfg
        self.data = data
        self.router = router
        self.risk_cfg = risk_cfg
        self.risk_state = risk_state
        self.confirm_cfg = confirm_cfg
        self.corr_cfg = corr_cfg or CorrelationConfig()
        self.news = news
        self.emit = emit or (lambda *_: _noop())
        # Live MT5 store (set by the server); used to read broker-exact tick
        # values for position sizing. Falls back to config pip_value if absent.
        self.mt5_store = mt5_store if mt5_store is not None else {}
        # Video/manual watch levels: pairs + price levels to act on (per strategy).
        self._watch_levels: List[dict] = watch_levels or []
        self._notify_levels_changed = notify_levels_changed

        # Lower-timeframe reversal exit rule + dedupe of acted-on signals.
        self._ltf_rule: Dict[str, Any] = ltf_exit_cfg or {}
        self._ltf_exit_seen: set[tuple] = set()

        # Per-trade journal (entry/exit times, pattern bars, in-between bars).
        self.journal = TradeJournal(journal_dir)

        # Shadow tracker (set by server when strategy.shadow_mode is enabled):
        # follows gate-rejected signals to their hypothetical TP/SL.
        self.shadow = None

        # Per-level cooldown (suppresses detector re-fires of the same level).
        self._cooldown = LevelCooldown(cooldown_cfg) if cooldown_cfg else None
        # Higher-timeframe context filter config ({} = disabled).
        self._htf_cfg: Dict[str, Any] = htf_cfg or {}
        # Clarity-score gate (0 = log-only).
        self._min_clarity = float(min_clarity_score or 0.0)

        # Indicator filter config ({} = disabled; dead_market_pct=0 disables gate).
        self._ind_cfg: Dict[str, Any] = indicator_cfg or {}

        # Volume-profile confirmation config ({} or enabled:false = disabled).
        # Non-blocking: contributes a clarity bonus only, so it can be
        # shadow-tested before it influences live acceptance.
        self._vp_cfg: Dict[str, Any] = volume_cfg or {}

        self._cache: Dict[tuple, List[Bar]] = {}
        self._signal_seen: set[tuple] = set()
        self._ticket_seq = itertools.count(start=int(datetime.now().timestamp()) % 1_000_000)
        self._stop = asyncio.Event()
        self._running = False
        # Pairs allowed by the dashboard Pairs Manager. Empty = all enabled.
        self._enabled_pairs: List[str] = []
        # Pattern flags overridden by dashboard. None = use config.yaml defaults.
        self._pattern_flags_override: Optional[Dict[str, bool]] = None

    # --------------------------------------------------------------------- #
    # Lifecycle
    # --------------------------------------------------------------------- #
    async def start(self) -> None:
        await self._warmup()
        self._running = True
        log.info("Engine started in %s mode", self.router.mode)
        await self.emit("engine:status", {"running": True, "mode": self.router.mode})

        try:
            while not self._stop.is_set():
                await self._tick()
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=self.cfg.poll_seconds)
                except asyncio.TimeoutError:
                    pass
        finally:
            self._running = False
            await self.emit("engine:status", {"running": False, "mode": self.router.mode})

    def stop(self) -> None:
        self._stop.set()

    def set_enabled_pairs(self, pairs: List[str]) -> None:
        """
        Called by the server whenever /api/pairs is updated from the dashboard.
        An empty list means 'all pairs enabled' (no filtering applied).
        """
        self._enabled_pairs = [p.upper() for p in pairs]
        if self._enabled_pairs:
            log.info("Engine: monitoring %d pairs: %s",
                     len(self._enabled_pairs), ", ".join(self._enabled_pairs))
        else:
            log.info("Engine: monitoring all pairs (no dashboard filter)")

    def set_enabled_patterns(self, pattern_keys: List[str]) -> None:
        """
        Called by the server when /api/patterns is updated from the dashboard.
        Builds a pattern_flags dict that overrides the config.yaml defaults.
        An empty list means 'use config.yaml defaults' (no dashboard override).
        """
        if not pattern_keys:
            self._pattern_flags_override = None
            log.info("Engine: pattern filter cleared — using config.yaml defaults")
            return
        # All known pattern keys — mark enabled ones True, rest False
        all_keys = [
            "double_top", "double_bottom", "head_shoulders", "inverse_hs",
            "triple_top", "triple_bottom", "rectangle", "trendline",
        ]
        self._pattern_flags_override = {k: (k in pattern_keys) for k in all_keys}
        active = [k for k in pattern_keys if k in all_keys]
        log.info("Engine: patterns set from dashboard — active: %s", ", ".join(active) or "none")

    def _active_pattern_flags(self) -> Dict[str, bool]:
        """Return the pattern flags to use this tick — dashboard override or config default."""
        if self._pattern_flags_override is not None:
            return self._pattern_flags_override
        return self.cfg.pattern_flags

    def set_watch_levels(self, levels: List[dict]) -> None:
        """Called by the server when the video/manual watch levels change."""
        self._watch_levels = levels or []
        active = [l for l in self._watch_levels if not l.get("consumed")]
        log.info("Engine: %d active watch level(s)", len(active))

    def _levels_for(self, display: str) -> List[dict]:
        s = display.upper()
        return [lv for lv in self._watch_levels
                if str(lv.get("symbol", "")).upper() == s and not lv.get("consumed")]

    def _watch_level_signals(self, inst: Dict[str, Any], tf: str, bars: List[Bar]) -> List[Signal]:
        """Produce candidate signals when the latest bar TESTS a watch level.
        Entry is at the level; the signal then goes through the SAME confirmation
        / correlation / risk gates as any other setup ('take the trade based on
        our strategy'). Side is taken from the level's bias, or inferred from the
        rejection direction. One trade per level (marked consumed on fill)."""
        levels = self._levels_for(inst["display"])
        if not levels or len(bars) < 20:
            return []
        bar = bars[-1]
        pip = inst.get("pip_size", 0.0001)
        atr = _atr_list(bars[-15:])
        out: List[Signal] = []
        for lv in levels:
            # only the timeframe the level was set for (default: any)
            lv_tf = lv.get("timeframe")
            if lv_tf and lv_tf != tf:
                continue
            try:
                L = float(lv["level"])
            except (KeyError, TypeError, ValueError):
                continue
            tol = float(lv.get("tol_pips", 15)) * pip
            if not (bar.low - tol <= L <= bar.high + tol):
                continue                       # price hasn't reached this level
            side = str(lv.get("side", "")).lower()
            if side not in ("buy", "sell"):
                side = "buy" if bar.close > L else ("sell" if bar.close < L else "")
                if not side:
                    continue
            buf = max(tol, 0.5 * atr) if atr > 0 else tol
            if buf <= 0:
                continue
            if side == "buy":
                entry, sl = L, L - buf
                tp = entry + self.risk_cfg.min_rr * (entry - sl)
            else:
                entry, sl = L, L + buf
                tp = entry - self.risk_cfg.min_rr * (sl - entry)
            if abs(entry - sl) <= 0:
                continue
            sig = Signal(
                symbol=inst["display"], timeframe=tf, setup="LEVEL", side=side,
                entry=round(entry, 5), sl=round(sl, 5), tp=round(tp, 5),
                pattern_level=round(L, 5), detected_at=bar.time, bars_in_pattern=1,
                notes=[f"Video watch level {L:g}" + (f" — {lv['note']}" if lv.get("note") else "")],
                rr=self.risk_cfg.min_rr,
            )
            setattr(sig, "_level_id", lv.get("id"))
            out.append(sig)
        return out

    def _is_pair_enabled(self, display: str) -> bool:
        """Return True if this instrument should be scanned this tick."""
        if not self._enabled_pairs:
            return True   # no filter set → scan everything
        sym = display.upper()
        return sym in self._enabled_pairs

    @property
    def running(self) -> bool:
        return self._running

    # --------------------------------------------------------------------- #
    # Tick
    # --------------------------------------------------------------------- #
    async def _warmup(self) -> None:
        log.info("Warming up history for %d instruments x %d timeframes",
                 len(self.cfg.instruments), len(self.cfg.timeframes))
        for inst in self.cfg.instruments:
            for tf in self.cfg.timeframes:
                try:
                    bars = self.data.fetch_history(
                        inst["symbol"], inst["display"], tf, self.cfg.initial_history_bars
                    )
                    self._cache[(inst["display"], tf)] = bars
                    log.info("  %s %s : %d bars", inst["display"], tf, len(bars))
                except Exception as exc:  # noqa: BLE001
                    log.warning("Warmup failed for %s %s: %s", inst["display"], tf, exc)

    async def _tick(self) -> None:
        # Refresh news once per tick (the filter has its own cache TTL)
        try:
            self.news.refresh()
        except Exception as exc:  # noqa: BLE001
            log.warning("News refresh failed: %s", exc)

        # Collect every confirmed candidate across all pairs/timeframes first,
        # so the correlation stage can compare and de-duplicate across pairs.
        candidates: List[tuple] = []
        for inst in self.cfg.instruments:
            if not self._is_pair_enabled(inst["display"]):
                continue   # pair toggled off in dashboard — skip
            for tf in self.cfg.timeframes:
                candidates.extend(await self._process(inst, tf))

        if candidates:
            await self._resolve_and_execute(candidates)

        # After caches are fresh, check open trades for a lower-timeframe reversal.
        await self._check_ltf_reversal_exits()

    # ------------------------------------------------------------------ #
    # Trade journalling
    # ------------------------------------------------------------------ #
    def _journal_closure(self, update) -> None:
        """Slice the bars from entry through this close and append to the journal."""
        bars = self._cache.get((update.symbol, update.timeframe), [])
        et, ct = update.entry_time or "", update.close_time or ""
        seg = [b.to_dict() for b in bars if (not et or b.time >= et) and (not ct or b.time <= ct)]
        still_open = any(p.ticket == update.ticket for p in self.router.open_positions())
        self.journal.record_close(update, seg, still_open)

    # ------------------------------------------------------------------ #
    # Lower-timeframe reversal exit
    # ------------------------------------------------------------------ #
    TF_ORDER = ["15m", "30m", "1h", "2h", "4h", "1d"]

    def _next_timeframe_up(self, tf: str) -> Optional[str]:
        """The next timeframe ABOVE `tf` that the engine actually scans
        (so its bar cache exists). 1d (or unknown) → None."""
        if tf not in self.TF_ORDER:
            return None
        for t in self.TF_ORDER[self.TF_ORDER.index(tf) + 1:]:
            if t in self.cfg.timeframes:
                return t
        return None

    def _lower_timeframes(self, tf: str, levels_down: int) -> List[str]:
        """The up-to-`levels_down` timeframes immediately BELOW `tf` that the
        engine is actually scanning (so their bar caches exist)."""
        if tf not in self.TF_ORDER:
            return []
        idx = self.TF_ORDER.index(tf)
        below = [t for t in self.TF_ORDER[:idx] if t in self.cfg.timeframes]
        return below[-levels_down:] if levels_down > 0 else below

    async def _check_ltf_reversal_exits(self) -> None:
        rule = self._ltf_rule
        if not rule or not rule.get("enabled"):
            return
        levels_down = int(rule.get("levels_down", 2))
        want_confirm = bool(rule.get("confirm", True))
        frac = float(rule.get("close_fraction", 0.5))
        move_be = bool(rule.get("move_sl_to_be", True))
        dt_db_flags = {"double_top": True, "double_bottom": True}

        for pos in self.router.open_positions():
            lowers = self._lower_timeframes(pos.timeframe, levels_down)
            if not lowers:
                continue
            # Opposing reversal: a long is threatened by a Double Top (sell);
            # a short is threatened by a Double Bottom (buy).
            want_side = "sell" if pos.side == "buy" else "buy"
            for ltf in lowers:
                bars = self._cache.get((pos.symbol, ltf), [])
                if len(bars) < 30:
                    continue
                opposing = [s for s in patterns.detect_double_top_bottom(bars)
                            if s.side == want_side and s.detected_at == bars[-1].time]
                if not opposing:
                    continue
                sig = opposing[0]
                dkey = (pos.ticket, ltf, sig.detected_at)
                if dkey in self._ltf_exit_seen:
                    continue
                if want_confirm:
                    ok, _checks = confirm(sig, bars, self.confirm_cfg)
                    if not ok:
                        continue
                self._ltf_exit_seen.add(dkey)
                price = bars[-1].close
                reason = f"ltf_rev_{ltf}_{sig.setup.lower()}"
                ups = self.router.reversal_exit(
                    pos.ticket, price, bars[-1], reason,
                    close_fraction=frac, move_sl_to_be=move_be)
                for u in ups:
                    record_close(self.risk_state, u.pnl)
                    self._journal_closure(u)
                    await self.emit("position:closed", u.to_dict())
                if ups:
                    log.info("LTF reversal exit: %s %s closed %.2f lots on %s %s",
                             pos.symbol, pos.timeframe, ups[0].lots, ltf, sig.setup)
                    await self.emit("signal:ltf_exit", {
                        "ticket": pos.ticket, "symbol": pos.symbol,
                        "trade_tf": pos.timeframe, "reversal_tf": ltf,
                        "pattern": sig.setup, "exit_price": price})
                break  # at most one reversal action per position per tick

    async def _process(self, inst: Dict[str, Any], tf: str) -> List[tuple]:
        """Update bars, run detection + confirmation, and return the list of
        confirmed (signal, inst) candidates for this pair/timeframe. The
        correlation gate + execution happen later, once per tick, in
        _resolve_and_execute()."""
        key = (inst["display"], tf)
        cached = self._cache.get(key, [])
        since_iso = cached[-1].time if cached else None

        try:
            new_bars = self.data.fetch_latest(inst["symbol"], inst["display"], tf, since_iso)
        except Exception as exc:  # noqa: BLE001
            log.warning("Fetch failed for %s %s: %s", inst["display"], tf, exc)
            return []

        if not new_bars:
            return []

        # Dynamic spread: if the EA didn't stamp a per-bar spread, sample the
        # broker's CURRENT spread from the live feed so exits are costed at the
        # spread prevailing now (captures news / session-rollover widening).
        _, _, _live_spread = self._live_tick_meta(inst["display"])

        for bar in new_bars:
            if not getattr(bar, "spread", 0.0) and _live_spread > 0:
                bar.spread = _live_spread
            cached.append(bar)
            # Advance shadow-tracked (rejected) signals on this bar.
            if self.shadow is not None:
                try:
                    self.shadow.on_bar(bar)
                except Exception as exc:  # noqa: BLE001
                    log.debug("Shadow on_bar failed: %s", exc)
            # Mark open positions on this symbol
            closures = self.router.on_bar(inst["display"], bar)
            for c in closures:
                record_close(self.risk_state, c.pnl)
                self._journal_closure(c)
                await self.emit("position:closed", c.to_dict())

        # Cap memory
        if len(cached) > self.cfg.lookback_bars:
            cached = cached[-self.cfg.lookback_bars:]
        self._cache[key] = cached

        await self.emit("bars:new", {
            "symbol": inst["display"],
            "timeframe": tf,
            "bars": [b.to_dict() for b in new_bars],
        })

        # Detect on the freshly-updated cache (respect dashboard pattern toggles)
        confirmed: List[tuple] = []
        signals = patterns.run_all(cached, self._active_pattern_flags())
        # Plus any video/manual watch levels the latest bar just tested.
        signals = signals + self._watch_level_signals(inst, tf, cached)
        for sig in signals:
            if await self._confirm_candidate(sig, inst):
                confirmed.append((sig, inst))
        return confirmed

    async def _reject(self, sig: Signal, stage: str,
                      failed: Optional[str] = None,
                      checks: Optional[List[CheckResult]] = None) -> None:
        """Single funnel for every signal rejection: emits the structured
        event (stage + failed_check + per-check results) and registers the
        signal with the shadow tracker so its hypothetical outcome is known."""
        payload: Dict[str, Any] = {"signal": sig.to_dict(), "stage": stage}
        if failed:
            payload["failed_check"] = failed
        if checks:
            payload["checks"] = [c.to_dict() for c in checks]
        if self.shadow is not None:
            try:
                self.shadow.register(sig, stage, failed)
            except Exception as exc:  # noqa: BLE001
                log.debug("Shadow register failed: %s", exc)
        await self.emit("signal:rejected", payload)

    async def _confirm_candidate(self, sig: Signal, inst: Dict[str, Any]) -> bool:
        """De-dup + confirmation. Returns True if the signal advances to the
        correlation/news/risk stages this tick."""
        key = (sig.symbol, sig.timeframe, sig.setup, sig.detected_at)
        if key in self._signal_seen:
            return False
        self._signal_seen.add(key)

        bars = self._cache.get((sig.symbol, sig.timeframe), [])

        # Per-level cooldown: drop re-fires of a level we already signalled on.
        # (Not shadow-registered — the first signal at the level already was.)
        if self._cooldown is not None and len(bars) >= 15:
            atr = _atr_list(bars[-15:])
            level = sig.pattern_level or sig.entry
            prior = self._cooldown.check(sig.symbol, sig.timeframe, sig.side,
                                         level, atr, [b.time for b in bars])
            if prior:
                await self.emit("signal:deduped", {
                    "signal": sig.to_dict(), "stage": "cooldown",
                    "prior": prior,
                })
                return False

        # Higher-timeframe context: don't buy a 1h double bottom inside a 2h
        # downtrend (and mirrored for sells). Own stage so its cost is measurable.
        if self._htf_cfg.get("enabled"):
            htf = self._next_timeframe_up(sig.timeframe)
            if htf:
                htf_bars = self._cache.get((sig.symbol, htf), [])
                conflict, detail, vals = htf_trend_conflict(
                    htf_bars, sig.side,
                    ema_period=int(self._htf_cfg.get("ema_period", 50)),
                    swings=int(self._htf_cfg.get("swings", 3)),
                )
                if conflict:
                    sig.notes.append(f"✗ {detail} ({htf})")
                    await self._reject(sig, "htf_context", failed="htf_trend", checks=[
                        CheckResult("htf_trend", False,
                                    value=vals.get("close"), threshold=vals.get("ema"),
                                    detail=f"{detail} ({htf})")])
                    return False
                sig.notes.append(f"✓ {detail} ({htf})")

        # ---------- Indicator suite ------------------------------------------ #
        # Compute RSI / EMA200 / volume / ATR-percentile from the bar cache.
        # Two effects:
        #   1. Dead-market gate — ATR percentile rank < dead_market_pct rejects
        #      the signal (patterns in frozen/hibernating markets are noise).
        #   2. Clarity bonus — non-binary confluence adds up to +35 points to the
        #      structural clarity score, helping borderline patterns cross the gate.
        # Both effects are logged; neither fires if the bar history is too short.
        if self._ind_cfg:
            ctx = compute_context(bars)
            ind_summary = ctx.summary()
            # Dead-market gate
            dead_pct = float(self._ind_cfg.get("dead_market_pct", 0.15))
            if (dead_pct > 0 and ctx.atr_pct_rank is not None
                    and ctx.atr_pct_rank < dead_pct):
                sig.notes.append(
                    f"✗ Dead market (ATR pct {ctx.atr_pct_rank:.0%} < {dead_pct:.0%})"
                )
                await self._reject(sig, "dead_market", failed="atr_percentile", checks=[
                    CheckResult(
                        "atr_percentile", False,
                        value=round(ctx.atr_pct_rank, 3),
                        threshold=dead_pct,
                        detail=f"ATR pct {ctx.atr_pct_rank:.0%} < {dead_pct:.0%} — frozen market",
                    )
                ])
                return False
            # Indicator clarity bonus (non-blocking)
            bonus, reasons = indicator_clarity_bonus(ctx, sig.side)
            if bonus > 0:
                old_score = sig.clarity_score
                sig.clarity_score = round(min(100.0, sig.clarity_score + bonus), 1)
                sig.notes.append(
                    f"Indicator bonus +{bonus:.0f} ({'; '.join(reasons)}) "
                    f"→ clarity {old_score:.0f}→{sig.clarity_score:.0f}"
                )
            else:
                sig.notes.append(f"Indicators: {ind_summary}")

        # ---------- Volume-profile confirmation (non-blocking) -------------- #
        # Volume-at-price (POC / value area / HVN) + a tick-volume delta proxy.
        # Adds a clarity bonus when the R2 retest sits at a high-volume node and
        # recent participation agrees with the trade direction. Logged on the
        # signal (and therefore in shadow + journal data) so its real effect on
        # win rate can be measured before it is ever made a hard gate.
        if self._vp_cfg and self._vp_cfg.get("enabled", True):
            try:
                vbonus, vreasons, vread = volume_clarity_bonus(
                    sig.entry, sig.side, bars, self._vp_cfg)
            except Exception:  # noqa: BLE001 — confirmation must never crash the loop
                vbonus, vreasons, vread = 0.0, [], {}
            if vbonus > 0:
                old_score = sig.clarity_score
                sig.clarity_score = round(min(100.0, sig.clarity_score + vbonus), 1)
                sig.notes.append(
                    f"Volume bonus +{vbonus:.0f} ({'; '.join(vreasons)}) "
                    f"→ clarity {old_score:.0f}→{sig.clarity_score:.0f}")
            elif vread:
                prof = vread.get("profile", {})
                poc = prof.get("poc")
                poc_s = f"{poc:.5f}" if isinstance(poc, (int, float)) else "n/a"
                sig.notes.append(
                    f"Volume: no confluence (POC {poc_s}, "
                    f"delta {vread.get('delta_norm', 0.0):+.2f})")

        # Clarity gate (strategy.min_clarity_score; 0 = log-only). The score is
        # carried on the signal either way, so accepted AND rejected events log it.
        if self._min_clarity > 0 and 0 < sig.clarity_score < self._min_clarity:
            await self._reject(sig, "clarity", failed="clarity_score", checks=[
                CheckResult("clarity_score", False, value=sig.clarity_score,
                            threshold=self._min_clarity,
                            detail=f"Clarity {sig.clarity_score:.0f} < {self._min_clarity:.0f}")])
            return False

        ok_conf, checks = confirm(sig, bars, self.confirm_cfg)
        sig.notes.extend(c.note for c in checks)
        if not ok_conf:
            await self._reject(sig, "confirmation", failed=failed_check(checks), checks=checks)
            return False
        return True

    # --------------------------------------------------------------------- #
    # Correlation gate (choppiness + direction + de-duplication) → execution
    # --------------------------------------------------------------------- #
    async def _resolve_and_execute(self, candidates: List[tuple]) -> None:
        ccfg = self.corr_cfg
        if not ccfg.enabled:
            for sig, inst in candidates:
                await self._execute_signal(sig, inst)
            return

        # Stage 1 — skip choppy markets (Choppiness Index).
        stage1: List[tuple] = []
        for sig, inst in candidates:
            bars = self._cache.get((sig.symbol, sig.timeframe), [])
            ci = corr.choppiness_index(bars, ccfg.ci_period)
            if ci is not None:
                sig.notes.append(f"Choppiness Index {ci:.0f}")
            if ci is not None and ccfg.ci_choppy_threshold > 0 and ci > ccfg.ci_choppy_threshold:
                sig.notes.append(f"✗ Too choppy (CI {ci:.0f} > {ccfg.ci_choppy_threshold:.0f})")
                await self._reject(sig, "choppiness", failed="choppiness_index", checks=[
                    CheckResult("choppiness_index", False, value=round(ci, 1),
                                threshold=ccfg.ci_choppy_threshold,
                                detail=f"Too choppy (CI {ci:.0f} > {ccfg.ci_choppy_threshold:.0f})")])
                continue
            stage1.append((sig, inst, ci))

        # Stage 2 — require correlated peers to confirm the direction.
        stage2: List[tuple] = []
        for sig, inst, ci in stage1:
            bars = self._cache.get((sig.symbol, sig.timeframe), [])
            conflict = None
            for peer in self.cfg.instruments:
                psym = peer["display"]
                if psym == sig.symbol or not self._is_pair_enabled(psym):
                    continue
                pbars = self._cache.get((psym, sig.timeframe), [])
                r = corr.correlation(bars, pbars, ccfg.lookback_bars)
                if r is None or abs(r) < ccfg.strong_threshold:
                    continue
                pdir = corr.net_direction(pbars, ccfg.direction_lookback)
                if pdir == 0:
                    continue
                if pdir != corr.expected_peer_dir(sig.side, r):
                    conflict = (psym, r, pdir)
                    break
            if conflict:
                psym, r, pdir = conflict
                move = "up" if pdir > 0 else "down"
                if ccfg.block_on_conflict:
                    sig.notes.append(f"✗ Correlation conflict: {psym} (r={r:+.2f}) moving {move}")
                    await self._reject(sig, "correlation", failed="correlation_conflict", checks=[
                        CheckResult("correlation_conflict", False, value=round(r, 2),
                                    threshold=ccfg.strong_threshold,
                                    detail=f"Conflict: {psym} (r={r:+.2f}) moving {move}")])
                    continue
                sig.notes.append(f"⚠ Correlation conflict (allowed): {psym} (r={r:+.2f}) moving {move}")
            stage2.append((sig, inst, ci))

        # Stage 3a — skip if a correlated position is already open (same bet).
        try:
            open_pos = self.router.open_positions()
        except Exception:  # noqa: BLE001
            open_pos = []
        stage3: List[tuple] = []
        for sig, inst, ci in stage2:
            bars = self._cache.get((sig.symbol, sig.timeframe), [])
            blocked_by = None
            for p in open_pos:
                if p.symbol == sig.symbol:
                    blocked_by = (p.symbol, None)
                    break
                pbars = self._cache.get((p.symbol, sig.timeframe), [])
                r = corr.correlation(bars, pbars, ccfg.lookback_bars)
                if r is None or abs(r) < ccfg.strong_threshold:
                    continue
                if corr.same_directional_bet(sig.side, p.side, r):
                    blocked_by = (p.symbol, r)
                    break
            if blocked_by and ccfg.dedupe_correlated:
                psym, r = blocked_by
                tag = f" (r={r:+.2f})" if r is not None else " (same symbol)"
                sig.notes.append(f"✗ Correlated position already open: {psym}{tag}")
                await self._reject(sig, "correlation_open", failed="correlated_position_open")
                continue
            stage3.append((sig, inst, ci))

        # Stage 3b — among co-firing correlated signals, keep only the cleanest.
        final = stage3
        if ccfg.dedupe_correlated and len(stage3) > 1:
            discarded: set = set()
            for i in range(len(stage3)):
                if i in discarded:
                    continue
                sig_i, _, _ = stage3[i]
                bars_i = self._cache.get((sig_i.symbol, sig_i.timeframe), [])
                group = [i]
                for j in range(i + 1, len(stage3)):
                    if j in discarded:
                        continue
                    sig_j, _, _ = stage3[j]
                    if sig_j.timeframe != sig_i.timeframe:
                        continue
                    bars_j = self._cache.get((sig_j.symbol, sig_j.timeframe), [])
                    r = corr.correlation(bars_i, bars_j, ccfg.lookback_bars)
                    if r is None or abs(r) < ccfg.strong_threshold:
                        continue
                    if corr.same_directional_bet(sig_i.side, sig_j.side, r):
                        group.append(j)
                if len(group) > 1:
                    best = min(group, key=lambda k: corr.quality_key(stage3[k][2], stage3[k][0].rr))
                    kept = stage3[best][0]
                    for k in group:
                        if k != best:
                            discarded.add(k)
                            skipped = stage3[k][0]
                            skipped.notes.append(
                                f"✗ Correlated duplicate — cleaner setup chosen on {kept.symbol}"
                            )
                            await self._reject(skipped, "correlation_dupe",
                                               failed="correlated_duplicate")
            final = [stage3[i] for i in range(len(stage3)) if i not in discarded]

        for sig, inst, _ci in final:
            await self._execute_signal(sig, inst)

    def _live_tick_meta(self, display: str):
        """Return (tick_value, tick_size, spread) for a symbol from the live
        MT5 prices feed, or (None, None, 0.0) if not available. Spread is the
        broker's CURRENT ask−bid in price units (sanity-capped). Matched
        case-insensitively with broker suffixes stripped ('EURUSD.r'→'EURUSD')."""
        prices = (self.mt5_store or {}).get("prices", []) or []
        want = display.upper().split(".")[0].split("_")[0]
        for p in prices:
            sym = str(p.get("symbol", "")).upper().split(".")[0].split("_")[0]
            if sym == want:
                tv = p.get("tick_value")
                ts = p.get("tick_size")
                try:
                    tv = float(tv) if tv is not None else None
                    ts = float(ts) if ts is not None else None
                except (TypeError, ValueError):
                    tv = ts = None
                spread = 0.0
                try:
                    bid = float(p.get("bid") or 0.0)
                    ask = float(p.get("ask") or 0.0)
                    if ask > bid > 0 and (ask - bid) < bid * 0.05:  # ignore junk quotes
                        spread = ask - bid
                except (TypeError, ValueError):
                    spread = 0.0
                return tv, ts, spread
        return None, None, 0.0

    async def _execute_signal(self, sig: Signal, inst: Dict[str, Any]) -> None:
        """Session gate → news check → risk check + sizing → submit order."""
        # Session gate: reject signals outside Forex market hours.
        # Prevents fills on weekend synthetic prices or stale bars pushed by
        # MT5 after a reconnect. The bar's detected_at timestamp is also
        # validated so a Friday-evening bar replayed on Saturday is blocked.
        session = market_session()
        if not is_forex_open(bar_time=sig.detected_at):
            sig.notes.append(f"✗ Market closed ({session})")
            await self._reject(sig, "session", failed="market_hours", checks=[
                CheckResult(
                    "market_hours", False,
                    detail=f"Forex closed — no new entries outside trading hours "
                           f"(bar={sig.detected_at}, session={session})",
                )
            ])
            return
        sig.notes.append(f"Session: {session}")

        # News check
        try:
            blocked, why = self.news.is_blocked(
                datetime.now(timezone.utc),
                currencies=_currencies_for(sig.symbol),
            )
        except Exception:  # noqa: BLE001
            blocked, why = False, "News check error (allowing)"
        if blocked:
            sig.notes.append("News block: " + why)
            await self._reject(sig, "news", failed="news_window", checks=[
                CheckResult("news_window", False, detail=why)])
            return

        # Risk check + sizing. Prefer live broker-exact tick value from MT5.
        tick_value, tick_size, live_spread = self._live_tick_meta(inst["display"])
        instrument = Instrument(
            symbol=inst["display"],
            pip_size=inst["pip_size"],
            pip_value=inst["pip_value"],
            tick_value=tick_value,
            tick_size=tick_size,
        )
        sizing: Dict[str, Any] = {}
        accept, why_risk, lots = evaluate_signal(sig, instrument, self.risk_cfg,
                                                 self.risk_state, sizing=sizing,
                                                 entry_spread=live_spread)
        sig.notes.append(why_risk)
        if not accept:
            await self._reject(sig, "risk", failed="risk", checks=[
                CheckResult("risk", False, detail=why_risk)])
            return

        # Build OrderRequest and submit
        ticket = next(self._ticket_seq)
        # Entry-bar reference for the router's look-ahead guard: the latest
        # CLOSED bar in cache at decision time. The position may only be managed
        # by bars strictly after this, so it can never be filled by its own
        # entry bar or by replayed/backfilled history.
        _entry_cache = self._cache.get((sig.symbol, sig.timeframe), [])
        _entry_bar_time = _entry_cache[-1].time if _entry_cache else sig.detected_at
        req = OrderRequest(
            ticket=ticket,
            symbol=sig.symbol,
            side=sig.side,
            lots=lots,
            entry=sig.entry,
            sl=sig.sl,
            tp=sig.tp,
            setup=sig.setup,
            timeframe=sig.timeframe,
            detected_at=sig.detected_at,
            entry_bar_time=_entry_bar_time,
            tick_value=tick_value or 0.0,   # same basis used for sizing → consistent P&L
            tick_size=tick_size or 0.0,
            risked_money=float(sizing.get("risked_money", 0.0)),
            sizing_basis=f"{sizing.get('basis', '')} [{sizing.get('source', '')}]",
            spread=live_spread,   # broker's real ask−bid at signal time
        )
        await self.emit("signal:accepted", {"signal": sig.to_dict(), "lots": lots})

        fill = self.router.submit(req)
        if fill is None:
            await self.emit("order:rejected", {"signal": sig.to_dict()})
            return

        record_fill(self.risk_state)
        # Journal the entry + the bars that formed the pattern.
        cached = self._cache.get((sig.symbol, sig.timeframe), [])
        window = max(int(getattr(sig, "bars_in_pattern", 0)) + 10, 20)
        pattern_bars = [b.to_dict() for b in cached[-window:]]
        self.journal.open_trade(fill, sig, pattern_bars)
        await self.emit("order:filled", fill.to_dict())

        # If this trade came from a video watch level, consume it (one trade per
        # level) and persist so it isn't re-triggered.
        level_id = getattr(sig, "_level_id", None)
        if level_id is not None:
            changed = False
            for lv in self._watch_levels:
                if lv.get("id") == level_id and not lv.get("consumed"):
                    lv["consumed"] = True
                    changed = True
            if changed and self._notify_levels_changed:
                try:
                    self._notify_levels_changed(self._watch_levels)
                except Exception:  # noqa: BLE001
                    pass


def _currencies_for(display_symbol: str) -> List[str]:
    """Return the currencies whose news affects this instrument."""
    s = display_symbol.upper()
    if s in {"XAUUSD", "USOIL"}:
        return ["USD"]
    if len(s) == 6:
        return [s[:3], s[3:]]
    return []


async def _noop(*_: Any, **__: Any) -> None:
    return None
