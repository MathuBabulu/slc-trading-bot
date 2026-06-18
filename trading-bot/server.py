"""SLC Trading Bot server.

  python server.py            # that's it — one process runs everything:
    - HTTP endpoints for the MT5DataBridge EA (feed, bars, pairs, commands)
    - the SLC strategy engine (paper/live)
    - the self-evaluation agent
    - the Telegram notifier
    - the web dashboard  ->  http://localhost:8765
"""
import json
import os
import threading
import time

import yaml
from flask import Flask, jsonify, request, send_from_directory

import agent
import engine
import notifier
import storage

BASE = os.path.dirname(os.path.abspath(__file__))
app = Flask(__name__, static_folder=None)

with open(os.path.join(BASE, "config.yaml")) as f:
    CFG = yaml.safe_load(f)


def seed_settings():
    """Copy yaml defaults into the DB once; DB wins afterwards."""
    defaults = {
        "trading_mode": CFG["engine"]["trading_mode"],
        "modes": CFG["engine"]["modes"],
        "paper_balance": CFG["engine"]["paper_start_balance"],
        "enabled_pairs": CFG["pairs"]["enabled"],
        # Watch-only pairs: streamed + analyzed + outcome-tracked as SHADOW
        # trades (no money, no concurrency impact) to grow the study sample.
        "watch_pairs": [
            "AUDJPY", "CADJPY", "CHFJPY", "NZDJPY",
            "EURAUD", "EURCAD", "EURCHF", "EURNZD",
            "GBPAUD", "GBPCAD", "GBPCHF", "GBPNZD",
            "AUDCAD", "AUDCHF", "AUDNZD", "NZDCAD", "NZDCHF", "CADCHF",
            "XPDUSD", "UKOIL", "ADAUSD", "DOGUSD", "LINKUSD", "BCHUSD",
        ],
        "agent_enabled": CFG["agent"]["enabled"],
        "telegram_enabled": CFG["telegram"]["enabled"],
        "telegram_bot_token": CFG["telegram"]["bot_token"],
        "telegram_chat_id": CFG["telegram"]["chat_id"],
        "notify_signals": CFG["telegram"]["notify_signals"],
        "discord_enabled": False,
        "discord_webhook_url": "",
        "agent_disabled_pairs": [],
        "agent_disabled_modes": [],
    }
    for k, v in CFG["risk"].items():
        defaults[k] = v
    existing = storage.all_settings()
    for k, v in defaults.items():
        if k not in existing:
            storage.set_setting(k, v)


# ======================================================================
# EA-facing endpoints (contract fixed by MT5DataBridge.mq5 — do not change)
# ======================================================================
@app.route("/api/mt5_feed", methods=["POST"])
def mt5_feed():
    engine.ingest_feed(request.get_json(force=True, silent=True) or {})
    return jsonify({"ok": True})


@app.route("/api/mt5_bars", methods=["POST"])
def mt5_bars():
    engine.ingest_bars(request.get_json(force=True, silent=True) or {})
    return jsonify({"ok": True})


@app.route("/api/pairs", methods=["GET"])
def pairs():
    enabled = storage.get_setting("enabled_pairs", [])
    watch = storage.get_setting("watch_pairs", [])
    # EA streams everything; the engine decides what is tradable vs shadow
    combined = enabled + [w for w in watch if w not in enabled]
    return jsonify({"enabled_pairs": combined, "count": len(combined)})


@app.route("/api/commands/next", methods=["GET"])
def commands_next():
    cmd = storage.next_command()
    return jsonify({"command": cmd})


@app.route("/api/commands/ack/<cmd_id>", methods=["POST"])
def commands_ack(cmd_id):
    return jsonify({"ok": storage.ack_command(cmd_id)})


