"""FastAPI app + WebSocket hub.

Endpoints:
  GET  /api/health                — basic ping
  GET  /api/status                — engine running?, mode, equity, caps
  GET  /api/signals               — last 100 signals (any stage)
  GET  /api/positions             — open positions
  GET  /api/closed                — closed trades
  GET  /api/news                  — upcoming high-impact events
  POST /api/mode                  — switch paper <-> live (live = stubbed)
  POST /api/halt                  — touch the kill-switch file
  POST /api/resume                — remove the kill-switch file
  WS   /ws                        — live stream of all engine events

If `server.serve_dashboard` is true in config, the dashboard at
../trading-dashboard/ is mounted at /, so `open http://localhost:8765/` shows
the full UI with the bot already connected.
"""
from __future__ import annotations

import asyncio
import collections
import json
import logging
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

log = logging.getLogger(__name__)


class WSHub:
    def __init__(self, history: int = 500) -> None:
        self._clients: List[WebSocket] = []
        self._history: Deque[dict] = collections.deque(maxlen=history)

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self._clients.append(ws)
        # Send recent backlog so the dashboard can render immediately
        for evt in list(self._history):
            try:
                await ws.send_json(evt)
            except Exception:  # noqa: BLE001
                break

    def disconnect(self, ws: WebSocket) -> None:
        try:
            self._clients.remove(ws)
        except ValueError:
            pass

    async def emit(self, event: str, payload: dict) -> None:
        msg = {"event": event, "payload": payload, "ts": _now_iso()}
        self._history.append(msg)
        dead: List[WebSocket] = []
        for ws in self._clients:
            try:
                await ws.send_json(msg)
            except Exception:  # noqa: BLE001
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)


class ModeChange(BaseModel):
    mode: str   # "paper" | "live"


