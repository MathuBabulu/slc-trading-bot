// Verification harness for the in-browser MT5 report parser.
// Runs the parser code from app.js inside a jsdom window so we can confirm
// it extracts trades correctly before the user feeds it a real MT5 report.

const fs = require("fs");
const path = require("path");
const { JSDOM } = require("jsdom");

const ROOT = __dirname;
const fixture = fs.readFileSync(path.join(ROOT, "test_fixture.html"), "utf8");
const appJs   = fs.readFileSync(path.join(ROOT, "app.js"), "utf8");

// Create a jsdom window that provides DOMParser, document, etc.
const dom = new JSDOM(`<!DOCTYPE html><html><body></body></html>`, {
  url: "file://" + ROOT + "/",
  runScripts: "outside-only",
});
const win = dom.window;

// We need to call the parser functions, which live inside an IIFE in app.js.
// Inject a small probe BEFORE the IIFE body's closing line so the parsers
// (and helpers) are exposed on window for the test.
const probedSrc = appJs.replace(
  /(\}\)\s*\(\s*\)\s*;\s*)\s*$/,
  `
  window.__parsers = {
    parseMT5HtmlReport,
    parseMT5CsvReport,
    parseSetupTag,
    scoreAdherence,
    parseMT5Date,
    extractAccountInfo,
  };
$1`
);

if (probedSrc === appJs) {
  console.error("FAIL: could not inject probe — IIFE closing not found.");
  process.exit(2);
}

// Stub out things the IIFE touches at top-level that don't exist in jsdom.
win.tradeData = null;
win.Chart = undefined;
// app.js calls fetch() at load (syncPairsToServer) to reach the optional
// trading-bot server. jsdom has no fetch; a real browser does. Stub it as a
// no-op resolving promise so the parser code under test can load cleanly.
win.fetch = () => Promise.resolve({ ok: false, json: () => Promise.resolve({}) });

// Execute the modified app.js inside the window context.
try {
  win.eval(probedSrc);
} catch (err) {
  console.error("Error executing app.js inside jsdom window:", err);
  process.exit(2);
}

const P = win.__parsers;
if (!P || typeof P.parseMT5HtmlReport !== "function") {
  console.error("FAIL: __parsers not exposed");
  process.exit(2);
}

// -------------------------------------------------------------------------
// Run the actual parse against the test fixture.
// -------------------------------------------------------------------------
const result = P.parseMT5HtmlReport(fixture);

const expected = [
  { symbol: "GBPUSD", side: "sell", setup: "HS",  timeframe: "H1",  category: "LIVE",     pnl: 86.00 },   // 87.50 - 2.00 + 0.50
  { symbol: "XAUUSD", side: "buy",  setup: "DB",  timeframe: "H1",  category: "LIVE",     pnl: 67.50 },   // 68.50 - 1.00 + 0
  { symbol: "USDCAD", side: "sell", setup: "DT",  timeframe: "H4",  category: "LIVE",     pnl: -60.00 },  // -58.20 - 3.00 + 1.20
  { symbol: "USOIL",  side: "buy",  setup: "REC", timeframe: "H1",  category: "TELEGRAM", pnl: 53.50 },   // 55.00 - 1.50 + 0
  { symbol: "EURUSD", side: "buy",  setup: "IHS", timeframe: "M15", category: "LIVE",     pnl: 46.00 },   // 48.00 - 2.00 + 0
];

let failures = 0;
function check(label, actual, want) {
  const ok = Math.abs((actual ?? 0) - want) < 0.01 || actual === want;
  console.log(`  ${ok ? "OK " : "XX "} ${label}: got ${JSON.stringify(actual)}, expected ${JSON.stringify(want)}`);
  if (!ok) failures++;
}

console.log("=".repeat(72));
console.log("MT5 HTML PARSER TEST — running parseMT5HtmlReport against fixture");
console.log("=".repeat(72));
console.log();
console.log(`Account:    ${JSON.stringify(result.account)}`);
console.log(`Source:     ${result._source}`);
console.log(`Trade count: ${result.trades.length} (expected ${expected.length})`);
console.log();

check("trade count", result.trades.length, expected.length);

result.trades.forEach((t, i) => {
  const want = expected[i];
  if (!want) return;
  console.log(`\nTrade ${i + 1}:`);
  check(`  symbol`,    t.symbol,    want.symbol);
  check(`  side`,      t.side,      want.side);
  check(`  setup`,     t.setup,     want.setup);
  check(`  timeframe`, t.timeframe, want.timeframe);
  check(`  category`,  t.category,  want.category);
  check(`  pnl`,       t.pnl,       want.pnl);
});

// Spot-check adherence: trade 1 (HS sell, H1, 1:2 R:R) should be 100; trade 5
// (IHS buy, M15, ~2.13:1 R:R) should also be 100.
console.log("\nAdherence spot checks:");
const t1 = result.trades[0];
const t5 = result.trades[4];
check("trade1 adherence.score", t1.adherence.score, 100);
check("trade5 adherence.score", t5.adherence.score, 100);

// Account info spot check
check("account.balance", result.account.balance, 10250.50);
check("account.currency", result.account.currency, "USD");
check("account.login", result.account.login, 12345678);

console.log("\n" + "=".repeat(72));
if (failures === 0) {
  console.log("PASSED — parser extracts all 5 fixture trades correctly.");
  process.exit(0);
} else {
  console.log(`FAILED — ${failures} assertion(s) did not match.`);
  process.exit(1);
}