# ======================================================================
# News-agent-facing endpoints (news_agent.py runs as a separate process)
# ======================================================================
@app.route("/api/status", methods=["GET"])
def status():
    """Open positions snapshot for the news agent: live broker positions
    PLUS the bot's paper trades (as pseudo-positions with negative tickets,
    so news-driven SL protection covers paper trading too)."""
    feed = engine.get_feed()
    positions = list(feed.get("open_positions", []))
    prices = feed.get("prices", {})
    for tr in storage.open_trades("paper"):
        px = prices.get(tr["symbol"], {})
        cur = px.get("bid") if tr["side"] == "buy" else px.get("ask")
        positions.append({
            "ticket": -tr["id"],                  # negative = paper trade id
            "symbol": tr["symbol"], "side": tr["side"], "lots": tr["lots"],
            "entry": tr["entry"], "sl": tr["sl"], "tp": tr["tp2"],
            "current": cur or tr["entry"],
            "unrealized_pnl": engine.trade_upnl(tr),
            "magic": 770001, "comment": "SLC#%d(paper)" % tr["id"],
        })
    return jsonify({
        "open_positions": positions,
        "ea_connected": (time.time() - feed.get("last_feed_t", 0)) < 30,
    })


@app.route("/api/commands", methods=["POST"])
def commands_post():
    """Accept SL-management commands from the news agent and enqueue them
    for the EA. Only stop-tightening commands are allowed from here —
    open/close stay exclusive to the engine."""
    body = request.get_json(force=True, silent=True) or {}
    cmd_type = body.get("type")
    if cmd_type not in ("trail_sl", "move_sl_be", "close_trade"):
        return jsonify({"ok": False,
                        "error": "type must be trail_sl|move_sl_be|close_trade"}), 400
    ticket = body.get("ticket")
    if not ticket:
        return jsonify({"ok": False, "error": "ticket required"}), 400
    reason = "[news] %s" % (body.get("reason") or "")[:380]

    # ---- news cut: close a losing trade at market -----------------------
    if cmd_type == "close_trade":
        if int(ticket) < 0:                       # paper trade
            tr = storage.query_one(
                "SELECT * FROM trades WHERE id=? AND status='open' AND mode='paper'",
                (-int(ticket),))
            if not tr:
                return jsonify({"ok": False, "error": "paper trade not found/open"}), 404
            px = engine.feed_state["prices"].get(tr["symbol"])
            if not px:
                return jsonify({"ok": False, "error": "no live price to close at"}), 409
            cur = px["bid"] if tr["side"] == "buy" else px["ask"]
            engine._close_paper(tr, cur, "news cut: %s" % reason[:140])
            storage.log_agent("info", "news_command",
                              "close_trade PAPER #%d at %.5f" % (tr["id"], cur))
            return jsonify({"ok": True, "id": 0})
        cmd_id = storage.enqueue_command("close_trade",
                                         {"ticket": ticket, "lots": 0, "reason": reason})
        storage.log_agent("info", "news_command",
                          "close_trade ticket=%s (full) queued for EA" % ticket)
        return jsonify({"ok": True, "id": cmd_id})

    new_sl = body.get("new_sl")
    if not new_sl:
        return jsonify({"ok": False, "error": "new_sl required"}), 400
    new_sl = float(new_sl)

    # negative ticket = paper trade -> apply the SL directly in the DB
    # (tighten-only; there is no broker position to command)
    if int(ticket) < 0:
        tr = storage.query_one(
            "SELECT * FROM trades WHERE id=? AND status='open' AND mode='paper'",
            (-int(ticket),))
        if not tr:
            return jsonify({"ok": False, "error": "paper trade not found/open"}), 404
        tighter = (new_sl > tr["sl"]) if tr["side"] == "buy" else (new_sl < tr["sl"])
        if not tighter:
            return jsonify({"ok": True, "id": 0, "note": "SL already tighter"})
        storage.update_trade(tr["id"], {"sl": new_sl})
        storage.log_agent("info", "news_command",
                          "%s PAPER #%d sl %.5f -> %.5f" % (cmd_type, tr["id"], tr["sl"], new_sl))
        notifier.send("🛡 <b>News SL update</b> (PAPER)\n<b>%s %s</b> #%d\nSL → <code>%.5f</code>\n<i>%s</i>"
                      % (tr["side"].upper(), tr["symbol"], tr["id"], new_sl, reason[:200]))
        return jsonify({"ok": True, "id": 0})

    cmd_id = storage.enqueue_command(cmd_type, {
        "ticket": ticket, "symbol": body.get("symbol", ""),
        "new_sl": new_sl, "reason": reason,
    })
    # keep the engine's mirror in sync so its trail logic doesn't fight this
    tr = storage.query_one(
        "SELECT * FROM trades WHERE ticket=? AND status='open'", (ticket,))
    if tr:
        tighter = (new_sl > tr["sl"]) if tr["side"] == "buy" else (new_sl < tr["sl"])
        if tighter:
            storage.update_trade(tr["id"], {"sl": new_sl})
    storage.log_agent("info", "news_command",
                      "%s ticket=%s new_sl=%s" % (cmd_type, ticket, new_sl))
    return jsonify({"ok": True, "id": cmd_id})