def build_app(
    config: Dict[str, Any],
    state_provider,                    # callable -> dict snapshot
    set_mode,                          # callable(str) -> tuple(bool, str)
    halt,                              # callable() -> None
    resume,                            # callable() -> None
    reset,                             # callable() -> None  ← NEW
    hub: WSHub,
    signals_log: List[dict],
    mt5_store: Dict[str, Any],         # shared dict updated by /api/mt5_feed
    command_queue: List[dict],          # shared list for news-agent SL commands
    enabled_pairs: List[str],           # pairs active in dashboard Pairs Manager
    enabled_patterns: List[str],        # patterns active in dashboard Patterns Manager
    notify_pairs_changed=None,          # optional callable(List[str]) → engine.set_enabled_pairs
    notify_patterns_changed=None,       # optional callable(List[str]) → engine.set_enabled_patterns
    agent_trades_provider=None,         # callable -> {open, closed, equity} (paper ledger, agent-only)
    persist_pairs=None,                 # optional callable(List[str]) → save enabled pairs to disk
    persist_patterns=None,              # optional callable(List[str]) → save enabled patterns to disk
    watch_levels=None,                  # shared list of video/manual watch levels
    notify_levels_changed=None,         # optional callable(List[dict]) → engine.set_watch_levels
    persist_levels=None,                # optional callable(List[dict]) → save watch levels to disk
    apply_paper_sl=None,                # optional callable(cmd dict) -> bool: apply SL cmd to a PAPER position
    dashboard_dir: Optional[Path] = None,
) -> FastAPI:
    app = FastAPI(title="Price-Action Trading Bot", version="1.0.0")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],            # local-only by default
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ---------- REST ---------------------------------------------------- #
    @app.get("/api/health")
    def health() -> dict:
        return {"ok": True, "time": _now_iso()}

    @app.get("/api/status")
    def status() -> dict:
        return state_provider()

    @app.get("/api/signals")
    def signals(limit: int = 100) -> dict:
        return {"signals": signals_log[-limit:]}

    @app.get("/api/positions")
    def positions() -> dict:
        snap = state_provider()
        return {"open": snap.get("open_positions", [])}

    @app.get("/api/closed")
    def closed(limit: int = 200) -> dict:
        snap = state_provider()
        return {"closed": (snap.get("closed_trades", []) or [])[-limit:]}

    @app.get("/api/agent/trades")
    def agent_trades() -> dict:
        """The AGENT's own trades only (the paper bot's ledger), separate from
        the broker account snapshot. Open trades carry live current price +
        unrealized P&L. This is what the dashboard shows so only the bot's own
        trades appear — not any manual/account history."""
        if agent_trades_provider is None:
            return {"open": [], "closed": [], "equity": None, "starting_equity": None}
        return agent_trades_provider()

    @app.get("/api/news")
    def news() -> dict:
        snap = state_provider()
        return {"upcoming": snap.get("upcoming_news", [])}

    @app.post("/api/mt5_bars")
    async def mt5_bars(request: Request) -> dict:
        """Receive OHLCV bar history pushed by the MT5DataBridge EA."""
        try:
            data = await request.json()
        except Exception:
            raise HTTPException(400, "Invalid JSON payload")
        if "bars" in data:
            mt5_store["bars"] = data["bars"]
        if "tz_offset_sec" in data:
            mt5_store["tz_offset_sec"] = data["tz_offset_sec"]
        mt5_store["_bars_received_at"] = _now_iso()
        sym_count = len(data.get("bars", {}))
        log.info("mt5_bars: received bar data for %d symbol(s)", sym_count)
        return {"ok": True, "symbols": sym_count}

    @app.get("/api/mt5_bars")
    def mt5_bars_status() -> dict:
        """Summary of the bar data currently held in the MT5 store."""
        bars_root = mt5_store.get("bars", {})
        summary = {
            sym: {tf: len(bars) for tf, bars in tfs.items()}
            for sym, tfs in bars_root.items()
        }
        return {
            "received_at": mt5_store.get("_bars_received_at"),
            "tz_offset_sec": mt5_store.get("tz_offset_sec", 0),
            "symbols": summary,
        }

    @app.post("/api/mt5_feed")
    async def mt5_feed(request: Request) -> dict:
        """Receive live MT5 data from the MQL5 EA bridge and broadcast to the dashboard.

        NOTE: bar data arrives on a SEPARATE endpoint (/api/mt5_bars) on its own
        slower cadence. We must NOT wipe it here, or the 5s price push would
        destroy the 60s bar push (breaking charts and pattern detection). So we
        preserve the bar-related keys across this update.
        """
        try:
            data = await request.json()
        except Exception:
            raise HTTPException(400, "Invalid JSON payload")
        preserved = {k: mt5_store[k] for k in ("bars", "tz_offset_sec", "_bars_received_at")
                     if k in mt5_store}
        mt5_store.clear()
        mt5_store.update(data)
        mt5_store.update(preserved)          # keep bars pushed by /api/mt5_bars
        mt5_store["_received_at"] = _now_iso()
        await hub.emit("mt5:prices", {"prices": data.get("prices", []), "ts": mt5_store["_received_at"]})
        await hub.emit("engine:status", {"source": "mt5_feed", **data})
        return {"ok": True}

    @app.get("/api/mt5/prices")
    def mt5_prices() -> dict:
        """Return the latest live prices pushed by the MT5 EA."""
        return {
            "prices": mt5_store.get("prices", []),
            "ts": mt5_store.get("_received_at"),
        }

    @app.get("/api/chart/{symbol}")
    def get_chart(symbol: str, tf: str = "1h") -> dict:
        """
        Return OHLCV bars for the dashboard pair chart panel — MT5 ONLY.

        Bars come exclusively from the live MT5 feed pushed by the
        MT5DataBridge EA to /api/mt5_bars (held in mt5_store["bars"]),
        timezone-corrected to UTC. There is NO external/yfinance fallback:
        if the EA hasn't pushed bars for this symbol/timeframe yet, an empty
        list is returned and the dashboard shows a "waiting for MT5" state.

        tf: 15m | 30m | 1h | 2h | 4h | 1d   (whatever the EA pushes)
        """
        # Dashboard tf -> mt5_store key (case-insensitive on the day key).
        tf_key = {
            "15m": "15m", "30m": "30m",
            "1h": "1h",   "2h": "2h",   "4h": "4h",
            "1d": "1d",   "1D": "1d",
        }.get(tf)

        bars_root = mt5_store.get("bars", {}) or {}
        out = []
        if tf_key and bars_root:
            tz_off = int(mt5_store.get("tz_offset_sec", 0) or 0)
            sym_data = None
            for key in (symbol, symbol.upper(), symbol.lower()):
                if key in bars_root:
                    sym_data = bars_root[key]
                    break
            if sym_data:
                for b in (sym_data.get(tf_key) or []):
                    try:
                        # MT5 bar time is server-local epoch; convert to UTC ISO
                        utc_ts = int(b["t"]) - tz_off
                        iso = (
                            datetime.fromtimestamp(utc_ts, tz=timezone.utc)
                            .isoformat()
                            .replace("+00:00", "Z")
                        )
                        out.append({
                            "t": iso,
                            "o": round(float(b["o"]), 5),
                            "h": round(float(b["h"]), 5),
                            "l": round(float(b["l"]), 5),
                            "c": round(float(b["c"]), 5),
                            "v": int(b.get("v", 0) or 0),
                        })
                    except (KeyError, TypeError, ValueError, OverflowError, OSError):
                        continue   # skip any malformed bar

        resp = {
            "symbol": symbol,
            "tf": tf,
            "bars": out[-200:],
            "source": "mt5",
            "received_at": mt5_store.get("_bars_received_at"),
        }
        if not out:
            resp["note"] = "no MT5 bars yet for this symbol/timeframe"
            log.debug("Chart: no MT5 bars for %s %s", symbol, tf)
        else:
            log.debug("Chart: served %d MT5 bars for %s %s", len(out), symbol, tf)
        return resp

    # ---------- News-agent command queue --------------------------------- #

    @app.post("/api/commands")
    async def post_command(request: Request) -> dict:
        """News agent posts an SL management command here."""
        try:
            cmd = await request.json()
        except Exception:
            raise HTTPException(400, "Invalid JSON payload")
        if "id" not in cmd:
            cmd["id"] = str(uuid.uuid4())
        cmd.setdefault("status", "pending")

        # PAPER positions first: if the ticket belongs to the paper router,
        # apply the SL change directly — the EA queue below only reaches real
        # MT5 positions, which is why news-agent SL moves never affected the
        # paper bot before (fixed 11 Jun).
        if apply_paper_sl is not None:
            try:
                if apply_paper_sl(cmd):
                    cmd["status"] = "done_paper"
                    log.info("Command applied to PAPER position: %s type=%s ticket=%s new_sl=%s",
                             cmd["id"], cmd.get("type"), cmd.get("ticket"), cmd.get("new_sl"))
                    await hub.emit("news_agent:command", cmd)
                    return {"ok": True, "id": cmd["id"], "applied": "paper"}
            except Exception as exc:  # noqa: BLE001
                log.warning("Paper SL apply failed: %s", exc)

        command_queue.append(cmd)
        # Keep queue bounded
        if len(command_queue) > 500:
            command_queue[:] = command_queue[-500:]
        log.info("Command queued: %s type=%s ticket=%s",
                 cmd["id"], cmd.get("type"), cmd.get("ticket"))
        await hub.emit("news_agent:command", cmd)
        return {"ok": True, "id": cmd["id"]}

    @app.get("/api/commands/next")
    def get_next_command() -> dict:
        """
        EA polls this endpoint to receive the next pending SL command.
        Returns one command at a time as a flat object (simplifies MQL5 parsing).
        Expired commands are silently purged.
        """
        now_iso = _now_iso()
        for cmd in command_queue:
            if cmd.get("status") != "pending":
                continue
            expires = cmd.get("expires_at", "9999")
            if expires < now_iso:
                cmd["status"] = "expired"
                continue
            # Return first pending non-expired command
            return {"command": cmd}
        return {"command": None}

    @app.post("/api/commands/ack/{cmd_id}")
    async def ack_command(cmd_id: str) -> dict:
        """EA calls this after successfully executing a command."""
        for cmd in command_queue:
            if cmd.get("id") == cmd_id:
                cmd["status"] = "acknowledged"
                cmd["ack_at"] = _now_iso()
                log.info("Command acknowledged: %s", cmd_id)
                await hub.emit("news_agent:ack", {"id": cmd_id, "ack_at": cmd["ack_at"]})
                return {"ok": True}
        raise HTTPException(404, f"Command '{cmd_id}' not found")

    @app.get("/api/commands")
    def get_all_commands(limit: int = 100) -> dict:
        """Return recent commands and their statuses (for dashboard view)."""
        return {"commands": command_queue[-limit:]}

    @app.get("/api/news_agent/decisions")
    def news_agent_decisions(limit: int = 50) -> dict:
        """Return the latest news-agent decision log entries (from JSONL file)."""
        log_file = Path("state/news_decisions.jsonl")
        if not log_file.exists():
            return {"decisions": []}
        lines = log_file.read_text(encoding="utf-8").strip().split("\n")
        entries: List[dict] = []
        for line in lines[-limit:]:
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except Exception:
                pass
        return {"decisions": entries}

    # ---------- Enabled pairs (dashboard toggle → bot) -------------------- #

    @app.get("/api/pairs")
    def get_pairs() -> dict:
        """
        Returns the list of pairs currently enabled in the dashboard Pairs Manager.
        The news agent and strategy engine use this to know what to monitor.
        An empty list means no pairs have been synced yet (treat all as enabled).
        """
        return {"enabled_pairs": list(enabled_pairs), "count": len(enabled_pairs)}

    @app.post("/api/pairs")
    async def set_pairs(request: Request) -> dict:
        """
        Dashboard POSTs the full enabled pairs list here whenever a toggle changes.
        Body: {"enabled_pairs": ["EURUSD", "GBPUSD", ...]}
        """
        try:
            body = await request.json()
        except Exception:
            raise HTTPException(400, "Invalid JSON payload")
        pairs = body.get("enabled_pairs", [])
        if not isinstance(pairs, list):
            raise HTTPException(400, "enabled_pairs must be a list")
        enabled_pairs.clear()
        enabled_pairs.extend(str(p).upper() for p in pairs)
        log.info("Enabled pairs updated: %d pairs", len(enabled_pairs))
        # Persist so the selection survives a server restart (the EA then keeps
        # watching them without the dashboard needing to be open).
        if persist_pairs:
            persist_pairs(list(enabled_pairs))
        # Notify the strategy engine so it immediately respects the new filter
        if notify_pairs_changed:
            notify_pairs_changed(list(enabled_pairs))
        await hub.emit("pairs:updated", {"enabled_pairs": list(enabled_pairs)})
        return {"ok": True, "count": len(enabled_pairs)}

    # ---------- Enabled patterns (dashboard toggle → bot) ----------------- #

    @app.get("/api/patterns")
    def get_patterns() -> dict:
        """
        Returns the list of patterns currently enabled in the dashboard.
        The strategy engine uses this to decide which detectors to run.
        An empty list means not yet synced — engine falls back to config.yaml defaults.
        """
        return {"enabled_patterns": list(enabled_patterns), "count": len(enabled_patterns)}

    @app.post("/api/patterns")
    async def set_patterns(request: Request) -> dict:
        """
        Dashboard POSTs the full enabled patterns list here on every toggle change.
        Body: {"enabled_patterns": ["double_top", "double_bottom", ...]}
        """
        try:
            body = await request.json()
        except Exception:
            raise HTTPException(400, "Invalid JSON payload")
        patterns = body.get("enabled_patterns", [])
        if not isinstance(patterns, list):
            raise HTTPException(400, "enabled_patterns must be a list")
        enabled_patterns.clear()
        enabled_patterns.extend(str(p).lower() for p in patterns)
        log.info("Enabled patterns updated: %s", ", ".join(enabled_patterns) or "none")
        if persist_patterns:
            persist_patterns(list(enabled_patterns))
        if notify_patterns_changed:
            notify_patterns_changed(list(enabled_patterns))
        await hub.emit("patterns:updated", {"enabled_patterns": list(enabled_patterns)})
        return {"ok": True, "count": len(enabled_patterns)}

    # ---------- Video / manual watch levels (pairs + price levels) -------- #

    @app.get("/api/watch_levels")
    def get_watch_levels() -> dict:
        """Pairs + price levels the bot monitors; when price tests one it trades
        per the normal strategy (confirmation/correlation/risk)."""
        return {"levels": list(watch_levels or [])}

    @app.post("/api/watch_levels")
    async def set_watch_levels(request: Request) -> dict:
        """Replace the full watch-level list.
        Body: {"levels": [{"symbol","level","side"?,"timeframe"?,"tol_pips"?,"note"?}]}.
        Each level is assigned an id if missing; 'consumed' is preserved/defaulted."""
        if watch_levels is None:
            raise HTTPException(503, "Watch levels not available")
        try:
            body = await request.json()
        except Exception:
            raise HTTPException(400, "Invalid JSON payload")
        items = body.get("levels", [])
        if not isinstance(items, list):
            raise HTTPException(400, "levels must be a list")
        cleaned = []
        for it in items:
            if not isinstance(it, dict) or "symbol" not in it or "level" not in it:
                continue
            try:
                lvl = float(it["level"])
            except (TypeError, ValueError):
                continue
            cleaned.append({
                "id": it.get("id") or uuid.uuid4().hex[:8],
                "symbol": str(it["symbol"]).upper(),
                "level": lvl,
                "side": (str(it.get("side", "")).lower() or None),
                "timeframe": it.get("timeframe") or None,
                "tol_pips": float(it.get("tol_pips", 15)),
                "note": it.get("note", ""),
                "consumed": bool(it.get("consumed", False)),
            })
        watch_levels.clear()
        watch_levels.extend(cleaned)
        if persist_levels:
            persist_levels(list(watch_levels))
        if notify_levels_changed:
            notify_levels_changed(list(watch_levels))
        await hub.emit("levels:updated", {"count": len(watch_levels)})
        active = sum(1 for l in watch_levels if not l.get("consumed"))
        return {"ok": True, "count": len(watch_levels), "active": active}

    # ---------- Analytics: funnel / shadow / journal ---------------------- #
    _BOT_ROOT = Path(__file__).resolve().parent.parent

    @app.get("/api/funnel")
    def funnel(hours: int = 24) -> dict:
        """Aggregate state/signals.log for the dashboard funnel panel:
        counts by stage, by failed_check (structured logging), clarity
        histogram, plus accepted/filled/deduped totals."""
        from datetime import timedelta
        path = _BOT_ROOT / "state" / "signals.log"
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
        out = {
            "hours": hours, "detected": 0, "accepted": 0, "filled": 0,
            "deduped": 0, "rejected": 0,
            "by_stage": {}, "by_check": {}, "clarity_hist": {},
            "recent_rejects": [],
        }
        if not path.exists():
            return out
        try:
            lines = path.read_text().splitlines()[-6000:]
        except Exception:  # noqa: BLE001
            return out
        for line in lines:
            try:
                j = json.loads(line)
            except Exception:  # noqa: BLE001
                continue
            if (j.get("ts") or "") < cutoff:
                continue
            ev = j.get("event", "")
            if ev == "signal:accepted":
                out["accepted"] += 1
            elif ev == "order:filled":
                out["filled"] += 1
            elif ev == "signal:deduped":
                out["deduped"] += 1
            elif ev == "signal:rejected":
                out["rejected"] += 1
                stage = j.get("stage") or "?"
                out["by_stage"][stage] = out["by_stage"].get(stage, 0) + 1
                check = j.get("failed_check")
                if not check:           # legacy lines: classify from reason text
                    r = (j.get("reason") or "")
                    check = ("candle_anatomy" if "Body ratio" in r or "candle" in r
                             else "momentum" if "Approach too fast" in r
                             else "legacy/" + stage)
                out["by_check"][check] = out["by_check"].get(check, 0) + 1
                if len(out["recent_rejects"]) < 20:
                    out["recent_rejects"].append({
                        "ts": j.get("ts"), "symbol": j.get("symbol"),
                        "tf": j.get("tf"), "setup": j.get("setup"),
                        "side": j.get("side"), "stage": stage,
                        "failed_check": j.get("failed_check"),
                        "clarity": j.get("clarity"), "reason": j.get("reason"),
                    })
            # clarity histogram over every signal that carries a score
            c = j.get("clarity")
            if isinstance(c, (int, float)) and c > 0:
                b = f"{int(c // 20) * 20}-{int(c // 20) * 20 + 19}"
                out["clarity_hist"][b] = out["clarity_hist"].get(b, 0) + 1
        out["detected"] = out["accepted"] + out["rejected"] + out["deduped"]
        return out

    @app.get("/api/shadow")
    def shadow_summary() -> dict:
        """Aggregated shadow outcomes (what rejected signals would have done):
        overall + per rejecting check, plus currently pending count."""
        out_path = _BOT_ROOT / "state" / "shadow_outcomes.jsonl"
        pend_path = _BOT_ROOT / "state" / "shadow_pending.json"
        rows: List[dict] = []
        if out_path.exists():
            try:
                for line in out_path.read_text().splitlines():
                    if line.strip():
                        rows.append(json.loads(line))
            except Exception:  # noqa: BLE001
                pass
        pending = 0
        if pend_path.exists():
            try:
                pending = len(json.loads(pend_path.read_text()))
            except Exception:  # noqa: BLE001
                pass

        def _stats(rs: List[dict]) -> dict:
            n = len(rs)
            wins = sum(1 for r in rs if r.get("outcome") == "win")
            losses = sum(1 for r in rs if r.get("outcome") == "loss")
            timeouts = n - wins - losses
            avg_r = round(sum(float(r.get("r", 0)) for r in rs) / n, 2) if n else 0.0
            return {"n": n, "wins": wins, "losses": losses, "timeouts": timeouts,
                    "win_pct": round(100.0 * wins / n, 1) if n else 0.0, "avg_r": avg_r}

        by_check: Dict[str, List[dict]] = {}
        for r in rows:
            key = r.get("failed_check") or r.get("stage") or "?"
            by_check.setdefault(key, []).append(r)
        return {
            "pending": pending,
            "overall": _stats(rows),
            "by_check": {k: _stats(v) for k, v in by_check.items()},
            "note": "Hypothetical plain SL/TP outcomes of REJECTED signals. "
                    "avg_r > 0 on a meaningful sample = that gate may be too strict.",
        }

    @app.get("/api/journal")
    def journal_list() -> dict:
        """Summaries of all journaled trades (newest first)."""
        jdir = _BOT_ROOT / "state" / "trade_journal"
        items: List[dict] = []
        if jdir.exists():
            for f in jdir.glob("*.json"):
                try:
                    rec = json.loads(f.read_text())
                    items.append({
                        "ticket": rec.get("ticket"), "symbol": rec.get("symbol"),
                        "timeframe": rec.get("timeframe"), "setup": rec.get("setup"),
                        "side": rec.get("side"), "status": rec.get("status"),
                        "entry_time": rec.get("entry_time"),
                        "net_pnl": rec.get("net_pnl"),
                        "n_exits": len(rec.get("exits") or []),
                        "clarity": (rec.get("signal") or {}).get("clarity_score"),
                    })
                except Exception:  # noqa: BLE001
                    continue
        items.sort(key=lambda r: r.get("entry_time") or "", reverse=True)
        return {"trades": items, "count": len(items)}

    @app.get("/api/journal/{ticket}")
    def journal_detail(ticket: int) -> dict:
        """Full journal record for one trade: signal (incl. pattern geometry +
        clarity), the bars that FORMED the pattern, every bar of the trade,
        and all exit legs — everything the trade chart needs."""
        f = _BOT_ROOT / "state" / "trade_journal" / f"{ticket}.json"
        if not f.exists():
            raise HTTPException(404, f"No journal record for ticket {ticket}")
        try:
            return json.loads(f.read_text())
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(500, f"Journal record unreadable: {exc}")

    @app.post("/api/mode")
    async def mode(req: ModeChange) -> dict:
        if req.mode not in ("paper", "live"):
            raise HTTPException(400, "mode must be 'paper' or 'live'")
        ok, why = set_mode(req.mode)
        if not ok:
            raise HTTPException(409, why)
        await hub.emit("engine:mode", {"mode": req.mode, "note": why})
        return {"mode": req.mode, "note": why}

    @app.post("/api/halt")
    async def do_halt() -> dict:
        halt()
        await hub.emit("engine:halt", {"halted": True})
        return {"halted": True}

    @app.post("/api/resume")
    async def do_resume() -> dict:
        resume()
        await hub.emit("engine:halt", {"halted": False})
        return {"halted": False}

    @app.post("/api/reset")
    async def do_reset() -> dict:
        """
        Reset paper trading session to a clean slate:
          - Clears open positions + closed trade history
          - Restores equity to starting balance
          - Resets daily/weekly risk counters
          - Clears the signals log and WebSocket event history
        """
        reset()
        signals_log.clear()
        hub._history.clear()
        await hub.emit("engine:reset", {"reset": True, "ts": _now_iso()})
        log.info("Paper session reset via /api/reset")
        return {"ok": True, "message": "Paper session reset to fresh start"}

    # ---------- WebSocket ---------------------------------------------- #
    @app.websocket("/ws")
    async def ws_endpoint(ws: WebSocket) -> None:
        await hub.connect(ws)
        try:
            while True:
                # Dashboard can send pings; we ignore content
                await ws.receive_text()
        except WebSocketDisconnect:
            hub.disconnect(ws)

    # ---------- Static dashboard --------------------------------------- #
    if dashboard_dir and dashboard_dir.exists():
        app.mount("/", StaticFiles(directory=str(dashboard_dir), html=True), name="dashboard")

    return app


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
