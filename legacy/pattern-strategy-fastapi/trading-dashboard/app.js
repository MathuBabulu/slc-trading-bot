// ============================================================================
// Price-Action Trading Dashboard — front-end logic
// ============================================================================

(function () {
  "use strict";

  // Start clean: clear any previously cached/imported history so only the
  // agent's own trades (loaded live from /api/agent/trades) are shown. A manual
  // report import during the session still works but is no longer auto-restored.
  try { localStorage.removeItem("tradeData"); } catch (_) {}

  const D = window.tradeData || null;

  // Chart.js global defaults — dark theme
  if (window.Chart) {
    Chart.defaults.color = "#94a3b8";
    Chart.defaults.borderColor = "#1f2a44";
    Chart.defaults.font.family = "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif";
    Chart.defaults.font.size = 11;
  }

  // ============================================================================
  // PAIRS CATALOG — master list of all supported trading pairs
  // ============================================================================
  const PAIRS_CATALOG = [
    { group: "Forex — Majors",  pairs: ["EURUSD","GBPUSD","USDJPY","AUDUSD","USDCAD","USDCHF","NZDUSD"] },
    { group: "Forex — Minors",  pairs: ["EURGBP","EURJPY","GBPJPY","AUDJPY","EURAUD","EURNZD","GBPAUD","GBPCAD","CADJPY","CHFJPY","NZDJPY","AUDCAD","AUDCHF","AUDNZD","EURCAD","EURCHF"] },
    { group: "Metals",          pairs: ["XAUUSD","XAGUSD","XPTUSD"] },
    { group: "Crypto",          pairs: ["BTCUSD","ETHUSD","LTCUSD","XRPUSD","BNBUSD","SOLUSD"] },
    { group: "Energy",          pairs: ["USOIL","UKOIL","NATGAS"] },
    { group: "Indices",         pairs: ["US30","US500","NAS100","GER40","UK100","JPN225","AUS200"] },
  ];

  // All pair names flat
  const ALL_PAIRS = PAIRS_CATALOG.flatMap(g => g.pairs);

  // Persist enabled pairs in localStorage
  function loadEnabledPairs() {
    try {
      const raw = localStorage.getItem("enabledPairs");
      if (raw) {
        const arr = JSON.parse(raw);
        if (Array.isArray(arr)) return new Set(arr);
      }
    } catch (_) {}
    return new Set(ALL_PAIRS); // default: all enabled
  }

  function saveEnabledPairs(set) {
    try { localStorage.setItem("enabledPairs", JSON.stringify([...set])); } catch (_) {}
    syncPairsToServer(set);
  }

  // Sync enabled pairs to the trading bot server so the engine + news agent
  // only monitor pairs that are toggled ON in the Pairs Manager.
  function syncPairsToServer(set) {
    fetch("/api/pairs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ enabled_pairs: [...set] }),
    }).catch(() => {}); // silent — server may not be running
  }

  let enabledPairs = loadEnabledPairs();
  // Sync current toggle state to server on page load
  syncPairsToServer(enabledPairs);

  // Returns true if a symbol should be shown (case-insensitive, strips broker suffixes)
  function isPairEnabled(symbol) {
    if (!symbol) return true;
    const s = symbol.toUpperCase().replace(/[._-].*$/, ""); // strip suffix like .r, _SB
    // If the pair isn't in the catalog at all, show it (don't hide unknown pairs)
    if (!ALL_PAIRS.includes(s)) return true;
    return enabledPairs.has(s);
  }

  // Return trades filtered by enabled pairs
  function getActiveTrades() {
    if (!D || !D.trades) return [];
    return D.trades.filter(t => isPairEnabled(t.symbol));
  }

  // -------- State --------
  let state = {
    activeTab: "overview",
    activePattern: "DT",
    sort: { key: "closeTime", dir: "desc" },
    filters: { symbol: "", setup: "", category: "", outcome: "", search: "" },
    charts: {},
  };

  // ============================================================================
  // BOOTSTRAP
  // ============================================================================
  document.addEventListener("DOMContentLoaded", () => {
    initTabs();
    initPatternTabs();
    initChecklist();
    initFileLoader();
    initBotClient();
    initPairsManager();
    initPatternsManager();
    initMT5ChartControls();
    // Refresh monitor tab every 10s to keep P&L and pair cards current
    setInterval(() => {
      if (typeof BOT !== "undefined" && BOT.connected) {
        fetch(BOT.baseUrl + "/api/status").then(r => r.json()).then(snap => {
          renderBotSnapshot(snap);
          renderMonitorTab();
        }).catch(() => {});
      } else {
        renderMonitorTab();
      }
    }, 10000);

    if (!D) {
      setDataSource("no-data");
      return;
    }
    setDataSource(D.isMockData ? "mock" : "live");
    document.getElementById("refresh-time").textContent =
      "Generated " + formatRelativeTime(D.generatedAt);

    renderAll();
  });

  // ============================================================================
  // TABS
  // ============================================================================
  function initTabs() {
    document.querySelectorAll("nav.tabs button").forEach(btn => {
      btn.addEventListener("click", () => {
        const tab = btn.dataset.tab;
        state.activeTab = tab;
        document.querySelectorAll("nav.tabs button").forEach(b => b.classList.toggle("active", b === btn));
        document.querySelectorAll(".tab-content").forEach(c => {
          c.classList.toggle("active", c.id === "tab-" + tab);
        });
        // Lazy-render charts when their tab becomes visible (avoids sizing issues)
        if (tab === "performance" || tab === "adherence" || tab === "overview") {
          requestAnimationFrame(() => renderCharts());
        }
        if (tab === "levels") loadWatchLevels();
      });
    });
    initWatchLevels();
  }

  // ---- Video watch levels (pairs + price levels fed from the live) ----------
  let _watchLevels = [];
  function initWatchLevels() {
    const addBtn = document.getElementById("wl-add");
    if (addBtn) addBtn.addEventListener("click", addWatchLevel);
    loadWatchLevels();
  }
  function loadWatchLevels() {
    fetch(BOT.baseUrl + "/api/watch_levels")
      .then(r => r.json())
      .then(d => { _watchLevels = d.levels || []; renderWatchLevels(); })
      .catch(() => { /* server may be offline */ });
  }
  function saveWatchLevels() {
    const msg = document.getElementById("wl-msg");
    fetch(BOT.baseUrl + "/api/watch_levels", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ levels: _watchLevels }),
    }).then(r => r.json()).then(d => {
      if (msg) msg.textContent = `Saved — ${d.active} active level(s).`;
      loadWatchLevels();
    }).catch(() => { if (msg) msg.textContent = "Could not save — is the server running?"; });
  }
  function addWatchLevel() {
    const sym = (document.getElementById("wl-symbol").value || "").trim().toUpperCase();
    const lvl = parseFloat(document.getElementById("wl-level").value);
    const msg = document.getElementById("wl-msg");
    if (!sym || isNaN(lvl)) { if (msg) msg.textContent = "Enter a pair and a numeric level."; return; }
    _watchLevels.push({
      symbol: sym, level: lvl,
      side: document.getElementById("wl-side").value || null,
      timeframe: document.getElementById("wl-tf").value || null,
      tol_pips: parseFloat(document.getElementById("wl-tol").value) || 15,
      note: document.getElementById("wl-note").value || "",
      consumed: false,
    });
    document.getElementById("wl-symbol").value = "";
    document.getElementById("wl-level").value = "";
    document.getElementById("wl-note").value = "";
    saveWatchLevels();
  }
  function removeWatchLevel(id) {
    _watchLevels = _watchLevels.filter(l => l.id !== id);
    saveWatchLevels();
  }
  window.removeWatchLevel = removeWatchLevel;   // for inline onclick
  function renderWatchLevels() {
    const table = document.getElementById("wl-table");
    const empty = document.getElementById("wl-empty");
    const tbody = table ? table.querySelector("tbody") : null;
    if (!table || !tbody) return;
    if (!_watchLevels.length) {
      table.style.display = "none"; if (empty) empty.style.display = "block"; return;
    }
    if (empty) empty.style.display = "none";
    table.style.display = "table";
    tbody.innerHTML = _watchLevels.map(l => `
      <tr style="${l.consumed ? "opacity:.5;" : ""}">
        <td><strong>${l.symbol}</strong></td>
        <td class="num">${_fmtPrice(l.symbol, l.level)}</td>
        <td>${l.side ? (l.side === "buy" ? '<span style="color:var(--green)">BUY</span>' : '<span style="color:var(--red)">SELL</span>') : "Auto"}</td>
        <td>${l.timeframe || "Any"}</td>
        <td class="num">${l.tol_pips ?? 15}</td>
        <td>${l.consumed ? '<span class="chip">Traded</span>' : '<span class="chip green">Watching</span>'}</td>
        <td style="color:var(--text-muted);font-size:12px;">${(l.note || "").slice(0, 40)}</td>
        <td><button onclick="removeWatchLevel('${l.id}')" style="background:none;border:none;color:var(--text-dim);cursor:pointer;">✕</button></td>
      </tr>`).join("");
  }

  function initPatternTabs() {
    document.querySelectorAll(".pattern-tabs button").forEach(btn => {
      btn.addEventListener("click", () => {
        const p = btn.dataset.pattern;
        document.querySelectorAll(".pattern-tabs button").forEach(b => b.classList.toggle("active", b === btn));
        document.querySelectorAll(".pattern-content").forEach(c => c.classList.toggle("active", c.dataset.pattern === p));
      });
    });
  }

  // ============================================================================
  // DATA SOURCE STATUS
  // ============================================================================
  function setDataSource(kind) {
    const pill = document.getElementById("data-source-pill");
    const txt  = document.getElementById("data-source-text");
    pill.classList.remove("connected", "disconnected");
    if (kind === "live") {
      pill.classList.add("connected");
      txt.textContent = "Live MT5 data";
    } else if (kind === "mock") {
      txt.textContent = "Sample data (no MT5 yet)";
    } else {
      pill.classList.add("disconnected");
      txt.textContent = "No data file loaded";
    }
  }

  // ============================================================================
  // METRICS
  // ============================================================================
  function computeMetrics(trades) {
    const closed = trades || [];
    const wins   = closed.filter(t => t.pnl > 0);
    const losses = closed.filter(t => t.pnl < 0);
    const bes    = closed.filter(t => t.pnl === 0);

    const totalPnl     = closed.reduce((s, t) => s + t.pnl, 0);
    const grossWin     = wins.reduce((s, t) => s + t.pnl, 0);
    const grossLoss    = Math.abs(losses.reduce((s, t) => s + t.pnl, 0));
    const winRate      = closed.length ? (wins.length / closed.length) * 100 : 0;
    const avgRR        = closed.length ? closed.reduce((s, t) => s + (t.rr || 0), 0) / closed.length : 0;
    const profitFactor = grossLoss > 0 ? grossWin / grossLoss : (grossWin > 0 ? Infinity : 0);
    const scored = closed.filter(t => t.adherence && t.adherence.score != null);
    const avgAdherence = scored.length
      ? scored.reduce((s, t) => s + t.adherence.score, 0) / scored.length
      : 0;
    const biggestWin  = closed.length ? Math.max(...closed.map(t => t.pnl)) : 0;
    const biggestLoss = closed.length ? Math.min(...closed.map(t => t.pnl)) : 0;

    // current week win rate
    const oneWeekAgo = new Date(); oneWeekAgo.setDate(oneWeekAgo.getDate() - 7);
    const thisWeek = closed.filter(t => new Date(t.closeTime) >= oneWeekAgo);
    const weekWR = thisWeek.length ? (thisWeek.filter(t => t.pnl > 0).length / thisWeek.length) * 100 : 0;

    return {
      total: closed.length, wins: wins.length, losses: losses.length, bes: bes.length,
      totalPnl, grossWin, grossLoss, winRate, avgRR, profitFactor, avgAdherence,
      biggestWin, biggestLoss, weekTrades: thisWeek.length, weekWR
    };
  }

  // ============================================================================
  // RENDER
  // ============================================================================
  function renderAll() {
    renderKPIs();
    renderOpenPositions();
    renderTradesTab();
    renderCharts();
    renderAdherence();
    populateFilters();
  }

  // -------- KPIs --------
  function renderKPIs() {
    const m = computeMetrics(getActiveTrades());
    const grid = document.getElementById("kpi-grid");
    const ccy = D.account.currency || "USD";

    const floating = (D.openPositions || []).reduce(
      (s, p) => s + (p.unrealizedPnl || 0), 0);
    const eq = (Number(D.account.balance) || 0) + floating;

    const kpis = [
      { label: "Win Rate (all)", value: m.winRate.toFixed(0) + "%", cls: m.winRate >= 70 ? "green" : m.winRate >= 50 ? "" : "red", delta: `${m.wins}W · ${m.losses}L · ${m.bes}BE` },
      { label: "Net P&L", value: fmtMoney(m.totalPnl, ccy), cls: m.totalPnl >= 0 ? "green" : "red", delta: `${m.total} closed trades` },
      { label: "Avg R:R Achieved", value: m.avgRR.toFixed(2), cls: m.avgRR >= 2 ? "green" : "red", delta: "Target: 1:2 min" },
      { label: "Profit Factor", value: isFinite(m.profitFactor) ? m.profitFactor.toFixed(2) : "∞", cls: m.profitFactor >= 1.5 ? "green" : "red", delta: "Gross win/loss ratio" },
      { label: "This Week", value: m.weekTrades + " trades", cls: "", delta: `${m.weekWR.toFixed(0)}% WR · cap 10–12` },
      { label: "Avg Adherence", value: m.avgAdherence.toFixed(0) + "%", cls: m.avgAdherence >= 90 ? "green" : m.avgAdherence >= 75 ? "" : "red", delta: "Rule-pass score" },
      {
        // Balance = realized ledger equity; show floating P&L + true equity.
        label: "Account Balance",
        value: fmtMoney(D.account.balance, ccy),
        cls: "",
        delta: (D.account.broker || "") +
               (floating ? ` · floating ${floating >= 0 ? "+" : "−"}${curSym()}` +
                 `${fmtNumber(Math.abs(floating), 2)} → equity ${fmtMoney(eq, ccy)}` : ""),
      },
      { label: "Open Positions", value: (D.openPositions || []).length, delta: openPnlDelta() },
    ];

    // In-place DOM update: avoid full innerHTML rebuild on each poll cycle.
    // Rebuilding the grid destroys and recreates .kpi elements which causes
    // the CSS entrance animation (rise .35s) to replay every 5 s → FLICKER.
    // Strategy: build the grid once on first render; thereafter only patch the
    // cells whose content actually changed — no DOM destruction, no flicker.
    const existing = grid.querySelectorAll(".kpi");
    if (existing.length === kpis.length) {
      kpis.forEach((k, i) => {
        const kpiEl = existing[i];
        const valEl = kpiEl.querySelector(".value");
        const deltaEl = kpiEl.querySelector(".delta");
        const newVal = String(k.value);
        const newDelta = k.delta || "";
        const newCls = "value " + (k.cls || "");
        if (valEl.textContent !== newVal) valEl.textContent = newVal;
        if (valEl.className !== newCls)   valEl.className  = newCls;
        if (deltaEl.innerHTML !== newDelta) deltaEl.innerHTML = newDelta;
      });
    } else {
      // First render or KPI count changed — full build
      grid.innerHTML = kpis.map(k => `
        <div class="kpi">
          <div class="label">${k.label}</div>
          <div class="value ${k.cls || ""}">${k.value}</div>
          <div class="delta">${k.delta || ""}</div>
        </div>
      `).join("");
    }
  }

  function openPnlDelta() {
    const op = D.openPositions || [];
    if (!op.length) return "—";
    const total = op.reduce((s, p) => s + (p.unrealizedPnl || 0), 0);
    return "Unrealized: " + fmtMoney(total, D.account.currency || "USD");
  }

  // -------- Open positions --------
  function renderOpenPositions() {
    const tbody = document.querySelector("#open-positions-table tbody");
    const op = (D.openPositions || []).filter(p => isPairEnabled(p.symbol));
    if (!op.length) {
      if (tbody.innerHTML !== "") tbody.innerHTML = "";
      document.getElementById("no-open").style.display = "block";
      return;
    }
    document.getElementById("no-open").style.display = "none";
    const ccy = D.account.currency || "USD";
    const newHtml = op.map(p => {
      const cur = (p.current != null) ? p.current : p.entry;
      return `
      <tr>
        <td><strong>${p.symbol}</strong></td>
        <td><span class="chip">${p.setup || "—"}</span></td>
        <td>${p.timeframe || "—"}</td>
        <td>${sideChip(p.side)}</td>
        <td class="num">${p.lots != null ? p.lots : "—"}</td>
        <td class="num">${_fmtPrice(p.symbol, p.entry)}</td>
        <td class="num"><strong>${_fmtPrice(p.symbol, cur)}</strong></td>
        <td class="num" style="color:var(--red);">${_fmtPrice(p.symbol, p.sl)}</td>
        <td class="num" style="color:var(--green);">${_fmtPrice(p.symbol, p.tp)}</td>
        <td class="num ${(p.unrealizedPnl || 0) >= 0 ? "pos" : "neg"}">${fmtMoney(p.unrealizedPnl || 0, ccy)}</td>
        <td class="num">${_pipsCell(p.symbol, p.side, p.entry, cur)}</td>
        <td class="num">${(p.progressPct || 0).toFixed(0)}%${p.scaled ? ' <span class="chip green" style="font-size:9px;">trailing</span>' : ''}</td>
      </tr>`;
    }).join("");
    // Only touch the DOM if the rows actually changed (price ticks update
    // "current" each cycle — an unchanged position should not cause a repaint)
    if (tbody.innerHTML !== newHtml) tbody.innerHTML = newHtml;
  }

  // Badge for how a closed trade ended.
  function closeReasonBadge(reason) {
    const r = (reason || "").toLowerCase();
    if (r === "tp") return `<span class="chip green">TP hit</span>`;
    if (r === "tp_partial") return `<span class="chip green">TP 50% booked</span>`;
    if (r === "sl") return `<span class="chip red">SL hit</span>`;
    if (r === "be") return `<span class="chip">Break-even</span>`;
    if (r === "trail") return `<span class="chip green">Trailed stop</span>`;
    if (r) return `<span class="chip">${r.charAt(0).toUpperCase() + r.slice(1)}</span>`;
    return `<span class="chip">—</span>`;
  }

  // -------- Trades tab --------
  function renderTradesTab() {
    const tbody = document.querySelector("#trades-table tbody");
    const activeTrades = getActiveTrades();
    const filtered = applyFiltersAndSort(activeTrades);

    document.getElementById("trade-count").textContent =
      `${filtered.length} of ${activeTrades.length} trades`;

    const ccy = D.account.currency || "USD";
    tbody.innerHTML = filtered.map(t => `
      <tr>
        <td>${fmtDate(t.closeTime)}</td>
        <td><strong>${t.symbol}</strong></td>
        <td><span class="chip">${t.setup || "—"}</span></td>
        <td>${t.timeframe || "—"}</td>
        <td>${categoryChip(t.category)}</td>
        <td>${sideChip(t.side)}</td>
        <td class="num">${t.lots != null ? t.lots : "—"}</td>
        <td class="num">${_fmtPrice(t.symbol, t.entry)}</td>
        <td class="num" style="color:var(--red);">${_fmtPrice(t.symbol, t.sl)}</td>
        <td class="num" style="color:var(--green);">${_fmtPrice(t.symbol, t.tp)}</td>
        <td class="num ${t.rr >= 2 ? "pos" : t.rr < 0 ? "neg" : ""}">${(t.rr || 0).toFixed(2)}</td>
        <td class="num">${_pipsCell(t.symbol, t.side, t.entry, t.exit)}</td>
        <td class="num ${t.pnl > 0 ? "pos" : t.pnl < 0 ? "neg" : ""}">${fmtMoney(t.pnl, ccy)}</td>
        <td>${closeReasonBadge(t.closeReason)}</td>
        <td class="num">${adherencePill(t.adherence)}</td>
        <td>${t._rawIdx != null
          ? `<button class="btn secondary" style="font-size:11px;padding:3px 10px;" title="Candle chart with entry / SL / TP / exits" onclick="openClosedTradeChart(${t._rawIdx})">📈</button>`
          : "—"}</td>
      </tr>
    `).join("");

    // sort headers
    document.querySelectorAll("#trades-table th[data-sort]").forEach(th => {
      th.onclick = () => {
        const k = th.dataset.sort;
        if (state.sort.key === k) state.sort.dir = state.sort.dir === "asc" ? "desc" : "asc";
        else { state.sort.key = k; state.sort.dir = "asc"; }
        renderTradesTab();
      };
      th.classList.remove("sort-asc", "sort-desc");
      if (th.dataset.sort === state.sort.key) th.classList.add(state.sort.dir === "asc" ? "sort-asc" : "sort-desc");
    });
  }

  function applyFiltersAndSort(trades) {
    const f = state.filters;
    const out = trades.filter(t => {
      if (f.symbol && t.symbol !== f.symbol) return false;
      if (f.setup && t.setup !== f.setup) return false;
      if (f.category && t.category !== f.category) return false;
      if (f.outcome) {
        if (f.outcome === "win" && t.pnl <= 0) return false;
        if (f.outcome === "loss" && t.pnl >= 0) return false;
        if (f.outcome === "be" && t.pnl !== 0) return false;
      }
      if (f.search) {
        const q = f.search.toLowerCase();
        if (!(t.comment || "").toLowerCase().includes(q) && !t.symbol.toLowerCase().includes(q)) return false;
      }
      return true;
    });

    const { key, dir } = state.sort;
    out.sort((a, b) => {
      let av = a[key]; let bv = b[key];
      if (key === "adherence") { av = (a.adherence && a.adherence.score) || 0; bv = (b.adherence && b.adherence.score) || 0; }
      if (key === "closeTime" || key === "openTime") { av = new Date(av).getTime(); bv = new Date(bv).getTime(); }
      if (typeof av === "string") { return dir === "asc" ? av.localeCompare(bv) : bv.localeCompare(av); }
      return dir === "asc" ? (av - bv) : (bv - av);
    });
    return out;
  }

  function populateFilters() {
    const activeTrades = getActiveTrades();
    const symbols = [...new Set(activeTrades.map(t => t.symbol))].sort();
    const setups  = [...new Set(activeTrades.map(t => t.setup).filter(Boolean))].sort();
    const sel = (id, items) => {
      const el = document.getElementById(id);
      const cur = el.value;
      el.innerHTML = `<option value="">All</option>` + items.map(s => `<option value="${s}">${s}</option>`).join("");
      el.value = cur;
      el.onchange = () => { state.filters[id.replace("filter-", "")] = el.value; renderTradesTab(); };
    };
    sel("filter-symbol", symbols);
    sel("filter-setup", setups);
    document.getElementById("filter-category").onchange = (e) => { state.filters.category = e.target.value; renderTradesTab(); };
    document.getElementById("filter-outcome").onchange  = (e) => { state.filters.outcome  = e.target.value; renderTradesTab(); };
    document.getElementById("filter-search").oninput    = (e) => { state.filters.search   = e.target.value; renderTradesTab(); };
  }

  // -------- Charts --------
  function renderCharts() {
    // Equity curve
    drawChart("chart-equity", "line", buildEquity(), {
      plugins: { legend: { display: false } },
      scales: {
        x: { grid: { display: false }, ticks: { maxTicksLimit: 8 } },
        y: { grid: { color: "#1f2a44" }, ticks: { callback: v => curSym() + v.toFixed(0) } },
      },
    });

    // Setup mix (doughnut)
    drawChart("chart-setup-mix", "doughnut", buildSetupMix(), {
      plugins: { legend: { position: "bottom", labels: { padding: 12, boxWidth: 12 } } },
      cutout: "60%",
    });

    // Win rate by week
    drawChart("chart-winrate", "bar", buildWinRateWeekly(), {
      plugins: { legend: { display: false } },
      scales: { y: { min: 0, max: 100, grid: { color: "#1f2a44" }, ticks: { callback: v => v + "%" } } },
    });

    // RR distribution
    drawChart("chart-rr-dist", "bar", buildRRHistogram(), {
      plugins: { legend: { display: false } },
      scales: { x: { grid: { display: false } }, y: { grid: { color: "#1f2a44" } } },
    });

    drawChart("chart-pnl-symbol", "bar", buildPnlBy("symbol"), {
      indexAxis: "y",
      plugins: { legend: { display: false } },
      scales: { x: { grid: { color: "#1f2a44" }, ticks: { callback: v => curSym() + v } } },
    });

    drawChart("chart-pnl-setup", "bar", buildPnlBy("setup"), {
      indexAxis: "y",
      plugins: { legend: { display: false } },
      scales: { x: { grid: { color: "#1f2a44" }, ticks: { callback: v => curSym() + v } } },
    });

    drawChart("chart-category", "bar", buildCategoryPerf(), {
      plugins: { legend: { position: "bottom" } },
      scales: { y: { grid: { color: "#1f2a44" } } },
    });

    drawChart("chart-hour", "bar", buildHourPerf(), {
      plugins: { legend: { display: false } },
      scales: { y: { min: 0, max: 100, grid: { color: "#1f2a44" }, ticks: { callback: v => v + "%" } } },
    });

    drawChart("chart-broken", "bar", buildBrokenRules(), {
      indexAxis: "y",
      plugins: { legend: { display: false } },
      scales: { x: { grid: { color: "#1f2a44" }, beginAtZero: true } },
    });
  }

  function drawChart(canvasId, type, data, options) {
    const c = document.getElementById(canvasId);
    if (!c) return;
    if (state.charts[canvasId]) state.charts[canvasId].destroy();
    state.charts[canvasId] = new Chart(c, {
      type, data,
      options: {
        responsive: true,
        maintainAspectRatio: false,
        ...options,
      },
    });
  }

  // -------- Chart data builders --------
  function buildEquity() {
    const sorted = [...getActiveTrades()].sort((a, b) => new Date(a.closeTime) - new Date(b.closeTime));
    let cum = 0;
    const labels = [], data = [];
    sorted.forEach((t, i) => {
      cum += t.pnl;
      labels.push("#" + (i + 1));
      data.push(+cum.toFixed(2));
    });
    return {
      labels,
      datasets: [{
        label: "Cumulative P&L",
        data,
        borderColor: "#3b82f6",
        backgroundColor: "rgba(59, 130, 246, 0.15)",
        fill: true,
        tension: 0.25,
        pointRadius: 0,
        pointHoverRadius: 4,
        borderWidth: 2,
      }],
    };
  }

  function buildSetupMix() {
    const counts = {};
    getActiveTrades().forEach(t => { if (t.setup) counts[t.setup] = (counts[t.setup] || 0) + 1; });
    const labels = Object.keys(counts);
    const data   = labels.map(l => counts[l]);
    const palette = ["#3b82f6", "#10b981", "#ef4444", "#f59e0b", "#a855f7", "#06b6d4", "#ec4899", "#84cc16"];
    return {
      labels,
      datasets: [{ data, backgroundColor: labels.map((_, i) => palette[i % palette.length]), borderColor: "#131927", borderWidth: 2 }],
    };
  }

  function buildWinRateWeekly() {
    const buckets = {};
    getActiveTrades().forEach(t => {
      const d = new Date(t.closeTime);
      const week = startOfWeekKey(d);
      if (!buckets[week]) buckets[week] = { wins: 0, total: 0 };
      buckets[week].total++;
      if (t.pnl > 0) buckets[week].wins++;
    });
    const weeks = Object.keys(buckets).sort();
    const data = weeks.map(w => +(buckets[w].wins / buckets[w].total * 100).toFixed(1));
    return {
      labels: weeks.map(w => "W " + w.slice(5)),
      datasets: [{
        label: "Win rate %",
        data,
        backgroundColor: data.map(v => v >= 70 ? "rgba(16, 185, 129, 0.6)" : v >= 50 ? "rgba(251, 191, 36, 0.6)" : "rgba(239, 68, 68, 0.6)"),
        borderColor: data.map(v => v >= 70 ? "#10b981" : v >= 50 ? "#fbbf24" : "#ef4444"),
        borderWidth: 1,
        borderRadius: 4,
      }],
    };
  }

  function buildRRHistogram() {
    const bins = { "Loss (−1R)": 0, "0–1R": 0, "1–2R": 0, "2–3R": 0, "3–4R": 0, "4–5R": 0, "5R+": 0 };
    getActiveTrades().forEach(t => {
      const r = t.rr;
      if (r < 0) bins["Loss (−1R)"]++;
      else if (r < 1) bins["0–1R"]++;
      else if (r < 2) bins["1–2R"]++;
      else if (r < 3) bins["2–3R"]++;
      else if (r < 4) bins["3–4R"]++;
      else if (r < 5) bins["4–5R"]++;
      else bins["5R+"]++;
    });
    const labels = Object.keys(bins);
    return {
      labels,
      datasets: [{
        label: "Count",
        data: labels.map(l => bins[l]),
        backgroundColor: labels.map(l => l.startsWith("Loss") ? "rgba(239, 68, 68, 0.6)"
                                       : (l === "0–1R" ? "rgba(251, 191, 36, 0.6)" : "rgba(16, 185, 129, 0.6)")),
        borderRadius: 4,
      }],
    };
  }

  function buildPnlBy(key) {
    const map = {};
    getActiveTrades().forEach(t => { if (t[key]) map[t[key]] = (map[t[key]] || 0) + t.pnl; });
    const sorted = Object.entries(map).sort((a, b) => b[1] - a[1]);
    return {
      labels: sorted.map(e => e[0]),
      datasets: [{
        label: "P&L",
        data: sorted.map(e => +e[1].toFixed(2)),
        backgroundColor: sorted.map(e => e[1] >= 0 ? "rgba(16, 185, 129, 0.6)" : "rgba(239, 68, 68, 0.6)"),
        borderColor: sorted.map(e => e[1] >= 0 ? "#10b981" : "#ef4444"),
        borderWidth: 1,
        borderRadius: 4,
      }],
    };
  }

  function buildCategoryPerf() {
    const cats = ["RED_FLAG", "LIVE", "TELEGRAM"];
    const active = getActiveTrades();
    const wr = cats.map(c => {
      const xs = active.filter(t => t.category === c);
      return xs.length ? +(xs.filter(t => t.pnl > 0).length / xs.length * 100).toFixed(1) : 0;
    });
    const cnt = cats.map(c => active.filter(t => t.category === c).length);
    return {
      labels: cats.map(c => c.replace("_", " ")),
      datasets: [
        { label: "Win rate %", data: wr, backgroundColor: "rgba(59, 130, 246, 0.6)", borderColor: "#3b82f6", borderWidth: 1, borderRadius: 4, yAxisID: "y" },
        { label: "Trade count", data: cnt, backgroundColor: "rgba(168, 85, 247, 0.4)", borderColor: "#a855f7", borderWidth: 1, borderRadius: 4, yAxisID: "y" },
      ],
    };
  }

  function buildHourPerf() {
    const hours = Array.from({ length: 24 }, (_, h) => h);
    const active = getActiveTrades();
    const data = hours.map(h => {
      const xs = active.filter(t => new Date(t.openTime).getUTCHours() === h);
      return xs.length ? +(xs.filter(t => t.pnl > 0).length / xs.length * 100).toFixed(1) : 0;
    });
    return {
      labels: hours.map(h => String(h).padStart(2, "0") + "h"),
      datasets: [{
        label: "Win rate %",
        data,
        backgroundColor: data.map(v => v >= 70 ? "rgba(16, 185, 129, 0.6)" : v >= 50 ? "rgba(251, 191, 36, 0.6)" : "rgba(239, 68, 68, 0.6)"),
        borderRadius: 4,
      }],
    };
  }

  function buildBrokenRules() {
    const recent = [...getActiveTrades()].sort((a, b) => new Date(b.closeTime) - new Date(a.closeTime)).slice(0, 50);
    const counts = {};
    recent.forEach(t => (t.adherence?.broken || []).forEach(r => counts[r] = (counts[r] || 0) + 1));
    const sorted = Object.entries(counts).sort((a, b) => b[1] - a[1]);
    return {
      labels: sorted.map(e => e[0]),
      datasets: [{
        label: "Broken",
        data: sorted.map(e => e[1]),
        backgroundColor: "rgba(239, 68, 68, 0.6)",
        borderColor: "#ef4444",
        borderWidth: 1,
        borderRadius: 4,
      }],
    };
  }

  // -------- Adherence section --------
  function renderAdherence() {
    const list = document.getElementById("adherence-list");
    const activeTrades = getActiveTrades();
    const rules = [
      "Momentum into zone",
      "Candle body ≥ 70%",
      "Opposing wick ≤ 30%",
      "Setup tagged in comment",
      "Direction matches setup",
      "TF ≥ M15",
      "Min 1:2 R:R",
      "1% risk sizing",
      "No news in 2h window",
    ];
    const html = rules.map(rule => {
      const total = activeTrades.length;
      const broken = activeTrades.filter(t => (t.adherence?.broken || []).includes(rule)).length;
      const pct = total ? Math.round(((total - broken) / total) * 100) : 0;
      return `
        <div class="adherence-row">
          <div class="name">${rule}</div>
          <div class="bar"><div class="fill" style="width:${pct}%"></div></div>
          <div class="pct">${pct}%</div>
        </div>`;
    }).join("");
    list.innerHTML = html;

    // Violations table
    const viol = activeTrades.filter(t => (t.adherence?.broken || []).length > 0)
                         .sort((a, b) => new Date(b.closeTime) - new Date(a.closeTime))
                         .slice(0, 25);
    const tbody = document.querySelector("#violations-table tbody");
    tbody.innerHTML = viol.map(t => `
      <tr>
        <td>${fmtDate(t.closeTime)}</td>
        <td><strong>${t.symbol}</strong></td>
        <td><span class="chip">${t.setup}</span></td>
        <td>${sideChip(t.side)}</td>
        <td class="num ${t.pnl >= 0 ? "pos" : "neg"}">${fmtMoney(t.pnl, D.account.currency || "USD")}</td>
        <td>${(t.adherence?.broken || []).map(r => `<span class="chip red">${r}</span>`).join(" ")}</td>
      </tr>
    `).join("");
    if (!viol.length) tbody.innerHTML = `<tr><td colspan="6" class="hint" style="text-align:center;">No rule violations — keep it up.</td></tr>`;
  }

  // ============================================================================
  // CHECKLIST INTERACTIVITY
  // ============================================================================
  function initChecklist() {
    const list = document.getElementById("pretrade-checklist");
    if (!list) return;
    list.addEventListener("change", updateChecklistResult);
    updateChecklistResult();
  }
  function updateChecklistResult() {
    const required = document.querySelectorAll("#pretrade-checklist input[data-required='true']");
    const optional = document.querySelectorAll("#pretrade-checklist input:not([data-required])");
    const reqDone = [...required].filter(x => x.checked).length;
    const optDone = [...optional].filter(x => x.checked).length;
    const out = document.getElementById("checklist-result");
    out.classList.remove("go", "no-go");
    if (reqDone === required.length) {
      out.classList.add("go");
      out.textContent = `✅ All ${required.length} required items met (${optDone}/${optional.length} optional) — trade qualifies`;
    } else {
      out.classList.add("no-go");
      out.textContent = `⛔ ${required.length - reqDone} required item${required.length - reqDone === 1 ? "" : "s"} missing — do not enter`;
    }
  }
  window.resetChecklist = function () {
    document.querySelectorAll("#pretrade-checklist input").forEach(c => c.checked = false);
    updateChecklistResult();
  };

  // ============================================================================
  // FILE LOADER (drag-drop trades.json)
  // ============================================================================
  function initFileLoader() {
    const dz = document.getElementById("dropzone");
    const fi = document.getElementById("file-input");
    if (!dz || !fi) return;

    dz.addEventListener("dragover", (e) => { e.preventDefault(); dz.classList.add("dragover"); });
    dz.addEventListener("dragleave", () => dz.classList.remove("dragover"));
    dz.addEventListener("drop", (e) => {
      e.preventDefault();
      dz.classList.remove("dragover");
      if (e.dataTransfer.files.length) handleFile(e.dataTransfer.files[0]);
    });
    fi.addEventListener("change", (e) => {
      if (e.target.files.length) handleFile(e.target.files[0]);
    });
  }

  function handleFile(file) {
    const msg = document.getElementById("upload-msg");
    const reader = new FileReader();
    const name = (file.name || "").toLowerCase();

    reader.onload = (e) => {
      try {
        let parsed;
        if (name.endsWith(".json")) {
          parsed = JSON.parse(e.target.result);
          if (!parsed.trades || !Array.isArray(parsed.trades)) throw new Error("missing 'trades' array");
        } else if (name.endsWith(".html") || name.endsWith(".htm")) {
          parsed = parseMT5HtmlReport(e.target.result);
        } else if (name.endsWith(".csv")) {
          parsed = parseMT5CsvReport(e.target.result);
        } else {
          // Best-effort sniff: looks like HTML?
          const txt = (e.target.result || "").trim();
          if (txt.startsWith("<")) parsed = parseMT5HtmlReport(txt);
          else if (txt.startsWith("{") || txt.startsWith("[")) parsed = JSON.parse(txt);
          else parsed = parseMT5CsvReport(txt);
        }

        if (!parsed.trades || parsed.trades.length === 0) {
          throw new Error("No closed trades found in the file");
        }
        if (!parsed.account) parsed.account = { currency: "USD", balance: 0, broker: "" };
        parsed.isMockData = false;

        // Persist across reloads so user doesn't have to re-import every time
        try { localStorage.setItem("tradeData", JSON.stringify(parsed)); } catch (_) {}
        window.tradeData = parsed;

        msg.style.color = "var(--green)";
        msg.textContent = `Loaded ${parsed.trades.length} trades. Reloading…`;
        setTimeout(() => location.reload(), 400);
      } catch (err) {
        msg.style.color = "var(--red)";
        msg.textContent = "Couldn't parse: " + err.message;
        console.error(err);
      }
    };
    reader.readAsText(file);
  }

  // ============================================================================
  // MT5 REPORT PARSERS (HTML + CSV)
  // ============================================================================

  function parseMT5HtmlReport(htmlString) {
    const doc = new DOMParser().parseFromString(htmlString, "text/html");
    const account = extractAccountInfo(doc, htmlString);
    const tables = Array.from(doc.querySelectorAll("table"));

    // Locate the table that contains the closed-positions rows.
    // MT5 emits a section header (often <th> spanning the whole table)
    // with text like "Positions" or "Closed Transactions" right above
    // the data rows. We scan all tables and pick the best match.
    let bestRows = [];
    for (const table of tables) {
      const rows = Array.from(table.querySelectorAll("tr"));
      const headerIdx = rows.findIndex(r => {
        const t = (r.textContent || "").trim().toLowerCase();
        return /position|deal|trade/.test(t) && t.length < 80;
      });
      if (headerIdx < 0) continue;

      // The column-header row is usually the next one with multiple cells
      let colHeaderIdx = -1;
      for (let i = headerIdx + 1; i < Math.min(headerIdx + 4, rows.length); i++) {
        const cellCount = rows[i].querySelectorAll("th, td").length;
        if (cellCount >= 8) { colHeaderIdx = i; break; }
      }
      if (colHeaderIdx < 0) continue;

      const colMap = mapColumns(rows[colHeaderIdx]);
      if (colMap.symbol === undefined || colMap.type === undefined) continue;

      const dataRows = [];
      for (let i = colHeaderIdx + 1; i < rows.length; i++) {
        const cells = rows[i].querySelectorAll("td");
        if (cells.length < 8) break;        // section ended
        // Skip total / summary rows (text in first cell instead of a number)
        const txt0 = cells[0].textContent.trim();
        if (!txt0 || /^total|^balance|^summary|^profit/i.test(txt0)) continue;
        dataRows.push({ cells, colMap });
      }
      if (dataRows.length > bestRows.length) bestRows = dataRows;
    }

    const trades = bestRows.map(r => rowToTrade(r.cells, r.colMap)).filter(Boolean);

    return {
      account,
      generatedAt: new Date().toISOString(),
      isMockData: false,
      trades,
      openPositions: [],
      _source: "MT5 HTML report",
    };
  }

  function mapColumns(headerRow) {
    // Returns { time, ticket, symbol, type, volume, price, sl, tp, closeTime, closePrice, commission, swap, profit, comment }
    const headers = Array.from(headerRow.querySelectorAll("th, td")).map(c => c.textContent.trim().toLowerCase());
    const out = {};
    headers.forEach((h, i) => {
      if (out.time === undefined && /^time$/.test(h)) { out.time = i; return; }
      if (out.closeTime === undefined && out.time !== undefined && /time/.test(h)) { out.closeTime = i; return; }
      if (/^position$|^ticket$|^order$|^deal$/.test(h)) out.ticket = i;
      else if (/^symbol$/.test(h)) out.symbol = i;
      else if (/^type$/.test(h)) out.type = i;
      else if (/^volume$|^size$|^lots$/.test(h)) out.volume = i;
      else if (/^s\s*\/\s*l$|stop/i.test(h)) out.sl = i;
      else if (/^t\s*\/\s*p$|take/i.test(h)) out.tp = i;
      else if (/^commission$/.test(h)) out.commission = i;
      else if (/^swap$|^rollover$/.test(h)) out.swap = i;
      else if (/^profit$|^p\s*&\s*l$|^p\/l$/.test(h)) out.profit = i;
      else if (/^comment$|^note$/.test(h)) out.comment = i;
      else if (/^price$/.test(h)) {
        if (out.price === undefined) out.price = i;
        else if (out.closePrice === undefined) out.closePrice = i;
      }
    });
    return out;
  }

  function rowToTrade(cells, m) {
    const txt = i => i !== undefined && cells[i] ? cells[i].textContent.trim() : "";
    const num = i => {
      const s = txt(i).replace(/[\s,]/g, "").replace(/[^\d.\-]/g, "");
      const n = parseFloat(s);
      return isNaN(n) ? 0 : n;
    };

    const type = txt(m.type).toLowerCase();
    const side = type.includes("sell") || type.includes("short") ? "sell" : "buy";

    const entry = num(m.price);
    const close = num(m.closePrice);
    const sl    = num(m.sl);
    const tp    = num(m.tp);
    const profit = num(m.profit) + num(m.commission) + num(m.swap);

    const comment = txt(m.comment);
    const tag = parseSetupTag(comment);

    // Achieved R = move / risk-per-unit (negative if it went against you)
    let achievedR = 0;
    if (sl && entry && sl !== entry) {
      const risk = Math.abs(entry - sl);
      const move = Math.abs(close - entry);
      const sign = (side === "buy" && close >= entry) || (side === "sell" && close <= entry) ? 1 : -1;
      achievedR = sign * (move / risk);
    } else if (profit !== 0) {
      // No SL info? Fall back to sign of P&L
      achievedR = profit > 0 ? 1 : -1;
    }

    const trade = {
      ticket: parseInt(txt(m.ticket).replace(/\D/g, ""), 10) || 0,
      symbol: txt(m.symbol).toUpperCase(),
      setup: tag.setup,
      timeframe: tag.timeframe,
      category: tag.category,
      side,
      entry,
      sl,
      tp,
      openTime: parseMT5Date(txt(m.time)),
      closeTime: parseMT5Date(txt(m.closeTime)),
      pnl: +profit.toFixed(2),
      rr: +achievedR.toFixed(2),
      comment,
    };
    trade.adherence = scoreAdherence(trade);
    return trade;
  }

  function parseMT5CsvReport(csvString) {
    // Detect delimiter: MT5 uses tab in "Open XML" and comma in CSV
    const firstLine = csvString.split(/\r?\n/)[0] || "";
    const delim = firstLine.includes("\t") ? "\t" : ",";
    const lines = csvString.split(/\r?\n/).filter(l => l.trim().length > 0);

    // Find the row that looks like a column header for closed positions
    let headerIdx = -1;
    for (let i = 0; i < lines.length; i++) {
      const low = lines[i].toLowerCase();
      if (low.includes("symbol") && low.includes("profit") && (low.includes("time") || low.includes("price"))) {
        headerIdx = i; break;
      }
    }
    if (headerIdx < 0) throw new Error("Could not locate the closed-positions header row in the CSV");

    const cols = splitCsvLine(lines[headerIdx], delim).map(c => c.toLowerCase().trim());
    const colMap = {};
    cols.forEach((h, i) => {
      if (colMap.time === undefined && /^time$/.test(h)) { colMap.time = i; return; }
      if (colMap.closeTime === undefined && colMap.time !== undefined && /time/.test(h)) { colMap.closeTime = i; return; }
      if (/position|ticket|order|deal/.test(h)) colMap.ticket = i;
      else if (h === "symbol") colMap.symbol = i;
      else if (h === "type") colMap.type = i;
      else if (/volume|size|lots/.test(h)) colMap.volume = i;
      else if (/s\s*\/\s*l|stop/.test(h)) colMap.sl = i;
      else if (/t\s*\/\s*p|take/.test(h)) colMap.tp = i;
      else if (h === "commission") colMap.commission = i;
      else if (/swap|rollover/.test(h)) colMap.swap = i;
      else if (/profit|p\s*&\s*l|p\/l/.test(h)) colMap.profit = i;
      else if (/comment|note/.test(h)) colMap.comment = i;
      else if (h === "price") {
        if (colMap.price === undefined) colMap.price = i;
        else if (colMap.closePrice === undefined) colMap.closePrice = i;
      }
    });

    const trades = [];
    for (let i = headerIdx + 1; i < lines.length; i++) {
      const cells = splitCsvLine(lines[i], delim);
      if (cells.length < 6) break;
      // Build a pseudo-cells array compatible with rowToTrade
      const pseudo = cells.map(t => ({ textContent: t }));
      const trade = rowToTrade(pseudo, colMap);
      if (trade && trade.symbol) trades.push(trade);
    }

    return {
      account: { currency: "USD", balance: 0, broker: "(from CSV)" },
      generatedAt: new Date().toISOString(),
      isMockData: false,
      trades,
      openPositions: [],
      _source: "MT5 CSV report",
    };
  }

  function splitCsvLine(line, delim) {
    const out = [];
    let cur = "";
    let inQ = false;
    for (let i = 0; i < line.length; i++) {
      const c = line[i];
      if (c === '"') { inQ = !inQ; continue; }
      if (c === delim && !inQ) { out.push(cur); cur = ""; continue; }
      cur += c;
    }
    out.push(cur);
    return out.map(s => s.trim());
  }

  function parseMT5Date(s) {
    // MT5 formats: "2026.05.22 14:30:00" or "2026-05-22 14:30:00"
    if (!s) return new Date(0).toISOString();
    const m = s.match(/(\d{4})[.\-\/](\d{1,2})[.\-\/](\d{1,2})\s+(\d{1,2}):(\d{2})(?::(\d{2}))?/);
    if (!m) {
      const d = new Date(s);
      return isNaN(d.getTime()) ? new Date(0).toISOString() : d.toISOString();
    }
    const iso = `${m[1]}-${m[2].padStart(2, "0")}-${m[3].padStart(2, "0")}T${m[4].padStart(2, "0")}:${m[5]}:${m[6] || "00"}Z`;
    const d = new Date(iso);
    return isNaN(d.getTime()) ? new Date(0).toISOString() : d.toISOString();
  }

  function extractAccountInfo(doc, raw) {
    // MT5 reports start with lines like:
    //   Account: 12345678 (USD, ICMarkets-Live, ...) - Demo / Real
    //   Name: ...
    //   Currency: USD
    //   Balance: 10250.50
    const text = (doc.body && doc.body.textContent) || raw || "";
    const out = { currency: "USD", balance: 0, broker: "" };

    const ccyMatch = text.match(/(?:Currency|Deposit Currency)\s*[:\s]\s*([A-Z]{3})/i);
    if (ccyMatch) out.currency = ccyMatch[1];

    const balMatch = text.match(/(?:Balance|Closing\s*balance)\s*[:\s]\s*([\d,]+\.?\d*)/i);
    if (balMatch) out.balance = parseFloat(balMatch[1].replace(/,/g, ""));

    const acctMatch = text.match(/Account\s*[:\s]\s*(\d+)/i);
    if (acctMatch) out.login = parseInt(acctMatch[1], 10);

    const brokerMatch = text.match(/(?:Broker|Company)\s*[:\s]\s*([^\n]+)/i);
    if (brokerMatch) out.broker = brokerMatch[1].trim().slice(0, 60);

    return out;
  }

  function parseSetupTag(comment) {
    const out = { setup: null, timeframe: null, category: "LIVE" };
    if (!comment) return out;
    const m = comment.trim().match(/^([A-Za-z]{1,3})[-_]\s*(M\d{1,2}|H\d|D\d)\s*(?:\|(.*))?$/i);
    if (m) {
      const setup = m[1].toUpperCase();
      const validSetups = new Set(["DT", "DB", "HS", "IHS", "CHS", "TT", "TB", "TRI", "REC", "TL", "FK"]);
      if (validSetups.has(setup)) out.setup = setup;
      out.timeframe = m[2].toUpperCase();
      const note = (m[3] || "").toLowerCase();
      if (note.includes("tg") || note.includes("telegram")) out.category = "TELEGRAM";
      else if (note.includes("rf") || note.includes("red")) out.category = "RED_FLAG";
    }
    return out;
  }

  function scoreAdherence(t) {
    const broken = [];
    const longs = new Set(["DB", "IHS", "TB"]);
    const shorts = new Set(["DT", "HS", "TT", "CHS"]);
    if (t.setup && longs.has(t.setup) && t.side !== "buy") broken.push("Direction matches setup");
    if (t.setup && shorts.has(t.setup) && t.side !== "sell") broken.push("Direction matches setup");
    if (!t.setup) broken.push("Setup tagged in comment");
    if (t.timeframe === "M1" || t.timeframe === "M5") broken.push("TF ≥ M15");
    if (t.sl && t.tp && t.entry) {
      const risk = Math.abs(t.entry - t.sl);
      const reward = Math.abs(t.tp - t.entry);
      if (risk > 0 && reward / risk < 1.99) broken.push("Min 1:2 R:R");
    }
    return { score: Math.max(0, 100 - broken.length * 15), broken };
  }

  // ============================================================================
  // HELPERS
  // ============================================================================
  // Currency symbol for a code (defaults to "CODE " for anything unmapped).
  function currencySymbol(ccy) {
    return ({ USD: "$", EUR: "€", GBP: "£", INR: "₹", JPY: "¥", AUD: "A$",
              CAD: "C$", CHF: "CHF ", NZD: "NZ$" })[ccy] || (ccy ? ccy + " " : "$");
  }
  // The account's current currency symbol (driven by the live MT5 account).
  function curSym() {
    return currencySymbol((D && D.account && D.account.currency) || "USD");
  }
  function fmtMoney(v, ccy) {
    const sign = v < 0 ? "−" : "";
    const symbol = currencySymbol(ccy);
    return sign + symbol + Math.abs(v).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }
  function fmtDate(iso) {
    if (!iso) return "—";
    const d = new Date(iso);
    return d.toLocaleDateString("en-GB", { day: "2-digit", month: "short" }) + " " +
           d.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" });
  }
  function formatRelativeTime(iso) {
    if (!iso) return "—";
    const diff = (Date.now() - new Date(iso).getTime()) / 1000;
    if (diff < 60) return "just now";
    if (diff < 3600) return Math.floor(diff / 60) + "m ago";
    if (diff < 86400) return Math.floor(diff / 3600) + "h ago";
    return Math.floor(diff / 86400) + "d ago";
  }
  function sideChip(side) {
    return side === "buy"
      ? `<span class="chip green">BUY</span>`
      : `<span class="chip red">SELL</span>`;
  }
  function categoryChip(cat) {
    if (!cat) return "—";
    const map = { RED_FLAG: "red", LIVE: "blue", TELEGRAM: "green" };
    const label = { RED_FLAG: "RED FLAG", LIVE: "LIVE", TELEGRAM: "TG" };
    return `<span class="chip ${map[cat] || ""}">${label[cat] || cat}</span>`;
  }
  function adherencePill(a) {
    if (!a || a.score == null) return `<span class="chip">—</span>`;
    const s = a.score;
    const cls = s >= 90 ? "green" : s >= 75 ? "yellow" : "red";
    return `<span class="chip ${cls}">${s}%</span>`;
  }
  function startOfWeekKey(d) {
    const day = d.getUTCDay();
    const diff = (day + 6) % 7; // Monday=0
    const monday = new Date(d);
    monday.setUTCDate(d.getUTCDate() - diff);
    return monday.toISOString().slice(0, 10);
  }

  // ============================================================================
  // PAIRS MANAGER
  // ============================================================================
  function initPairsManager() {
    renderPairsUI();
  }

  function renderPairsUI() {
    const container = document.getElementById("pairs-container");
    if (!container) return;

    container.innerHTML = PAIRS_CATALOG.map(({ group, pairs }) => `
      <div class="pairs-group">
        <div class="pairs-group-header">
          <h3>${group}</h3>
          <div class="pairs-group-actions">
            <button onclick="pairsGroupEnable(${JSON.stringify(pairs)})">Enable all</button>
            <button onclick="pairsGroupDisable(${JSON.stringify(pairs)})">Disable all</button>
          </div>
        </div>
        <div class="pairs-grid">
          ${pairs.map(p => `
            <label class="pair-toggle ${enabledPairs.has(p) ? "enabled" : ""}" id="pt-${p}">
              <span class="pair-name">${p}</span>
              <span class="toggle-switch">
                <input type="checkbox" ${enabledPairs.has(p) ? "checked" : ""}
                  onchange="pairsToggle('${p}', this.checked)">
                <span class="toggle-slider"></span>
              </span>
            </label>
          `).join("")}
        </div>
      </div>
    `).join("");

    updatePairsSummary();
  }

  function updatePairsSummary() {
    const el = document.getElementById("pairs-enabled-count");
    if (el) el.textContent = enabledPairs.size;
  }

  window.pairsToggle = function(pair, checked) {
    if (checked) enabledPairs.add(pair); else enabledPairs.delete(pair);
    saveEnabledPairs(enabledPairs);
    // Update label class
    const label = document.getElementById("pt-" + pair);
    if (label) label.classList.toggle("enabled", checked);
    updatePairsSummary();
    if (D) renderAll();
  };

  window.pairsGroupEnable = function(pairs) {
    pairs.forEach(p => enabledPairs.add(p));
    saveEnabledPairs(enabledPairs);
    renderPairsUI();
    if (D) renderAll();
  };

  window.pairsGroupDisable = function(pairs) {
    pairs.forEach(p => enabledPairs.delete(p));
    saveEnabledPairs(enabledPairs);
    renderPairsUI();
    if (D) renderAll();
  };

  window.pairsEnableAll = function() {
    ALL_PAIRS.forEach(p => enabledPairs.add(p));
    saveEnabledPairs(enabledPairs);
    renderPairsUI();
    if (D) renderAll();
  };

  window.pairsDisableAll = function() {
    enabledPairs.clear();
    saveEnabledPairs(enabledPairs);
    renderPairsUI();
    if (D) renderAll();
  };

  // ============================================================================
  // PATTERNS MANAGER
  // ============================================================================

  // Full catalog of all supported patterns
  const PATTERNS_CATALOG = [
    {
      key: "double_top",
      label: "Double Top",
      desc: "Two peaks at the same resistance level — bearish reversal sell signal.",
      direction: "sell",
      available: true,   // implemented and stable
    },
    {
      key: "double_bottom",
      label: "Double Bottom",
      desc: "Two troughs at the same support level — bullish reversal buy signal.",
      direction: "buy",
      available: true,
    },
    {
      key: "head_shoulders",
      label: "Head & Shoulders",
      desc: "Three peaks with the middle highest — bearish reversal sell signal.",
      direction: "sell",
      available: true,   // v2: coded, can be enabled
    },
    {
      key: "inverse_hs",
      label: "Inverse H&S",
      desc: "Three troughs with the middle deepest — bullish reversal buy signal.",
      direction: "buy",
      available: true,
    },
    {
      key: "triple_top",
      label: "Triple Top",
      desc: "Three peaks at the same resistance — strong bearish reversal.",
      direction: "sell",
      available: true,
    },
    {
      key: "triple_bottom",
      label: "Triple Bottom",
      desc: "Three troughs at the same support — strong bullish reversal.",
      direction: "buy",
      available: true,
    },
    {
      key: "rectangle",
      label: "Rectangle",
      desc: "Price consolidates between flat support & resistance — trades the breakout.",
      direction: "both",
      available: true,
    },
    {
      key: "trendline",
      label: "Trendline Break",
      desc: "Price breaks an established ascending or descending trendline.",
      direction: "both",
      available: true,
    },
  ];

  const ALL_PATTERN_KEYS = PATTERNS_CATALOG.map(p => p.key);

  // Default: only the two stable patterns on, rest off (mirrors config.yaml defaults)
  const DEFAULT_ENABLED_PATTERNS = new Set(["double_top", "double_bottom"]);

  function loadEnabledPatterns() {
    try {
      const raw = localStorage.getItem("enabledPatterns");
      if (raw) {
        const arr = JSON.parse(raw);
        if (Array.isArray(arr)) return new Set(arr);
      }
    } catch (_) {}
    return new Set(DEFAULT_ENABLED_PATTERNS);
  }

  function saveEnabledPatterns(set) {
    try { localStorage.setItem("enabledPatterns", JSON.stringify([...set])); } catch (_) {}
    syncPatternsToServer(set);
  }

  function syncPatternsToServer(set) {
    fetch("/api/patterns", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ enabled_patterns: [...set] }),
    }).catch(() => {});  // silent if server not running
  }

  let enabledPatterns = loadEnabledPatterns();
  // Sync to server on page load
  syncPatternsToServer(enabledPatterns);

  function initPatternsManager() {
    renderPatternsUI();
  }

  function renderPatternsUI() {
    const container = document.getElementById("patterns-container");
    if (!container) return;

    const badgeClass = { buy: "badge-buy", sell: "badge-sell", both: "badge-both" };
    const badgeLabel = { buy: "Buy", sell: "Sell", both: "Buy & Sell" };

    container.innerHTML = `
      <div class="patterns-grid">
        ${PATTERNS_CATALOG.map(p => {
          const on = enabledPatterns.has(p.key);
          return `
            <label class="pattern-card ${on ? "enabled" : ""}" id="pc-${p.key}">
              <div class="pattern-card-header">
                <span class="pattern-card-name">${p.label}</span>
                <span class="toggle-switch">
                  <input type="checkbox" ${on ? "checked" : ""}
                    onchange="patternToggle('${p.key}', this.checked)">
                  <span class="toggle-slider"></span>
                </span>
              </div>
              <div style="display:flex; align-items:center; gap:6px; margin-bottom:6px;">
                <span class="pattern-card-badge ${badgeClass[p.direction]}">${badgeLabel[p.direction]}</span>
              </div>
              <div class="pattern-card-desc">${p.desc}</div>
            </label>
          `;
        }).join("")}
      </div>
    `;

    updatePatternsSummary();
  }

  function updatePatternsSummary() {
    const countEl = document.getElementById("patterns-enabled-count");
    const namesEl = document.getElementById("patterns-active-names");
    if (countEl) countEl.textContent = enabledPatterns.size;
    if (namesEl) {
      const names = PATTERNS_CATALOG
        .filter(p => enabledPatterns.has(p.key))
        .map(p => p.label);
      namesEl.textContent = names.length ? names.join(", ") : "None — bot is not scanning any patterns";
    }
  }

  window.patternToggle = function(key, checked) {
    if (checked) enabledPatterns.add(key); else enabledPatterns.delete(key);
    saveEnabledPatterns(enabledPatterns);
    const card = document.getElementById("pc-" + key);
    if (card) card.classList.toggle("enabled", checked);
    updatePatternsSummary();
  };

  window.patternsEnableAll = function() {
    ALL_PATTERN_KEYS.forEach(k => enabledPatterns.add(k));
    saveEnabledPatterns(enabledPatterns);
    renderPatternsUI();
  };

  window.patternsDisableAll = function() {
    enabledPatterns.clear();
    saveEnabledPatterns(enabledPatterns);
    renderPatternsUI();
  };

  // ============================================================================
  // MONITOR TAB
  // ============================================================================

  // Short labels for pattern keys
  const PATTERN_SHORT = {
    double_top:    "DTop",
    double_bottom: "DBot",
    head_shoulders:"H&S",
    inverse_hs:    "IH&S",
    triple_top:    "TTop",
    triple_bottom: "TBot",
    rectangle:     "Rect",
    trendline:     "TLine",
  };

  // Setup code → friendly name (from bot signal.setup field)
  const SETUP_LABEL = {
    DT: "Double Top", DB: "Double Bottom",
    HS: "H&S", IHS: "Inv H&S",
    TT: "Triple Top", TB: "Triple Bottom",
    RECT: "Rectangle", TL: "Trendline",
  };

  // Last bot snapshot stored here so monitor can read it
  let _lastSnap = null;

  // Pipeline counters (session)
  const PIP = { detected: 0, confirmed: 0, news: 0, risk: 0, filled: 0, closed: 0 };

  // Per-pair last signal: { pair → { time, setup, event, stage, detail } }
  const _lastSigPerPair = {};

  function _updatePipelineFromSignal(msg) {
    const p   = msg.payload || {};
    const sig = p.signal || p;
    const pair = (sig.symbol || "").toUpperCase().replace(/[._-].*$/, "");

    // Record per-pair last signal
    if (pair) {
      _lastSigPerPair[pair] = {
        ts:    msg.ts,
        setup: sig.setup || "",
        tf:    sig.timeframe || "",
        event: msg.event,
        stage: p.stage || "",
        pnl:   p.pnl,
        rr:    p.rr,
      };
    }

    // Update pipeline counters
    if (msg.event === "signal:rejected") {
      PIP.detected++;
      if (p.stage === "news")  PIP.confirmed++;
      if (p.stage === "risk")  { PIP.confirmed++; PIP.news++; }
    } else if (msg.event === "signal:accepted") {
      PIP.detected++; PIP.confirmed++; PIP.news++; PIP.risk++;
    } else if (msg.event === "order:filled") {
      PIP.detected++; PIP.confirmed++; PIP.news++; PIP.risk++; PIP.filled++;
    } else if (msg.event === "position:closed") {
      PIP.closed++;
    }
  }

  function renderMonitorTab() {
    if (!document.getElementById("tab-monitor")) return;
    _renderMonitorHeader();
    _renderMonitorGrid();
    _renderPipeline();
    _renderMonitorFeed();
  }

  function _renderMonitorHeader() {
    const connected = typeof BOT !== "undefined" && BOT.connected;
    const dot  = document.getElementById("mon-dot");
    const text = document.getElementById("mon-status-text");
    if (dot)  dot.className = "monitor-dot " + (connected ? "green" : "dim");
    if (text) text.textContent = connected ? "Bot connected" : "Bot offline";

    const activePairs    = [...enabledPairs];
    const activePatterns = [...enabledPatterns];
    const snap = _lastSnap;

    const el = id => document.getElementById(id);
    if (el("mon-pairs-count"))    el("mon-pairs-count").textContent    = activePairs.length || "all";
    if (el("mon-patterns-count")) el("mon-patterns-count").textContent = activePatterns.length;
    if (el("mon-signals-today"))  el("mon-signals-today").textContent  =
      PIP.detected + PIP.filled + PIP.closed;
    if (el("mon-open-count"))     el("mon-open-count").textContent     =
      snap ? (snap.open_positions || []).length : "—";
  }

  function _renderMonitorGrid() {
    const grid = document.getElementById("monitor-grid");
    if (!grid) return;

    const snap         = _lastSnap;
    const openPos      = snap ? (snap.open_positions || []) : [];
    const activePairs  = [...enabledPairs];
    const activePatterns = [...enabledPatterns];

    // Decide which pairs to show
    let displayPairs = activePairs.length > 0 ? activePairs : ALL_PAIRS;
    // If nothing from catalog is in enabled set, just show up to 20 pairs
    if (displayPairs.length === 0) displayPairs = ALL_PAIRS.slice(0, 20);

    // Build a map: symbol → open position
    const posMap = {};
    openPos.forEach(p => {
      const sym = (p.symbol || "").toUpperCase().replace(/[._-].*$/, "");
      posMap[sym] = p;
    });

    const patChips = activePatterns.length > 0
      ? activePatterns.map(k => `<span class="monitor-pat-chip">${PATTERN_SHORT[k] || k}</span>`).join("")
      : '<span class="monitor-pat-chip" style="color:var(--text-muted);">none</span>';

    grid.innerHTML = displayPairs.map(pair => {
      const pos      = posMap[pair];
      const lastSig  = _lastSigPerPair[pair];
      const inTrade  = !!pos;
      const side     = pos ? pos.side : null;

      let cardClass = "monitor-card";
      if (inTrade && side === "buy")  cardClass += " in-trade-buy";
      if (inTrade && side === "sell") cardClass += " in-trade-sell";
      if (lastSig && (lastSig.event === "signal:accepted") && !inTrade) cardClass += " signal-alert";

      // Status chip
      let chipHtml;
      if (inTrade) {
        chipHtml = `<span class="monitor-status-chip chip-${side}">${side.toUpperCase()}</span>`;
      } else if (lastSig && lastSig.event === "signal:accepted") {
        chipHtml = `<span class="monitor-status-chip chip-signal">SIGNAL</span>`;
      } else {
        chipHtml = `<span class="monitor-status-chip chip-watch">WATCHING</span>`;
      }

      // P&L
      let pnlHtml = "";
      if (inTrade && pos.unrealized_pnl != null) {
        const pnl = parseFloat(pos.unrealized_pnl) || 0;
        pnlHtml = `<div class="monitor-pnl ${pnl >= 0 ? "pos" : "neg"}">
          ${pnl >= 0 ? "+" : ""}$${Math.abs(pnl).toFixed(2)}
          <span style="font-size:10px; font-weight:400; color:var(--text-dim)"> unrealised</span>
        </div>`;
      }

      // Entry info
      let entryHtml = "";
      if (inTrade) {
        entryHtml = `<div style="font-size:11px; color:var(--text-dim);">
          Entry ${pos.entry || "—"} · SL ${pos.sl || "—"} · TP ${pos.tp || "—"}
        </div>`;
      }

      // Last signal summary
      let lastSigHtml = "";
      if (lastSig) {
        const t   = formatHHMMSS ? formatHHMMSS(lastSig.ts) : lastSig.ts.slice(11, 19);
        const lbl = SETUP_LABEL[lastSig.setup] || lastSig.setup;
        const tf  = lastSig.tf;
        let evSpan;
        if (lastSig.event === "order:filled")       evSpan = `<span class="sig-fill">FILLED</span>`;
        else if (lastSig.event === "signal:accepted") evSpan = `<span class="sig-accept">ACCEPTED</span>`;
        else if (lastSig.event === "position:closed") evSpan = `<span>CLOSED</span>`;
        else {
          const stage = lastSig.stage ? ` (${lastSig.stage})` : "";
          evSpan = `<span class="sig-reject">REJECTED${stage}</span>`;
        }
        lastSigHtml = `<div class="monitor-last-sig">
          Last: ${t} · ${lbl} ${tf} · ${evSpan}
        </div>`;
      } else {
        lastSigHtml = `<div class="monitor-last-sig">No signals yet this session</div>`;
      }

      return `
        <div class="${cardClass}">
          <div class="monitor-card-top">
            <span class="monitor-pair-name">${pair}</span>
            ${chipHtml}
          </div>
          <div class="monitor-patterns-row">${patChips}</div>
          ${pnlHtml}
          ${entryHtml}
          ${lastSigHtml}
        </div>`;
    }).join("");
  }

  function _renderPipeline() {
    const set = (id, val) => {
      const el = document.getElementById(id);
      if (!el) return;
      el.textContent = val;
      el.className = "pipeline-count" + (val === 0 ? " zero" : "");
    };
    set("pip-detected",  PIP.detected);
    set("pip-confirmed", PIP.confirmed);
    set("pip-news",      PIP.news);
    set("pip-risk",      PIP.risk);
    set("pip-filled",    PIP.filled);
    set("pip-closed",    PIP.closed);
  }

  function _renderMonitorFeed() {
    const feed    = document.getElementById("monitor-feed");
    const counter = document.getElementById("mon-feed-count");
    if (!feed) return;

    const allEvents = typeof BOT !== "undefined" ? BOT.signals : [];
    if (counter) counter.textContent = allEvents.length + " event" + (allEvents.length !== 1 ? "s" : "");

    if (allEvents.length === 0) {
      feed.innerHTML = `<div class="mf-empty">No signals detected yet — feed will populate as the bot scans.</div>`;
      return;
    }

    feed.innerHTML = allEvents.map(msg => {
      const p   = msg.payload || {};
      const sig = p.signal || p;
      const time = (typeof formatHHMMSS === "function") ? formatHHMMSS(msg.ts) : (msg.ts || "").slice(11, 19);

      const pair    = (sig.symbol || p.symbol || "—").toUpperCase().replace(/[._-].*$/, "");
      const setup   = SETUP_LABEL[sig.setup || ""] || sig.setup || "—";
      const tf      = sig.timeframe || p.timeframe || "";
      const dir     = (sig.direction || sig.side || "").toUpperCase();

      let badgeCls, badgeTxt, detail;

      if (msg.event === "order:filled") {
        badgeCls = "filled";
        badgeTxt = "FILLED";
        detail   = `${dir} ${p.lots || sig.lots || ""}@ ${p.fill_price || "—"} SL:${p.sl || sig.sl || "—"}`;
      } else if (msg.event === "position:closed") {
        badgeCls = "closed";
        badgeTxt = (p.close_reason || "CLOSED").toUpperCase();
        const pnl = parseFloat(p.pnl || 0);
        const rr  = parseFloat(p.rr  || 0);
        detail   = `${dir} closed @ ${p.exit || "—"} → ${pnl >= 0 ? "+" : ""}$${Math.abs(pnl).toFixed(2)} (${rr.toFixed(2)}R)`;
      } else if (msg.event === "signal:accepted") {
        badgeCls = "accepted";
        badgeTxt = "ACCEPTED";
        detail   = `${dir} entry ${sig.entry || "—"} SL:${sig.sl || "—"} TP:${sig.tp || "—"}`;
      } else if (msg.event === "signal:rejected") {
        badgeCls = "rejected";
        badgeTxt = "REJECTED" + (p.stage ? " · " + p.stage : "");
        const note = Array.isArray(sig.notes) ? sig.notes.slice(-1)[0] || "" : "";
        detail   = `${dir}${note ? " · " + note : ""}`;
      } else if (msg.event === "news_agent:command") {
        badgeCls = "news_agent";
        badgeTxt = (p.type || "SL CMD").toUpperCase().replace(/_/g, " ");
        detail   = `ticket:${p.ticket || "—"} new_sl:${p.new_sl || "—"}`;
      } else {
        badgeCls = "closed";
        badgeTxt = msg.event;
        detail   = "";
      }

      const pattern = setup + (tf ? " · " + tf : "");

      return `<div class="mf-row">
        <span class="mf-time">${escapeHtml(time)}</span>
        <span class="mf-pair">${escapeHtml(pair)}</span>
        <span class="mf-pattern">${escapeHtml(pattern)}</span>
        <span class="mf-detail" title="${escapeHtml(detail)}">${escapeHtml(detail)}</span>
        <span class="mf-badge ${badgeCls}">${escapeHtml(badgeTxt)}</span>
      </div>`;
    }).join("");
  }

  // ============================================================================
  // BOT CLIENT — REST + WebSocket to the local Python backend
  // ============================================================================
  // The bot runs on http://localhost:8765 (configurable). When the dashboard is
  // served by the bot itself, same-origin is used. Otherwise we hit localhost.
  //
  // Wire format:
  //   GET  /api/status     -> { running, mode, equity, ... }
  //   POST /api/mode       -> { mode: "paper" | "live" }
  //   POST /api/halt       -> kill switch on
  //   POST /api/resume     -> kill switch off
  //   WS   /ws             -> { event, payload, ts }
  //
  // The dashboard works fully OFFLINE (no bot) using the file-import path.
  // When the bot is online, the topbar pill turns green and the Live Bot tab
  // becomes active.

  const BOT = {
    baseUrl: (function () {
      // Same-origin if served by bot, else localhost:8765
      const o = window.location.origin;
      if (o.startsWith("http")) return o;
      return "http://localhost:8765";
    })(),
    ws: null,
    connected: false,
    mode: "paper",
    haltState: false,
    signals: [],
    reconnectMs: 3000,
    maxSignals: 200,
  };

  function initBotClient() {
    // Mode toggle clicks
    document.querySelectorAll("#mode-toggle button").forEach(btn => {
      btn.addEventListener("click", () => {
        if (btn.classList.contains("disabled")) return;
        const mode = btn.dataset.mode;
        if (mode === BOT.mode) return;
        if (mode === "live") {
          const ok = confirm(
            "Switch to LIVE mode? Orders will be sent to your real broker " +
            "account using the configured caps. Are you sure?"
          );
          if (!ok) return;
        }
        botPostMode(mode);
      });
    });

    document.getElementById("btn-halt").addEventListener("click", () => {
      if (!BOT.connected) return;
      botPost("/api/halt").then(() => log.info("Halted"));
    });
    document.getElementById("btn-resume").addEventListener("click", () => {
      if (!BOT.connected) return;
      botPost("/api/resume").then(() => log.info("Resumed"));
    });

    document.getElementById("btn-reset").addEventListener("click", () => {
      if (!BOT.connected) {
        alert("Bot is not connected. Start the server first.");
        return;
      }
      showResetConfirm();
    });

    // Try the REST status first; if it succeeds, the bot is up
    fetch(BOT.baseUrl + "/api/status")
      .then(r => r.ok ? r.json() : Promise.reject(new Error("not 200")))
      .then(snap => {
        botSetConnected(true);
        renderBotSnapshot(snap);
        renderMonitorTab();
        connectWS();
      })
      .catch(() => {
        botSetConnected(false);
      });
  }

  function connectWS() {
    const wsUrl = BOT.baseUrl.replace(/^http/, "ws") + "/ws";
    try {
      BOT.ws = new WebSocket(wsUrl);
    } catch (e) {
      botSetConnected(false);
      setTimeout(connectWS, BOT.reconnectMs);
      return;
    }
    BOT.ws.onopen = () => {
      // The dashboard only listens; backend pushes events.
      // Send a ping every 25s to keep the connection alive through proxies.
      BOT._ping = setInterval(() => {
        try { BOT.ws.send("ping"); } catch (e) {}
      }, 25000);
    };
    BOT.ws.onmessage = ev => {
      try {
        const msg = JSON.parse(ev.data);
        handleBotEvent(msg);
      } catch (e) {
        // ignore
      }
    };
    BOT.ws.onclose = () => {
      if (BOT._ping) { clearInterval(BOT._ping); BOT._ping = null; }
      botSetConnected(false);
      setTimeout(connectWS, BOT.reconnectMs);
    };
    BOT.ws.onerror = () => { try { BOT.ws.close(); } catch (e) {} };
  }

  function handleBotEvent(msg) {
    if (!msg || !msg.event) return;

    if (msg.event === "engine:status" || msg.event === "engine:mode") {
      // Re-fetch full snapshot for consistency
      fetch(BOT.baseUrl + "/api/status").then(r => r.json()).then(renderBotSnapshot).catch(() => {});
      return;
    }
    if (msg.event === "engine:halt") {
      BOT.haltState = !!(msg.payload && msg.payload.halted);
      renderHaltState();
      return;
    }
    if (msg.event === "engine:reset") {
      // Server wiped state — refresh everything from scratch
      BOT.signals = [];
      BOT.haltState = false;
      renderHaltState();
      fetch(BOT.baseUrl + "/api/status").then(r => r.json()).then(renderBotSnapshot).catch(() => {});
      renderMonitorTab();
      return;
    }
    if (
      msg.event === "signal:accepted"  || msg.event === "signal:rejected" ||
      msg.event === "order:filled"     || msg.event === "order:rejected"  ||
      msg.event === "position:closed"  || msg.event === "news_agent:command" ||
      msg.event === "news_agent:ack"
    ) {
      BOT.signals.unshift(msg);
      if (BOT.signals.length > BOT.maxSignals) BOT.signals.pop();
      // Update pipeline + per-pair tracking
      _updatePipelineFromSignal(msg);
      renderSignalStream();
      renderMonitorTab();
      renderOverviewTab(_lastSnap);   // refresh activity feed
      // Closures + fills change equity/positions — refresh snapshot
      if (msg.event === "order:filled" || msg.event === "position:closed") {
        fetch(BOT.baseUrl + "/api/status").then(r => r.json()).then(snap => {
          renderBotSnapshot(snap);
          renderMonitorTab();
        }).catch(() => {});
      }
    }
    if (msg.event === "pairs:updated" || msg.event === "patterns:updated") {
      renderMonitorTab();
    }
    if (msg.event === "mt5:prices") {
      renderMT5Prices(msg.payload.prices || [], msg.payload.ts);
    }
  }

  function botSetConnected(yes) {
    BOT.connected = yes;
    const pill = document.getElementById("bot-pill");
    const text = document.getElementById("bot-status-text");
    const offline = document.getElementById("bot-offline-banner");
    const online  = document.getElementById("bot-online-content");
    if (yes) {
      pill.classList.add("connected");
      pill.classList.remove("disconnected");
      text.textContent = "Bot: online";
      if (offline) offline.style.display = "none";
      if (online)  online.style.display  = "block";
      startMT5PricePolling();
    } else {
      pill.classList.remove("connected");
      pill.classList.add("disconnected");
      text.textContent = "Bot: offline";
      if (offline) offline.style.display = "block";
      if (online)  online.style.display  = "none";
      // Disable mode toggle when offline
      document.querySelectorAll("#mode-toggle button").forEach(b => b.classList.add("disabled"));
      renderOverviewTab(null);
      stopMT5PricePolling();
    }
  }

  // ── Overview tab ──────────────────────────────────────────────────────────
  function renderOverviewTab(s) {
    const offline  = document.getElementById("ov-offline-banner");
    const live     = document.getElementById("ov-live-content");
    if (!s || !BOT.connected) {
      if (offline) offline.style.display = "flex";
      if (live)    live.style.display    = "none";
      return;
    }
    if (offline) offline.style.display = "none";
    if (live)    live.style.display    = "block";

    const set = (id, v) => { const el = document.getElementById(id); if (el) el.textContent = v; };
    const fmt = (n, d=2) => n != null ? fmtNumber(n, d) : "—";

    // Equity
    const eq = s.equity != null ? curSym() + fmt(s.equity) : "—";
    set("ov-equity", eq);

    // P&L today
    const pnlDay = s.realized_today || 0;
    const pnlDayEl = document.getElementById("ov-pnl-today");
    if (pnlDayEl) {
      pnlDayEl.textContent = (pnlDay >= 0 ? "+" : "−") + curSym() + fmt(Math.abs(pnlDay));
      pnlDayEl.style.color = pnlDay >= 0 ? "var(--green)" : "var(--red)";
    }

    // P&L this week
    const pnlWk = s.realized_this_week || 0;
    const pnlWkEl = document.getElementById("ov-pnl-week");
    if (pnlWkEl) {
      pnlWkEl.textContent = (pnlWk >= 0 ? "+" : "−") + curSym() + fmt(Math.abs(pnlWk));
      pnlWkEl.style.color = pnlWk >= 0 ? "var(--green)" : "var(--red)";
    }

    // Trades
    set("ov-trades", `${s.trades_today || 0} / ${s.trades_this_week || 0}`);

    // Open count
    const openPos = (s.open_positions || []).filter(p => isPairEnabled(p.symbol));
    set("ov-open", openPos.length);

    // Mode
    set("ov-mode", (s.mode || "paper").toUpperCase());

    // Active pairs
    const cap = s.config_summary || {};
    const pairsEl = document.getElementById("ov-pairs");
    if (pairsEl) {
      const pairs = cap.instruments || [];
      pairsEl.innerHTML = pairs.length
        ? pairs.map(p => `<span class="chip green" style="font-size:11px;">${p}</span>`).join("")
        : `<span style="color:var(--text-dim);font-size:13px;">None enabled</span>`;
    }

    // Active patterns
    const patsEl = document.getElementById("ov-patterns");
    if (patsEl) {
      const pats = (cap.patterns_enabled || []).map(k => k.replace(/_/g, " ").replace(/\b\w/g, c => c.toUpperCase()));
      patsEl.innerHTML = pats.length
        ? pats.map(p => `<span class="chip green" style="font-size:11px;">${p}</span>`).join("")
        : `<span style="color:var(--text-dim);font-size:13px;">None enabled</span>`;
    }

    // Timeframes
    set("ov-timeframes", (cap.timeframes || []).join("  ·  ") || "—");

    // Open positions list
    const openListEl = document.getElementById("ov-open-list");
    if (openListEl) {
      if (openPos.length === 0) {
        openListEl.innerHTML = `<span style="color:var(--text-dim);">No open positions.</span>`;
      } else {
        openListEl.innerHTML = openPos.map(p => {
          const pnlSign = (p.pnl || 0) >= 0 ? "var(--green)" : "var(--red)";
          const pnlStr  = p.pnl != null ? ((p.pnl >= 0 ? "+" : "−") + curSym() + fmt(Math.abs(p.pnl))) : "";
          return `
            <div style="padding:8px 0;border-bottom:1px solid var(--border);display:flex;justify-content:space-between;align-items:center;">
              <div>
                <strong>${p.symbol}</strong>
                <span style="color:${p.side==='buy'?'var(--green)':'var(--red)'}"> ${(p.side||"").toUpperCase()}</span>
                <span style="color:var(--text-muted);font-size:12px;"> · ${p.setup||""} ${p.timeframe||""}</span>
              </div>
              <div style="text-align:right;font-size:13px;">
                <span style="color:${pnlSign};font-weight:600;">${pnlStr}</span>
                <div style="color:var(--text-muted);font-size:11px;">${p.lots} lots @ ${p.fill_price||p.entry||"—"}</div>
              </div>
            </div>`;
        }).join("");
      }
    }

    // Recent activity (last 6 signals)
    const actEl = document.getElementById("ov-activity");
    if (actEl) {
      const recent = BOT.signals.slice(0, 6);
      if (recent.length === 0) {
        actEl.innerHTML = `<span style="color:var(--text-dim);">Waiting for signals…</span>`;
      } else {
        const ICONS = {
          "signal:accepted": "✅", "order:filled": "📥",
          "signal:rejected": "🚫", "order:rejected": "❌",
          "position:closed": "🔒", "news_agent:command": "📰",
        };
        actEl.innerHTML = recent.map(msg => {
          const sig  = msg.payload?.signal || msg.payload || {};
          const sym  = sig.symbol || sig.Symbol || "—";
          const side = sig.side   || sig.direction || "";
          const icon = ICONS[msg.event] || "·";
          const label = msg.event.replace(":", " · ");
          const t = msg.ts ? msg.ts.slice(11, 16) + " UTC" : "";
          return `
            <div style="display:flex;align-items:center;gap:10px;padding:7px 0;border-bottom:1px solid var(--border);">
              <span style="font-size:16px;">${icon}</span>
              <div style="flex:1;min-width:0;">
                <span style="font-weight:600;">${sym}</span>
                <span style="color:${side==='buy'?'var(--green)':side==='sell'?'var(--red)':'var(--text-dim)'};">
                  ${side ? ' ' + side.toUpperCase() : ''}
                </span>
                <span style="color:var(--text-muted);font-size:12px;"> · ${label}</span>
              </div>
              <span style="color:var(--text-muted);font-size:11px;white-space:nowrap;">${t}</span>
            </div>`;
        }).join("");
      }
    }

    // Next news event
    const newsEl = document.getElementById("ov-news");
    if (newsEl) {
      const upcoming = s.upcoming_news || [];
      if (upcoming.length === 0) {
        newsEl.textContent = "No high-impact events in the next 24h.";
      } else {
        const n = upcoming[0];
        newsEl.innerHTML = `
          <strong style="color:var(--yellow);">${n.currency || ""}</strong>
          ${n.title || ""}
          <span style="color:var(--text-muted);"> — ${formatHHMM(n.time_utc)}</span>
          ${upcoming.length > 1 ? `<span style="color:var(--text-muted);font-size:12px;margin-left:8px;">+${upcoming.length-1} more</span>` : ""}
        `;
      }
    }
  }

  function renderBotSnapshot(s) {
    if (!s) return;
    _lastSnap = s;   // store for monitor tab
    BOT.mode = s.mode || "paper";
    BOT.haltState = !!s.halted;

    // Mode toggle highlight
    document.querySelectorAll("#mode-toggle button").forEach(b => {
      b.classList.toggle("active", b.dataset.mode === BOT.mode);
      b.classList.remove("disabled");
    });
    document.getElementById("bot-live-warning").classList.toggle("hidden", BOT.mode !== "live");

    // Stat tiles
    const setText = (id, v) => { const el = document.getElementById(id); if (el) el.textContent = v; };
    setText("bot-mode-display", s.mode ? s.mode.toUpperCase() : "—");
    setText("bot-equity", s.equity != null ? curSym() + fmtNumber(s.equity, 2) : "—");
    const pnl = s.realized_today || 0;
    const pnlEl = document.getElementById("bot-pnl-today");
    if (pnlEl) {
      pnlEl.textContent = (pnl >= 0 ? "+" : "−") + curSym() + fmtNumber(Math.abs(pnl), 2);
      pnlEl.style.color = pnl >= 0 ? "var(--green)" : "var(--red)";
    }
    const cap = s.config_summary || {};
    setText("bot-trades-count", (s.trades_today || 0) + " / " + (s.trades_this_week || 0));
    setText("bot-open-count", (s.open_positions || []).length);

    const upcoming = s.upcoming_news || [];
    setText("bot-news-next", upcoming.length
      ? `${upcoming[0].currency} ${upcoming[0].title.slice(0, 24)} @ ${formatHHMM(upcoming[0].time_utc)}`
      : "Clear next 24h");

    setText("bot-cfg-instruments", (cap.instruments || []).join(", ") || "—");
    setText("bot-cfg-timeframes",  (cap.timeframes  || []).join(", ") || "—");
    setText("bot-cfg-risk",        cap.per_trade_pct != null ? cap.per_trade_pct + "%" : "—");
    setText("bot-cfg-rr",          cap.min_rr != null ? "1:" + cap.min_rr : "—");
    setText("bot-cfg-daycap",      cap.daily_trade_cap != null ? cap.daily_trade_cap : "—");
    setText("bot-cfg-weekcap",     cap.weekly_trade_cap != null ? cap.weekly_trade_cap : "—");
    setText("bot-cfg-patterns",    (cap.patterns_enabled || []).join(", ") || "none");

    // Open positions list
    const openList = document.getElementById("bot-open-list");
    if (openList) {
      const ps = (s.open_positions || []).filter(p => isPairEnabled(p.symbol));
      if (ps.length === 0) {
        openList.textContent = "None";
      } else {
        openList.innerHTML = ps.map(p =>
          `<div style="padding:4px 0; border-bottom:1px solid var(--border);">
             <strong>${p.symbol}</strong> ${p.side.toUpperCase()} ${p.lots} lots @ ${p.fill_price}
             <br><span class="hint">${p.setup}-${p.timeframe}, SL ${p.sl}, TP ${p.tp}</span>
           </div>`
        ).join("");
      }
    }

    renderHaltState();
    renderOverviewTab(s);
  }

  // Per-pair pip/point size (mirrors config.yaml pip_size values).
  // Returns {size, label, dec}: JPY crosses 0.01, standard FX 0.0001,
  // metals per convention, indices/crypto/energy → whole points.
  function _pipInfo(symbol) {
    const s = (symbol || "").toUpperCase();
    if (s.includes("JPY")) return { size: 0.01,   label: "pips", dec: 1 };
    if (s === "XAUUSD" || s === "XPTUSD") return { size: 0.1, label: "pips", dec: 1 };
    if (s === "XAGUSD") return { size: 0.001, label: "pips", dec: 1 };
    // Crypto is 6 letters too (BTCUSD…) — must not fall into the FX branch.
    const CRYPTO = ["BTC", "ETH", "LTC", "XRP", "BNB", "SOL", "ADA", "DOG", "BCH", "DOT", "LIN"];
    if (CRYPTO.includes(s.slice(0, 3))) return { size: 1.0, label: "pts", dec: 1 };
    if (/^[A-Z]{6}$/.test(s)) return { size: 0.0001, label: "pips", dec: 1 };  // FX
    return { size: 1.0, label: "pts", dec: 1 };       // indices, energy
  }

  // Signed pips/points captured between two prices for a position side.
  // Returns formatted HTML cell content (or "—" when not computable).
  function _pipsCell(symbol, side, fromPrice, toPrice) {
    const a = Number(fromPrice), b = Number(toPrice);
    if (isNaN(a) || isNaN(b) || a <= 0 || b <= 0) return "—";
    const info = _pipInfo(symbol);
    const sign = (side || "").toLowerCase() === "sell" ? -1 : 1;
    const pips = ((b - a) * sign) / info.size;
    const col = pips >= 0 ? "var(--green)" : "var(--red)";
    return `<span style="color:${col};font-weight:600;" title="${info.label}">` +
           `${pips >= 0 ? "+" : "−"}${Math.abs(pips).toFixed(info.dec)}` +
           `<span style="font-size:9px;color:var(--text-muted);"> ${info.label}</span></span>`;
  }

  // Pick a sensible number of decimals for a price.
  function _priceDecimals(symbol, v) {
    const s = (symbol || "").toUpperCase();
    if (s.includes("JPY")) return 3;
    const a = Math.abs(v || 0);
    if (a >= 100) return 2;     // indices, crypto, gold
    if (a >= 10) return 3;
    return 5;                   // FX
  }
  function _fmtPrice(symbol, v) {
    if (v == null || isNaN(v) || Number(v) === 0) return "—";   // 0 = not set
    return Number(v).toFixed(_priceDecimals(symbol, v));
  }

  // Rich live open-positions table: entry / current / SL / TP / P&L / progress.
  function renderLivePositions(positions) {
    const table = document.getElementById("live-positions-table");
    const empty = document.getElementById("live-positions-empty");
    const tbody = table ? table.querySelector("tbody") : null;
    const countEl = document.getElementById("live-pos-count");
    const pnlEl = document.getElementById("live-pos-pnl");
    if (!table || !tbody) return;

    const ps = (positions || []).filter(p => isPairEnabled(p.symbol));
    if (!ps.length) {
      table.style.display = "none";
      if (empty) empty.style.display = "block";
      if (countEl) countEl.textContent = "";
      if (pnlEl) pnlEl.textContent = "";
      return;
    }
    if (empty) empty.style.display = "none";
    table.style.display = "table";

    let totalPnl = 0;
    const ccy = (D.account && D.account.currency) || "USD";
    tbody.innerHTML = ps.map((p, _pi) => {
      const sym = p.symbol;
      const side = (p.side || "").toLowerCase();
      const entry = Number(p.entry ?? p.fill_price);
      const cur = Number(p.current ?? p.price_current ?? p.fill_price ?? entry);
      const sl = Number(p.sl);
      const tp = Number(p.tp);
      const pnl = Number(p.unrealized_pnl ?? p.pnl ?? 0);
      totalPnl += isNaN(pnl) ? 0 : pnl;

      const distSL = (!isNaN(sl) && sl > 0) ? Math.abs(cur - sl) : null;
      const distTP = (!isNaN(tp) && tp > 0) ? Math.abs(tp - cur) : null;

      // progress toward TP (0–100%)
      let prog = null;
      if (!isNaN(tp) && !isNaN(entry) && tp !== entry) {
        prog = side === "sell"
          ? (entry - cur) / (entry - tp)
          : (cur - entry) / (tp - entry);
        prog = Math.max(0, Math.min(1, prog)) * 100;
      }
      const sideCol = side === "buy" ? "var(--green)" : "var(--red)";
      const pnlCol = pnl >= 0 ? "var(--green)" : "var(--red)";
      const progBar = prog == null ? "—" : `
        <div style="background:var(--bg-elev-2);border-radius:6px;height:14px;position:relative;overflow:hidden;">
          <div style="width:${prog.toFixed(0)}%;height:100%;background:${pnlCol};opacity:.55;"></div>
          <span style="position:absolute;inset:0;font-size:10px;line-height:14px;text-align:center;color:var(--text);">${prog.toFixed(0)}%</span>
        </div>`;

      return `<tr>
        <td><strong>${sym}</strong></td>
        <td style="color:${sideCol};font-weight:600;">${side.toUpperCase()}</td>
        <td class="num">${p.lots ?? "—"}</td>
        <td class="num">${_fmtPrice(sym, entry)}</td>
        <td class="num"><strong>${_fmtPrice(sym, cur)}</strong></td>
        <td class="num" style="color:var(--red);">${_fmtPrice(sym, sl)}</td>
        <td class="num" style="color:var(--green);">${_fmtPrice(sym, tp)}</td>
        <td class="num" style="color:${pnlCol};font-weight:600;">${(pnl>=0?"+":"−")}${curSym()}${fmtNumber(Math.abs(pnl),2)}</td>
        <td class="num">${_pipsCell(sym, side, entry, cur)}</td>
        <td class="num">${distSL == null ? "—" : _fmtPrice(sym, distSL)}</td>
        <td class="num">${distTP == null ? "—" : _fmtPrice(sym, distTP)}</td>
        <td>${progBar}</td>
        <td class="num" title="Money at risk recorded at fill (sizing invariant basis)">
          ${p.risked_money ? curSym() + fmtNumber(p.risked_money, 2) : "—"}</td>
        <td><button class="btn secondary" style="font-size:11px;padding:3px 10px;"
          title="Live candle chart with entry / SL / TP" onclick="openPositionChart(${p.ticket != null ? p.ticket : _pi})">📈</button></td>
      </tr>`;
    }).join("");

    if (countEl) countEl.textContent = `${ps.length} open`;
    if (pnlEl) {
      pnlEl.textContent = (totalPnl >= 0 ? "+" : "−") + curSym() + fmtNumber(Math.abs(totalPnl), 2);
      pnlEl.style.color = totalPnl >= 0 ? "var(--green)" : "var(--red)";
    }
  }

  function renderHaltState() {
    const halt = document.getElementById("btn-halt");
    const resume = document.getElementById("btn-resume");
    if (!halt || !resume) return;
    if (BOT.haltState) {
      halt.style.display = "none";
      resume.style.display = "block";
    } else {
      halt.style.display = "block";
      resume.style.display = "none";
    }
  }

  // ── Reset confirmation dialog ──────────────────────────────────────────────
  function showResetConfirm() {
    // Remove any existing dialog
    const existing = document.getElementById("reset-modal");
    if (existing) existing.remove();

    const modal = document.createElement("div");
    modal.id = "reset-modal";
    modal.style.cssText = `
      position:fixed;inset:0;z-index:9999;
      background:rgba(0,0,0,0.75);
      display:flex;align-items:center;justify-content:center;
    `;
    modal.innerHTML = `
      <div style="
        background:var(--bg-elev);border:1px solid var(--yellow);
        border-radius:12px;padding:32px;max-width:400px;width:90%;text-align:center;
        box-shadow:0 8px 32px rgba(0,0,0,0.5);
      ">
        <div style="font-size:36px;margin-bottom:12px;">🔄</div>
        <h3 style="margin:0 0 12px;color:var(--yellow);">Reset Paper Session?</h3>
        <p style="color:var(--text-dim);margin:0 0 24px;font-size:13px;line-height:1.6;">
          This will <strong style="color:var(--text);">permanently clear</strong>:<br>
          • All open positions<br>
          • Full trade history &amp; P&amp;L<br>
          • Signal log &amp; dashboard feed<br>
          • Risk counters (equity restored to starting balance)
        </p>
        <div style="display:flex;gap:12px;justify-content:center;">
          <button id="reset-cancel" style="
            flex:1;padding:10px;border-radius:8px;border:1px solid var(--border-strong);
            background:var(--bg-elev-2);color:var(--text);cursor:pointer;font-size:14px;
          ">Cancel</button>
          <button id="reset-confirm" style="
            flex:1;padding:10px;border-radius:8px;border:1px solid var(--yellow);
            background:var(--yellow-soft);color:var(--yellow);cursor:pointer;
            font-size:14px;font-weight:600;
          ">Yes, Reset</button>
        </div>
      </div>
    `;
    document.body.appendChild(modal);

    document.getElementById("reset-cancel").onclick = () => modal.remove();
    modal.onclick = e => { if (e.target === modal) modal.remove(); };

    document.getElementById("reset-confirm").onclick = () => {
      modal.remove();
      botPost("/api/reset")
        .then(r => r.json())
        .then(data => {
          if (data.ok) {
            // Clear local state immediately
            BOT.signals = [];
            BOT.haltState = false;
            renderHaltState();
            renderMonitorTab();
            fetch(BOT.baseUrl + "/api/status").then(r => r.json()).then(renderBotSnapshot).catch(() => {});
            // Flash a success toast
            showToast("✅ Paper session reset — fresh start!");
          }
        })
        .catch(() => showToast("❌ Reset failed — is the server running?"));
    };
  }

  function showToast(msg) {
    const t = document.createElement("div");
    t.textContent = msg;
    t.style.cssText = `
      position:fixed;bottom:24px;left:50%;transform:translateX(-50%);
      background:var(--bg-elev);border:1px solid var(--border-strong);
      color:var(--text);padding:12px 24px;border-radius:8px;
      box-shadow:0 4px 16px rgba(0,0,0,0.4);z-index:9999;
      font-size:14px;pointer-events:none;
    `;
    document.body.appendChild(t);
    setTimeout(() => t.remove(), 3500);
  }

  // ═══════════════════════════════════════════════════════════════════════
  // MT5 LIVE PRICES TAB
  // ═══════════════════════════════════════════════════════════════════════

  let _mt5Chart      = null;   // active Chart.js instance
  let _mt5ChartSym   = null;   // currently charted symbol
  let _mt5ChartTf    = "1h";   // current timeframe
  let _mt5PriceTimer = null;   // polling interval

  // Start polling prices every 5 s when bot connects
  function startMT5PricePolling() {
    if (_mt5PriceTimer) return;
    fetchAndRenderPrices();
    refreshAgentTrades();
    _mt5PriceTimer = setInterval(() => {
      fetchAndRenderPrices();
      refreshAgentTrades();
    }, 5000);
  }

  let _lastClosedCount = -1;

  // Pull ONLY the agent's own trades (paper ledger) and drive the dashboard:
  //  - the live Open Positions table (entry/current/SL/TP/running P&L)
  //  - the Trade Log + KPIs from the agent's CLOSED trades
  // This is independent of the broker account snapshot, so manual/account
  // trades never appear — only what the bot itself did.
  function refreshAgentTrades() {
    if (!BOT.connected) return;
    fetch(BOT.baseUrl + "/api/agent/trades")
      .then(r => r.json())
      .then(data => {
        const open = data.open || [];
        const closed = data.closed || [];
        _agentOpenRaw = open;                      // raw records for trade charts
        _agentClosedRaw = closed;
        renderLivePositions(open);                 // live table every poll

        if (D) {
          if (data.currency) D.account.currency = data.currency;   // match MT5 account ccy
          if (data.equity != null) {
            // Live REALIZED balance from the paper ledger (starting capital
            // + all closed P&L). Floating P&L is shown alongside in the KPI.
            D.account.equity = data.equity;
            D.account.balance = data.equity;
          }
          if (data.starting_equity != null) {
            D.account.broker = "Paper (start " + curSym() +
              Number(data.starting_equity).toLocaleString("en-IN") + ")";
          }
          // Open positions must track the live feed on EVERY poll — previously
          // they were only refreshed when a trade closed, so the Trade Log
          // tab's Open Positions table drifted from the Overview live table.
          D.openPositions = open.map(mapAgentOpenTrade);
          try { renderOpenPositions(); } catch (_) {}
          // Balance/equity KPIs must track the ledger live, not wait for the
          // next closed trade.
          try { renderKPIs(); } catch (_) {}
          // Only rebuild the (heavier) Trade Log / KPIs when a trade closes.
          if (closed.length !== _lastClosedCount) {
            _lastClosedCount = closed.length;
            D.trades = closed.map(mapAgentClosedTrade);
            try { renderAll(); } catch (_) {}
          }
        }
      })
      .catch(() => {});
  }

  // Map an agent CLOSED trade (paper PositionUpdate) to the dashboard format.
  function mapAgentClosedTrade(t, i) {
    const pnl = Number(t.pnl) || 0;
    return {
      ticket: t.ticket,
      _rawIdx: i,                      // index into _agentClosedRaw for the trade chart
      entryTime: t.entry_time || "",
      symbol: t.symbol,
      setup: t.setup || "",
      timeframe: t.timeframe || "",
      category: "LIVE",
      side: t.side,
      lots: t.lots != null ? Number(t.lots) : null,
      entry: Number(t.entry),
      sl: Number(t.sl),
      tp: Number(t.tp),
      exit: Number(t.exit),
      // signed pips captured (for the sortable Pips column)
      pips: (() => {
        const e = Number(t.entry), x = Number(t.exit);
        if (isNaN(e) || isNaN(x) || e <= 0 || x <= 0) return null;
        const sgn = (t.side || "").toLowerCase() === "sell" ? -1 : 1;
        return ((x - e) * sgn) / _pipInfo(t.symbol).size;
      })(),
      rr: Number(t.rr) || 0,
      pnl: pnl,
      closeTime: t.close_time,
      closeReason: t.close_reason || "",
      outcome: pnl > 0 ? "win" : (pnl < 0 ? "loss" : "be"),
      comment: (t.setup || "") + (t.timeframe ? "-" + t.timeframe : ""),
    };
  }
  // Map an agent OPEN trade for the open-positions views.
  function mapAgentOpenTrade(p) {
    const entry = Number(p.entry ?? p.fill_price);
    const tp = Number(p.tp);
    const cur = Number(p.current ?? p.fill_price);
    const side = p.side;
    let prog = 0;
    if (!isNaN(tp) && !isNaN(entry) && tp !== entry) {
      prog = side === "sell" ? (entry - cur) / (entry - tp) : (cur - entry) / (tp - entry);
      prog = Math.max(0, Math.min(1, prog)) * 100;
    }
    return {
      symbol: p.symbol, setup: p.setup || "", timeframe: p.timeframe || "",
      category: "LIVE", side: side,
      entry: entry, sl: Number(p.sl), tp: tp, current: cur,
      unrealizedPnl: Number(p.unrealized_pnl) || 0,
      lots: p.lots,
      progressPct: prog,
      scaled: !!p.partial_done,   // 50% already booked → remainder rides the trail
    };
  }

  function stopMT5PricePolling() {
    if (_mt5PriceTimer) { clearInterval(_mt5PriceTimer); _mt5PriceTimer = null; }
  }

  function fetchAndRenderPrices() {
    if (!BOT.connected) return;
    fetch(BOT.baseUrl + "/api/mt5/prices")
      .then(r => r.json())
      .then(data => renderMT5Prices(data.prices || [], data.ts))
      .catch(() => {});
  }

  function renderMT5Prices(prices, ts) {
    const offline = document.getElementById("mt5-prices-offline");
    const grid    = document.getElementById("mt5-prices-grid");
    const tsEl    = document.getElementById("mt5-prices-ts");

    if (!prices.length) {
      if (offline) offline.style.display = "flex";
      if (grid)    grid.style.display    = "none";
      return;
    }
    if (offline) offline.style.display = "none";
    if (grid)    grid.style.display    = "grid";
    if (tsEl && ts) tsEl.textContent = "updated " + ts.slice(11, 19) + " UTC";

    grid.innerHTML = prices.map(p => {
      const chg     = p.change_pct || 0;
      const chgSign = chg >= 0 ? "+" : "";
      const chgCol  = chg >= 0 ? "var(--green)" : "var(--red)";
      const chgArr  = chg >= 0 ? "▲" : "▼";
      const digits  = p.digits || 5;
      const bid     = (p.bid || 0).toFixed(digits);
      const ask     = (p.ask || 0).toFixed(digits);
      const hi      = (p.day_high || 0).toFixed(digits);
      const lo      = (p.day_low  || 0).toFixed(digits);
      const spread  = (p.spread || 0).toFixed(1);

      return `
        <div class="card mt5-price-card" data-sym="${p.symbol}" style="
          cursor:pointer;padding:16px;transition:border-color .15s;
          border:1px solid var(--border);
        " onmouseenter="this.style.borderColor='var(--blue)'"
           onmouseleave="this.style.borderColor='var(--border)'"
           onclick="openMT5Chart('${p.symbol}')">
          <div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:10px;">
            <strong style="font-size:14px;">${p.symbol}</strong>
            <span style="color:${chgCol};font-size:12px;font-weight:600;">${chgArr} ${chgSign}${chg.toFixed(3)}%</span>
          </div>
          <div style="font-size:22px;font-weight:700;color:var(--text);letter-spacing:.02em;margin-bottom:4px;">${bid}</div>
          <div style="font-size:11px;color:var(--text-muted);margin-bottom:10px;">Ask ${ask} · Spread ${spread} pts</div>
          <div style="display:flex;justify-content:space-between;font-size:11px;color:var(--text-muted);">
            <span>H ${hi}</span>
            <span>L ${lo}</span>
          </div>
        </div>`;
    }).join("");
  }

  // Called when user clicks a price card
  function openMT5Chart(sym) {
    _mt5ChartSym = sym;
    const panel = document.getElementById("mt5-chart-panel");
    const title = document.getElementById("mt5-chart-title");
    if (panel) panel.style.display = "block";
    if (title) title.textContent = sym;
    // Scroll chart into view
    if (panel) panel.scrollIntoView({ behavior: "smooth", block: "nearest" });
    loadMT5Chart(sym, _mt5ChartTf);
  }

  function loadMT5Chart(sym, tf) {
    _mt5ChartTf = tf;
    // Update active TF button
    document.querySelectorAll(".mt5-tf").forEach(b => {
      b.classList.toggle("active", b.dataset.tf === tf);
    });
    // Show loading
    const loading = document.getElementById("mt5-chart-loading");
    if (loading) loading.style.display = "flex";

    fetch(BOT.baseUrl + "/api/chart/" + sym + "?tf=" + tf)
      .then(r => r.json())
      .then(data => drawMT5Chart(sym, tf, data.bars || [], data.source))
      .catch(() => {
        if (loading) loading.textContent = "Failed to load chart data.";
      });
  }

  // Chart data is sourced exclusively from the live MT5 feed.
  function setChartSource(source) {
    const el = document.getElementById("mt5-chart-source");
    if (!el) return;
    if (source === "mt5") {
      el.textContent = "● MT5 live";
      el.style.background = "rgba(16,185,129,0.15)";
      el.style.color = "var(--green)";
    } else {
      el.textContent = "";
      el.style.background = "transparent";
    }
  }

  function drawMT5Chart(sym, tf, bars, source) {
    const loading = document.getElementById("mt5-chart-loading");
    const canvas  = document.getElementById("mt5-chart-canvas");
    if (!canvas) return;

    setChartSource(source);

    if (!bars.length) {
      if (loading) {
        loading.style.display = "flex";
        loading.textContent = "No MT5 bars yet — waiting for the EA to push this symbol/timeframe.";
      }
      return;
    }
    if (loading) loading.style.display = "none";

    // Destroy old chart instance
    if (_mt5Chart) { _mt5Chart.destroy(); _mt5Chart = null; }

    // Candlestick mode (default) — custom renderer with OHLC hover readout.
    // The Chart.js line mode below is kept and reachable via the style toggle.
    if (_mt5ChartStyle === "candles") {
      drawCandleChart(canvas, bars, {}, { symbol: sym, daily: (tf === "1D" || tf === "1d") });
      return;
    }

    const isDaily = (tf === "1D" || tf === "1d");
    const labels = bars.map(b => {
      const d = new Date(b.t);
      if (isDaily) return d.toLocaleDateString("en-GB", { month:"short", day:"numeric" });
      return d.toLocaleTimeString("en-GB", { hour:"2-digit", minute:"2-digit" });
    });
    const closes = bars.map(b => b.c);

    // Colour: green if last close > first close, else red
    const rising  = closes[closes.length - 1] >= closes[0];
    const lineCol = rising ? "rgba(16,185,129,1)" : "rgba(239,68,68,1)";
    const fillCol = rising ? "rgba(16,185,129,0.08)" : "rgba(239,68,68,0.08)";

    _mt5Chart = new Chart(canvas, {
      type: "line",
      data: {
        labels,
        datasets: [{
          label: sym + " " + tf,
          data: closes,
          borderColor: lineCol,
          backgroundColor: fillCol,
          borderWidth: 1.5,
          pointRadius: 0,
          pointHoverRadius: 4,
          fill: true,
          tension: 0.1,
        }],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: "index", intersect: false },
        plugins: {
          legend: { display: false },
          tooltip: {
            callbacks: {
              label: ctx => {
                const b = bars[ctx.dataIndex] || {};
                return [
                  "O " + (b.o != null ? b.o : "—") + "   H " + (b.h != null ? b.h : "—"),
                  "L " + (b.l != null ? b.l : "—") + "   C " + (b.c != null ? b.c : ctx.parsed.y),
                ];
              },
            },
          },
        },
        scales: {
          x: {
            ticks: { color: "#64748b", maxTicksLimit: 10, maxRotation: 0 },
            grid:  { color: "#1f2a44" },
          },
          y: {
            ticks: { color: "#64748b" },
            grid:  { color: "#1f2a44" },
            position: "right",
          },
        },
      },
    });
  }

  // Wire up TF buttons and close button (called once on init)
  function initMT5ChartControls() {
    document.querySelectorAll(".mt5-tf").forEach(btn => {
      btn.addEventListener("click", () => {
        if (_mt5ChartSym) loadMT5Chart(_mt5ChartSym, btn.dataset.tf);
      });
    });
    // Candles / Line style toggle (candles are the default).
    const styleBtn = document.getElementById("mt5-chart-style");
    if (styleBtn) {
      styleBtn.textContent = _mt5ChartStyle === "candles" ? "Candles ▾" : "Line ▾";
      styleBtn.addEventListener("click", () => {
        _mt5ChartStyle = _mt5ChartStyle === "candles" ? "line" : "candles";
        styleBtn.textContent = _mt5ChartStyle === "candles" ? "Candles ▾" : "Line ▾";
        if (_mt5ChartSym) loadMT5Chart(_mt5ChartSym, _mt5ChartTf);
      });
    }
    const closeBtn = document.getElementById("mt5-chart-close");
    if (closeBtn) closeBtn.addEventListener("click", () => {
      const panel = document.getElementById("mt5-chart-panel");
      if (panel) panel.style.display = "none";
      if (_mt5Chart) { _mt5Chart.destroy(); _mt5Chart = null; }
      _mt5ChartSym = null;
    });
  }

  function renderSignalStream() {
    const el = document.getElementById("signal-stream");
    if (!el) return;
    if (BOT.signals.length === 0) {
      el.innerHTML = `<div class="empty">Waiting for the bot to detect its first signal…</div>`;
      return;
    }
    el.innerHTML = BOT.signals.map(msg => {
      const p = msg.payload || {};
      const sig = p.signal || p;
      const time = formatHHMMSS(msg.ts);
      let evClass = "";
      let evLabel = msg.event;
      if (msg.event === "signal:accepted") { evClass = "accepted"; evLabel = "ACCEPTED"; }
      else if (msg.event === "signal:rejected") { evClass = "rejected"; evLabel = "REJECTED (" + (p.stage || "?") + ")"; }
      else if (msg.event === "order:filled") { evClass = "filled"; evLabel = "FILLED"; }
      else if (msg.event === "order:rejected") { evClass = "error"; evLabel = "ORDER_REJ"; }
      else if (msg.event === "position:closed") { evClass = "closed"; evLabel = (p.close_reason || "CLOSED").toUpperCase(); }

      let body;
      if (msg.event === "position:closed") {
        body = `${p.symbol} ${p.side.toUpperCase()} ${p.lots} → ${p.exit} (${p.pnl >= 0 ? "+" : ""}$${fmtNumber(p.pnl, 2)}, ${p.rr.toFixed(2)}R)`;
      } else if (msg.event === "order:filled") {
        body = `${p.symbol} ${p.side.toUpperCase()} ${p.lots} @ ${p.fill_price} (${p.setup}-${p.timeframe})`;
      } else {
        // Prefer the structured failing check (new logging) over the last note,
        // which could misleadingly be a PASSING check's message.
        let why = "";
        if (msg.event === "signal:rejected") {
          const checks = p.checks || [];
          const failing = checks.find(c => c && c.passed === false);
          why = failing ? failing.detail : (p.failed_check || "");
        }
        if (!why) {
          why = Array.isArray(sig.notes) ? sig.notes.slice(-1)[0] || "" : "";
        }
        const clarity = (sig.clarity_score && sig.clarity_score > 0)
          ? ` [clarity ${Math.round(sig.clarity_score)}]` : "";
        body = `${sig.symbol || ""} ${(sig.setup || "")}-${(sig.timeframe || "")} ${(sig.side || "").toUpperCase()} entry ${sig.entry || "?"}${clarity} ${why ? "· " + why : ""}`;
      }
      return `<div class="row"><span class="time">${time}</span><span class="ev ${evClass}">${evLabel}</span><span class="body">${escapeHtml(body)}</span></div>`;
    }).join("");
  }

  function botPostMode(newMode) {
    botPost("/api/mode", { mode: newMode }).then(res => {
      if (!res) return;
      // Server returns {mode, note}; reflect optimistically and refetch
      BOT.mode = res.mode;
      document.querySelectorAll("#mode-toggle button").forEach(b => {
        b.classList.toggle("active", b.dataset.mode === BOT.mode);
      });
      document.getElementById("bot-live-warning").classList.toggle("hidden", BOT.mode !== "live");
      fetch(BOT.baseUrl + "/api/status").then(r => r.json()).then(renderBotSnapshot).catch(() => {});
      if (res.note) alert(res.note);
    }).catch(err => {
      alert("Could not switch mode: " + (err && err.message ? err.message : err));
    });
  }

  function botPost(path, body) {
    return fetch(BOT.baseUrl + path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: body ? JSON.stringify(body) : "{}",
    }).then(async r => {
      if (!r.ok) {
        const txt = await r.text();
        throw new Error(txt || ("HTTP " + r.status));
      }
      return r.json();
    });
  }

  // ---- small format helpers reused above ------------------------------------
  function fmtNumber(n, decimals) {
    if (n == null || isNaN(n)) return "—";
    return Number(n).toLocaleString(undefined, {
      minimumFractionDigits: decimals,
      maximumFractionDigits: decimals,
    });
  }
  function formatHHMM(iso) {
    try { return new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }); }
    catch (e) { return iso; }
  }
  function formatHHMMSS(iso) {
    try { return new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" }); }
    catch (e) { return iso; }
  }
  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, c => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
    }[c]));
  }
  // light log shim
  const log = { info: (...a) => console.log("[bot]", ...a) };

  // ============================================================================
  // CANDLESTICK RENDERER + TRADE CHART (entry / SL / TP / exit overlays)
  // ============================================================================
  let _mt5ChartStyle = "candles";       // "candles" (default) | "line"
  let _agentOpenRaw = [];               // raw open OrderFills from /api/agent/trades
  let _agentClosedRaw = [];             // raw closed PositionUpdates

  // Accept bars from /api/chart ({t,o,h,l,c}) or the journal ({time,open,...}).
  function _normBar(b) {
    return {
      t: b.t || b.time || "",
      o: Number(b.o != null ? b.o : b.open),
      h: Number(b.h != null ? b.h : b.high),
      l: Number(b.l != null ? b.l : b.low),
      c: Number(b.c != null ? b.c : b.close),
    };
  }

  // Pure-canvas candlestick chart. overlays:
  //   entry, sl, slInit, tp, level : horizontal price lines
  //   entryTime                    : ISO — marks the entry bar (▲/▼)
  //   side                         : "buy" | "sell"
  //   exits: [{time, price, reason, pnl}]
  function drawCandleChart(canvas, rawBars, overlays, opts) {
    overlays = overlays || {}; opts = opts || {};
    const bars = (rawBars || []).map(_normBar).filter(b => !isNaN(b.o));
    // The MT5 EA pushes bars newest-first; overlays and time markers need
    // chronological order, so always sort oldest → newest.
    bars.sort((a, b) => a.t < b.t ? -1 : a.t > b.t ? 1 : 0);
    if (!canvas || !bars.length) return;

    const dpr = window.devicePixelRatio || 1;
    const box = canvas.parentElement.getBoundingClientRect();
    const W = Math.max(320, box.width - 4), H = Math.max(240, (canvas.style.height ? parseInt(canvas.style.height) : box.height) || 300);
    canvas.width = W * dpr; canvas.height = H * dpr;
    canvas.style.width = W + "px"; canvas.style.height = H + "px";
    const ctx = canvas.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, W, H);

    const padL = 8, padR = 64, padT = 26, padB = 22;
    const plotW = W - padL - padR, plotH = H - padT - padB;

    // Price extent includes every overlay line so SL/TP are always visible.
    let lo = Math.min(...bars.map(b => b.l)), hi = Math.max(...bars.map(b => b.h));
    ["entry", "sl", "slInit", "tp", "level"].forEach(k => {
      const v = Number(overlays[k]);
      if (v && !isNaN(v) && v > 0) { lo = Math.min(lo, v); hi = Math.max(hi, v); }
    });
    const span = (hi - lo) || 1; lo -= span * 0.05; hi += span * 0.05;
    const y = p => padT + (hi - p) / (hi - lo) * plotH;
    const n = bars.length;
    const step = plotW / n;
    const cw = Math.max(1, Math.min(11, step * 0.65));
    const x = i => padL + i * step + step / 2;

    // Grid + y labels
    ctx.font = "10px system-ui, sans-serif";
    ctx.strokeStyle = "#1f2a44"; ctx.fillStyle = "#64748b";
    for (let g = 0; g <= 4; g++) {
      const gy = padT + g * plotH / 4;
      ctx.beginPath(); ctx.moveTo(padL, gy); ctx.lineTo(W - padR, gy); ctx.stroke();
      const price = hi - g * (hi - lo) / 4;
      ctx.fillText(price.toFixed(_priceDecimals(opts.symbol, price)), W - padR + 6, gy + 3);
    }
    // x labels (≤8)
    const lbl = Math.max(1, Math.ceil(n / 8));
    for (let i = 0; i < n; i += lbl) {
      const d = new Date(bars[i].t);
      const txt = isNaN(d) ? "" : (opts.daily ? d.toLocaleDateString("en-GB", { month: "short", day: "numeric" })
                                              : d.toLocaleDateString("en-GB", { day: "numeric" }) + " " + d.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" }));
      ctx.fillText(txt, Math.min(x(i) - 14, W - padR - 60), H - 7);
    }

    // Candles
    for (let i = 0; i < n; i++) {
      const b = bars[i], up = b.c >= b.o;
      const col = up ? "#10b981" : "#ef4444";
      ctx.strokeStyle = col; ctx.fillStyle = col;
      ctx.beginPath(); ctx.moveTo(x(i), y(b.h)); ctx.lineTo(x(i), y(b.l)); ctx.stroke();
      const top = y(Math.max(b.o, b.c)), bot = y(Math.min(b.o, b.c));
      ctx.fillRect(x(i) - cw / 2, top, cw, Math.max(1, bot - top));
    }

    // Overlay lines
    function hline(price, color, dash, label) {
      const v = Number(price);
      if (!v || isNaN(v) || v <= 0) return;
      ctx.save();
      ctx.strokeStyle = color; ctx.setLineDash(dash); ctx.lineWidth = 1.2;
      ctx.beginPath(); ctx.moveTo(padL, y(v)); ctx.lineTo(W - padR, y(v)); ctx.stroke();
      ctx.setLineDash([]);
      ctx.fillStyle = color; ctx.font = "bold 10px system-ui, sans-serif";
      const txt = label + " " + v.toFixed(_priceDecimals(opts.symbol, v));
      const tw = ctx.measureText(txt).width + 8;
      ctx.globalAlpha = 0.92;
      ctx.fillRect(W - padR - tw - 4, y(v) - 8, tw, 14);
      ctx.fillStyle = "#0b1220"; ctx.fillText(txt, W - padR - tw, y(v) + 3);
      ctx.restore();
    }
    hline(overlays.level,  "#eab308", [2, 4], "LVL");
    hline(overlays.tp,     "#10b981", [6, 4], "TP");
    hline(overlays.sl,     "#ef4444", [6, 4], "SL");
    if (overlays.slInit && overlays.slInit !== overlays.sl)
      hline(overlays.slInit, "#f97316", [2, 4], "SL₀");
    hline(overlays.entry,  "#60a5fa", [], "ENTRY");

    // Index of the first bar at/after an ISO time.
    function idxAt(iso) {
      if (!iso) return -1;
      for (let i = 0; i < n; i++) if (bars[i].t >= iso) return i;
      return -1;
    }
    // Entry marker
    const ei = idxAt(overlays.entryTime);
    if (ei >= 0 && overlays.entry) {
      ctx.fillStyle = "#60a5fa"; ctx.font = "12px system-ui, sans-serif";
      const ey = y(Number(overlays.entry));
      ctx.fillText(overlays.side === "sell" ? "▼" : "▲", x(ei) - 5,
                   overlays.side === "sell" ? ey - 8 : ey + 16);
    }
    // Exit markers
    (overlays.exits || []).forEach(ex => {
      let i = idxAt(ex.time); if (i < 0) i = n - 1;
      const px = Number(ex.price); if (!px || isNaN(px)) return;
      const win = (Number(ex.pnl) || 0) >= 0;
      ctx.fillStyle = win ? "#10b981" : "#ef4444";
      ctx.font = "bold 11px system-ui, sans-serif";
      ctx.fillText("✕", x(i) - 4, y(px) + 4);
      ctx.font = "9px system-ui, sans-serif";
      ctx.fillText((ex.reason || "").slice(0, 12), x(i) - 14, y(px) - 7);
    });

    // Hover OHLC readout (replaces the line chart's tooltip — no info lost).
    canvas._candle = { bars, x0: padL, step, W, H, padT, padR, symbol: opts.symbol };
    if (!canvas._candleHoverWired) {
      canvas._candleHoverWired = true;
      canvas.addEventListener("mousemove", ev => {
        const c = canvas._candle; if (!c) return;
        const r = canvas.getBoundingClientRect();
        const i = Math.floor((ev.clientX - r.left - c.x0) / c.step);
        const el = canvas._ohlcEl || (canvas._ohlcEl = (() => {
          const d = document.createElement("div");
          d.style.cssText = "position:absolute;top:6px;left:12px;font:11px system-ui;color:#cbd5e1;background:rgba(11,18,32,.85);padding:3px 8px;border-radius:6px;pointer-events:none;";
          canvas.parentElement.style.position = "relative";
          canvas.parentElement.appendChild(d);
          return d;
        })());
        if (i >= 0 && i < c.bars.length) {
          const b = c.bars[i];
          const f = v => v.toFixed(_priceDecimals(c.symbol, v));
          el.textContent = `${new Date(b.t).toLocaleString("en-GB")}  O ${f(b.o)}  H ${f(b.h)}  L ${f(b.l)}  C ${f(b.c)}`;
          el.style.display = "block";
        } else el.style.display = "none";
      });
      canvas.addEventListener("mouseleave", () => {
        if (canvas._ohlcEl) canvas._ohlcEl.style.display = "none";
      });
    }
  }

  // ---------------- Trade chart modal ----------------
  function _tcEls() {
    return {
      modal: document.getElementById("trade-chart-modal"),
      canvas: document.getElementById("trade-chart-canvas"),
      title: document.getElementById("trade-chart-title"),
      sub: document.getElementById("trade-chart-sub"),
      clar: document.getElementById("trade-chart-clarity"),
      src: document.getElementById("trade-chart-src"),
      legend: document.getElementById("trade-chart-legend"),
      exits: document.getElementById("trade-chart-exits"),
      loading: document.getElementById("trade-chart-loading"),
    };
  }

  function _tcOpen(title, sub) {
    const e = _tcEls(); if (!e.modal) return null;
    e.modal.style.display = "flex";
    e.title.textContent = title; e.sub.textContent = sub || "";
    e.clar.style.display = "none"; e.src.textContent = "";
    e.legend.innerHTML = ""; e.exits.innerHTML = "";
    e.loading.style.display = "flex";
    const ctx = e.canvas.getContext("2d");
    ctx && ctx.clearRect(0, 0, e.canvas.width, e.canvas.height);
    return e;
  }

  function _tcLegend(e, overlays, extra) {
    const items = [];
    if (overlays.entry) items.push(`<span style="color:#60a5fa;">— Entry ${overlays.entry}</span>`);
    if (overlays.sl)    items.push(`<span style="color:#ef4444;">‑ ‑ SL ${overlays.sl}</span>`);
    if (overlays.slInit && overlays.slInit !== overlays.sl)
      items.push(`<span style="color:#f97316;">‑ ‑ Original SL ${overlays.slInit}</span>`);
    if (overlays.tp)    items.push(`<span style="color:#10b981;">‑ ‑ TP ${overlays.tp}</span>`);
    if (overlays.level) items.push(`<span style="color:#eab308;">· · Pattern level ${overlays.level}</span>`);
    if (extra) items.push(`<span>${extra}</span>`);
    e.legend.innerHTML = items.join("");
  }

  function _tcRender(e, bars, overlays, opts, srcLabel) {
    e.loading.style.display = "none";
    if (!bars || !bars.length) {
      e.loading.style.display = "flex";
      e.loading.textContent = "No bars available for this trade/timeframe.";
      return;
    }
    e.src.textContent = srcLabel;
    drawCandleChart(e.canvas, bars, overlays, opts);
  }

  // Closed trade — journal-backed (pattern bars + every trade bar + exits);
  // falls back to the recent live chart window if no journal record exists.
  window.openClosedTradeChart = function (rawIdx) {
    const t = _agentClosedRaw[rawIdx];
    if (!t) return;
    const e = _tcOpen(`${t.symbol} ${String(t.side || "").toUpperCase()} — ${t.setup}-${t.timeframe}`,
                      `closed ${t.close_time || ""} · ${t.close_reason || ""}`);
    if (!e) return;
    const overlays = {
      entry: t.entry, sl: t.sl, tp: t.tp, side: t.side, entryTime: t.entry_time,
      exits: [{ time: t.close_time, price: t.exit, reason: t.close_reason, pnl: t.pnl }],
    };
    fetch(BOT.baseUrl + "/api/journal/" + t.ticket)
      .then(r => { if (!r.ok) throw new Error("no journal"); return r.json(); })
      .then(rec => {
        const seen = {}, bars = [];
        (rec.pattern_bars || []).concat(rec.trade_bars || []).forEach(b => {
          const nb = _normBar(b);
          if (nb.t && !seen[nb.t]) { seen[nb.t] = 1; bars.push(nb); }
        });
        bars.sort((a, b2) => a.t < b2.t ? -1 : 1);
        overlays.exits = (rec.exits || []).map(x2 => ({
          time: x2.close_time, price: x2.exit, reason: x2.reason, pnl: x2.pnl }));
        overlays.level = (rec.signal || {}).pattern_level;
        const cl = (rec.signal || {}).clarity_score;
        if (cl && cl > 0) {
          e.clar.style.display = "inline-block";
          e.clar.textContent = "clarity " + Math.round(cl) + "/100";
        }
        _tcLegend(e, overlays, `${(rec.exits || []).length} exit leg(s) · net ${curSym()}${fmtNumber(rec.net_pnl, 2)}`);
        _tcRender(e, bars, overlays, { symbol: t.symbol, daily: t.timeframe === "1d" }, "journal");
        e.exits.innerHTML = (rec.exits || []).map(x2 =>
          `<span class="chip" style="margin-right:8px;">${x2.reason} @ ${x2.exit} → ${x2.pnl >= 0 ? "+" : ""}${fmtNumber(x2.pnl, 2)} (${(x2.rr || 0).toFixed(2)}R)</span>`).join("");
      })
      .catch(() => {
        // Legacy trade (pre-journal): best effort with the live bar window.
        fetch(BOT.baseUrl + "/api/chart/" + t.symbol + "?tf=" + (t.timeframe || "1h"))
          .then(r => r.json())
          .then(d => {
            _tcLegend(e, overlays, "⚠ no journal record — recent bars may not cover this trade");
            _tcRender(e, d.bars || [], overlays, { symbol: t.symbol, daily: t.timeframe === "1d" }, "live window");
          })
          .catch(() => { e.loading.textContent = "Could not load chart data."; });
      });
  };

  // Open position — live bars + entry / current SL / original SL / TP.
  window.openPositionChart = function (ticket) {
    const p = _agentOpenRaw.find(q => q.ticket === ticket) || _agentOpenRaw[ticket];
    if (!p) return;
    const e = _tcOpen(`${p.symbol} ${String(p.side || "").toUpperCase()} — ${p.setup}-${p.timeframe} (OPEN)`,
                      `filled ${p.fill_time || ""} · ${p.lots} lots` +
                      (p.risked_money ? ` · risked ${curSym()}${fmtNumber(p.risked_money, 2)}` : ""));
    if (!e) return;
    const overlays = {
      entry: p.fill_price, sl: p.sl, slInit: p.init_sl, tp: p.tp,
      side: p.side, entryTime: p.fill_time, exits: [],
    };
    fetch(BOT.baseUrl + "/api/chart/" + p.symbol + "?tf=" + (p.timeframe || "1h"))
      .then(r => r.json())
      .then(d => {
        _tcLegend(e, overlays, p.partial_done ? "50% booked — runner on trailed stop" : "");
        _tcRender(e, d.bars || [], overlays, { symbol: p.symbol, daily: p.timeframe === "1d" }, "MT5 live");
      })
      .catch(() => { e.loading.textContent = "Could not load chart data."; });
  };

  // ============================================================================
  // ANALYTICS PANELS — rejection funnel, clarity histogram, shadow outcomes
  // ============================================================================
  function _hbar(label, count, max, color) {
    const w = max > 0 ? Math.max(2, Math.round(count / max * 100)) : 0;
    return `<div style="display:flex;align-items:center;gap:8px;margin:4px 0;">
      <span style="flex:0 0 150px;font-size:12px;color:var(--text-dim);overflow:hidden;text-overflow:ellipsis;">${escapeHtml(label)}</span>
      <div style="flex:1;background:var(--bg-elev-2);border-radius:4px;height:14px;overflow:hidden;">
        <div style="width:${w}%;height:100%;background:${color};opacity:.7;"></div></div>
      <span style="flex:0 0 44px;text-align:right;font-size:12px;">${count}</span></div>`;
  }

  function refreshAnalytics() {
    if (typeof BOT === "undefined" || !BOT.connected) return;

    fetch(BOT.baseUrl + "/api/funnel?hours=24").then(r => r.json()).then(f => {
      const checksEl = document.getElementById("funnel-checks");
      if (checksEl) {
        const entries = Object.entries(f.by_check || {}).sort((a, b) => b[1] - a[1]);
        checksEl.innerHTML = entries.length
          ? entries.map(([k, v]) => _hbar(k, v, entries[0][1], "#eab308")).join("")
          : `<div class="mf-empty">No rejections in the window.</div>`;
      }
      const tot = document.getElementById("funnel-totals");
      if (tot) tot.textContent =
        `detected ${f.detected} · deduped ${f.deduped} · rejected ${f.rejected} · accepted ${f.accepted} · filled ${f.filled}`;
      const clarEl = document.getElementById("funnel-clarity");
      if (clarEl) {
        const order = ["0-19", "20-39", "40-59", "60-79", "80-99", "100-119"];
        const hist = f.clarity_hist || {};
        const ks = order.filter(k => hist[k]).concat(Object.keys(hist).filter(k => !order.includes(k)));
        const mx = Math.max(1, ...ks.map(k => hist[k]));
        clarEl.innerHTML = ks.length
          ? ks.map(k => _hbar("clarity " + k, hist[k], mx, "#60a5fa")).join("")
          : `<div class="mf-empty">No scored signals yet.</div>`;
      }
    }).catch(() => {});

    fetch(BOT.baseUrl + "/api/shadow").then(r => r.json()).then(s => {
      const el = document.getElementById("shadow-table");
      if (el) {
        const rows = Object.entries(s.by_check || {}).sort((a, b) => b[1].n - a[1].n);
        el.innerHTML = rows.length ? `
          <table class="trades" style="font-size:12px;width:100%;">
            <thead><tr><th>rejecting check</th><th class="num">n</th><th class="num">win%</th><th class="num">avg R</th></tr></thead>
            <tbody>${rows.map(([k, v]) => `
              <tr><td>${escapeHtml(k)}</td><td class="num">${v.n}</td>
              <td class="num">${v.win_pct}%</td>
              <td class="num" style="color:${v.avg_r > 0.2 ? "var(--red)" : v.avg_r < -0.2 ? "var(--green)" : "var(--text)"};">
                ${v.avg_r > 0 ? "+" : ""}${v.avg_r}</td></tr>`).join("")}
            </tbody></table>`
          : `<div class="mf-empty">No shadow outcomes resolved yet.</div>`;
      }
      const pe = document.getElementById("shadow-pending");
      if (pe) pe.textContent = `${s.pending || 0} rejected signal(s) still being tracked · ` +
        `overall: ${s.overall.n} resolved, win ${s.overall.win_pct}%, avg R ${s.overall.avg_r > 0 ? "+" : ""}${s.overall.avg_r}`;
    }).catch(() => {});

    // Pairs tab config-awareness: a pair toggled on here but missing from
    // config.yaml instruments cannot actually trade — badge it.
    fetch(BOT.baseUrl + "/api/status").then(r => r.json()).then(snap => {
      const inst = ((snap.config_summary || {}).instruments || []).map(s2 => s2.toUpperCase());
      if (!inst.length) return;
      document.querySelectorAll("[id^='pt-']").forEach(label => {
        const pair = label.id.slice(3).toUpperCase();
        const cb = label.querySelector("input[type=checkbox]");
        let badge = label.querySelector(".cfg-missing");
        const missing = cb && cb.checked && !inst.includes(pair);
        if (missing && !badge) {
          badge = document.createElement("span");
          badge.className = "cfg-missing";
          badge.textContent = "⚠ not in server config";
          badge.title = "Enabled here, but this instrument is not in config.yaml — the engine cannot scan it. Uncomment it in config.yaml and restart the server.";
          badge.style.cssText = "font-size:10px;color:var(--yellow);margin-left:6px;";
          const nameEl = label.querySelector(".pair-name");
          (nameEl || label).appendChild(badge);
        } else if (!missing && badge) {
          badge.remove();
        }
      });
    }).catch(() => {});
  }

  // Modal close wiring + analytics polling
  document.addEventListener("DOMContentLoaded", () => {
    const modal = document.getElementById("trade-chart-modal");
    const close = document.getElementById("trade-chart-close");
    if (close) close.addEventListener("click", () => { modal.style.display = "none"; });
    if (modal) modal.addEventListener("click", ev => {
      if (ev.target === modal) modal.style.display = "none";
    });
    setTimeout(refreshAnalytics, 2500);          // after bot client connects
    setInterval(refreshAnalytics, 30000);
  });

  // Expose MT5 chart function globally so inline onclick handlers can reach it
  window.openMT5Chart = openMT5Chart;
})();