# ======================================================================
# Dashboard API
# ======================================================================
@app.route("/")
def index():
    return send_from_directory(os.path.join(BASE, "dashboard"), "index.html")


@app.route("/api/state")
def state():
    feed = engine.get_feed()
    p = engine.params()
    mode = p["trading_mode"]
    bal = engine._balance(mode if mode != "off" else "paper", p)
    open_trades = [t for t in storage.open_trades() if t["mode"] != "shadow"]
    for t in open_trades:                       # annotate live/unrealized P&L
        t["upnl"] = engine.trade_upnl(t)
    return jsonify({
        "feed": feed,
        "settings": storage.all_settings(),
        "open_trades": open_trades,
        "open_pnl": round(sum(t["upnl"] or 0 for t in open_trades), 2),
        "shadow_open": len(storage.open_trades("shadow")),
        "paper_balance": bal if mode != "live" else None,
        "analysis": engine.get_last_info(),
        "server_time": int(time.time()),
        "ea_connected": (time.time() - feed.get("last_feed_t", 0)) < 30,
    })


@app.route("/api/bars")
def bars():
    symbol = request.args.get("symbol", "EURUSD")
    tf = request.args.get("tf", "1h")
    limit = int(request.args.get("limit", 300))
    return jsonify({"symbol": symbol, "tf": tf,
                    "bars": storage.get_bars(symbol, tf, limit)})


@app.route("/api/trades")
def trades():
    where, args = ["1=1"], []
    for col in ("mode", "trade_mode", "symbol", "grade", "status"):
        v = request.args.get(col)
        if v and v != "all":
            where.append("%s=?" % col)
            args.append(v)
    res = request.args.get("result")
    if res == "win":
        where.append("pnl > 0")
    elif res == "loss":
        where.append("pnl <= 0")
    days = request.args.get("days")
    if days and days != "all":
        where.append("entry_time >= ?")
        args.append(int(time.time()) - int(days) * 86400)
    rows = storage.query(
        "SELECT * FROM trades WHERE %s ORDER BY entry_time DESC LIMIT 500"
        % " AND ".join(where), tuple(args))
    return jsonify({"trades": rows})


@app.route("/api/performance")
def performance():
    return jsonify(agent.evaluate())


@app.route("/api/equity")
def equity():
    mode = request.args.get("mode", "paper")
    return jsonify({"points": storage.query(
        "SELECT t, balance, equity FROM equity WHERE mode=? ORDER BY t", (mode,))})


@app.route("/api/signals")
def signals():
    return jsonify({"signals": storage.query(
        "SELECT * FROM signals ORDER BY t DESC LIMIT 100")})


@app.route("/api/agent_log")
def agent_log():
    return jsonify({"log": storage.query(
        "SELECT * FROM agent_log ORDER BY t DESC LIMIT 100")})


@app.route("/api/settings", methods=["POST"])
def update_settings():
    body = request.get_json(force=True) or {}
    allowed = {
        "trading_mode", "modes", "risk_pct", "b_setup_risk_factor", "min_rr",
        "atr_buffer", "max_concurrent", "daily_stop_pct", "weekly_stop_pct",
        "min_grade", "regime_max", "regime_b_ban", "max_spread_frac", "vol_mult",
        "enabled_pairs", "watch_pairs", "agent_enabled", "telegram_enabled",
        "telegram_bot_token", "telegram_chat_id", "notify_signals", "notify_agent",
        "discord_enabled", "discord_webhook_url",
        "paper_balance", "agent_disabled_pairs", "agent_disabled_modes",
    }
    changed = {}
    for k, v in body.items():
        if k in allowed:
            storage.set_setting(k, v)
            changed[k] = v
    if "trading_mode" in changed:
        storage.log_agent("info", "trading_mode",
                          "switched to %s via dashboard" % changed["trading_mode"])
        notifier.send("🔁 Trading mode switched to <b>%s</b>"
                      % str(changed["trading_mode"]).upper())
    return jsonify({"ok": True, "changed": changed})


@app.route("/api/trade/close/<int:trade_id>", methods=["POST"])
def close_trade(trade_id):
    return jsonify({"ok": engine.manual_close(trade_id)})


@app.route("/api/telegram_test", methods=["POST"])
def telegram_test():
    return jsonify(notifier.send_test())


@app.route("/api/agent/run", methods=["POST"])
def agent_run():
    threading.Thread(target=agent.run_once, args=(CFG["agent"], notifier.send),
                     daemon=True).start()
    return jsonify({"ok": True})


@app.route("/api/spread")
def api_spread():
    import json as _json
    feed = engine.get_feed()
    prices = feed.get("prices", {})
    try:
        with open(os.path.join(BASE, "state", "open_spread.json")) as f:
            osp = _json.load(f)
    except Exception:
        osp = {}

    opens = []
    for tr in storage.open_trades():
        if tr["mode"] not in ("paper", "shadow"):
            continue
        pr = prices.get(tr["symbol"], {})
        rec = osp.get(str(tr["id"]), {})
        opens.append({
            "id": tr["id"], "symbol": tr["symbol"], "side": tr["side"],
            "mode": tr["mode"], "entry": tr["entry"], "sl": tr["sl"],
            "cur_spread_pts": rec.get("cur_spread_pts", pr.get("spread")),
            "max_spread_pts": rec.get("max_spread_pts"),
        })

    total = induced = 0
    by_sym, recent = {}, []
    try:
        with open(os.path.join(BASE, "state", "spread_trace.jsonl")) as f:
            lines = f.readlines()
        for ln in lines:
            try:
                r = _json.loads(ln)
            except Exception:
                continue
            total += 1
            if r.get("spread_induced"):
                induced += 1
                by_sym[r["symbol"]] = by_sym.get(r["symbol"], 0) + 1
        recent = [_json.loads(x) for x in lines[-8:] if x.strip()]
    except Exception:
        pass

    return jsonify({"open": opens,
                    "stops": {"total": total, "spread_induced": induced,
                              "by_symbol": by_sym, "recent": recent}})


@app.route("/spread")
def spread_page():
    from flask import Response
    html = """<!doctype html><html><head><meta charset='utf-8'>
<title>SLC - spread monitor</title><meta name='viewport' content='width=device-width,initial-scale=1'>
<style>
 body{font-family:system-ui,-apple-system,sans-serif;margin:0;padding:24px;background:#0f1115;color:#e7e9ee}
 h1{font-size:18px;font-weight:600;margin:0 0 16px}
 .cards{display:flex;gap:12px;flex-wrap:wrap;margin-bottom:20px}
 .card{background:#171a21;border:1px solid #232733;border-radius:10px;padding:14px 18px;min-width:130px}
 .card .label{font-size:12px;color:#9aa3b2}.card .val{font-size:24px;font-weight:600;margin-top:4px}
 table{width:100%;border-collapse:collapse;font-size:13px;margin-bottom:24px}
 th,td{text-align:left;padding:8px 10px;border-bottom:1px solid #232733}
 th{color:#9aa3b2;font-weight:500}
 .warn{color:#f0b34a}.bad{color:#e26a6a}.ok{color:#5fc08a}
 .pill{display:inline-block;padding:2px 8px;border-radius:6px;background:#232733;font-size:12px}
 .muted{color:#9aa3b2}
</style></head><body>
<h1>Spread monitor <span class='muted' id='ts'></span></h1>
<div class='cards'>
 <div class='card'><div class='label'>Total SL exits</div><div class='val' id='c-total'>-</div></div>
 <div class='card'><div class='label'>Spread-induced</div><div class='val bad' id='c-induced'>-</div></div>
 <div class='card'><div class='label'>Open trades</div><div class='val' id='c-open'>-</div></div>
 <div class='card'><div class='label'>Widest open spread</div><div class='val warn' id='c-wide'>-</div></div>
</div>
<h1 style='font-size:15px'>Open trades</h1>
<table id='opent'><thead><tr><th>#</th><th>Symbol</th><th>Side</th><th>Mode</th>
 <th>Cur spread</th><th>Max spread</th></tr></thead><tbody></tbody></table>
<h1 style='font-size:15px'>Recent stops</h1>
<table id='stopt'><thead><tr><th>Time</th><th>Symbol</th><th>Side</th><th>Max spread</th><th>Cause</th></tr></thead><tbody></tbody></table>
<script>
async function load(){
 const r=await fetch('/api/spread'); const d=await r.json();
 document.getElementById('ts').textContent='- '+new Date().toLocaleTimeString();
 document.getElementById('c-total').textContent=d.stops.total;
 document.getElementById('c-induced').textContent=d.stops.spread_induced;
 document.getElementById('c-open').textContent=d.open.length;
 const wide=Math.max(0,...d.open.map(o=>o.max_spread_pts||0));
 document.getElementById('c-wide').textContent=wide?wide.toFixed(1)+'p':'-';
 const ob=document.querySelector('#opent tbody'); ob.innerHTML='';
 d.open.forEach(o=>{const tr=document.createElement('tr');
  const ms=o.max_spread_pts||0; const cls=ms>=10?'bad':ms>=4?'warn':'ok';
  tr.innerHTML=`<td>${o.id}</td><td>${o.symbol}</td><td>${o.side}</td><td><span class='pill'>${o.mode}</span></td>`+
   `<td>${o.cur_spread_pts!=null?o.cur_spread_pts.toFixed(1)+'p':'-'}</td>`+
   `<td class='${cls}'>${ms?ms.toFixed(1)+'p':'-'}</td>`; ob.appendChild(tr);});
 if(!d.open.length) ob.innerHTML="<tr><td colspan='6' class='muted'>no open paper/shadow trades</td></tr>";
 const sb=document.querySelector('#stopt tbody'); sb.innerHTML='';
 d.stops.recent.slice().reverse().forEach(s=>{const tr=document.createElement('tr');
  const t=new Date((s.t||0)*1000).toLocaleString();
  tr.innerHTML=`<td>${t}</td><td>${s.symbol}</td><td>${s.side}</td>`+
   `<td>${s.max_spread_pts!=null?Number(s.max_spread_pts).toFixed(1)+'p':'-'}</td>`+
   `<td class='${s.spread_induced?"bad":"muted"}'>${s.spread_induced?'spread-induced':'price'}</td>`; sb.appendChild(tr);});
 if(!d.stops.recent.length) sb.innerHTML="<tr><td colspan='5' class='muted'>no stops recorded yet</td></tr>";
}
load(); setInterval(load,5000);
</script></body></html>"""
    return Response(html, mimetype="text/html")


# ======================================================================
if __name__ == "__main__":
    storage.init()
    seed_settings()
    notifier.start()
    engine.set_notifier(notifier.send)

    threading.Thread(target=engine.engine_loop,
                     args=(CFG["engine"]["poll_seconds"],), daemon=True).start()
    threading.Thread(target=agent.agent_loop,
                     args=(CFG["agent"], notifier.send), daemon=True).start()

    host, port = CFG["server"]["host"], CFG["server"]["port"]
    print("=" * 60)
    print(" SLC Trading Bot")
    print(" Dashboard : http://localhost:%d" % port)
    print(" EA target : http://<this-machine-ip>:%d" % port)
    print("=" * 60)
    app.run(host=host, port=port, threaded=True)
