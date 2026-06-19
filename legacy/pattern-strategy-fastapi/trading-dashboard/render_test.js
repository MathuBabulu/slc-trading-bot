const fs = require("fs"), path = require("path");
const { JSDOM } = require("jsdom");
const ROOT = "/sessions/vibrant-funny-newton/mnt/Trading Strategy/trading-dashboard";
const html  = fs.readFileSync(path.join(ROOT,"index.html"),"utf8");
const dataJs= fs.readFileSync(path.join(ROOT,"data.js"),"utf8");
const appJs = fs.readFileSync(path.join(ROOT,"app.js"),"utf8");

const errors = [];
const dom = new JSDOM(html, { url:"file://"+ROOT+"/", runScripts:"outside-only", pretendToBeVisual:true });
const win = dom.window;
win.onerror = (m)=>errors.push("window.onerror: "+m);
const origErr = console.error;
console.error = (...a)=>{ errors.push("console.error: "+a.join(" ")); };

// stubs the browser provides
win.fetch = () => Promise.resolve({ ok:false, json:()=>Promise.resolve({}), text:()=>Promise.resolve("") });
win.matchMedia = win.matchMedia || (()=>({matches:false,addEventListener(){},removeEventListener(){}}));
// Chart.js stub: records instantiation, no canvas needed
function Chart(c,cfg){ this.cfg=cfg; this.destroy=()=>{}; this.update=()=>{}; }
Chart.defaults = { font:{}, color:"" };
win.Chart = Chart;
// canvas getContext stub
win.HTMLCanvasElement.prototype.getContext = function(){ return { canvas:this }; };

try {
  win.eval(dataJs);          // load sample data
  win.eval(appJs);           // define IIFE + register DOMContentLoaded
  // fire DOMContentLoaded so the dashboard renders
  const ev = new win.Event("DOMContentLoaded",{bubbles:true});
  win.document.dispatchEvent(ev);
} catch(e){ errors.push("THROWN: "+(e.stack||e)); }

// Inspect rendered DOM
const kpis = win.document.querySelectorAll("#kpi-grid .kpi, #kpi-grid > *").length;
const tabs = win.document.querySelectorAll("[data-tab], .tab, nav button").length;
const charts = win.document.querySelectorAll("canvas").length;
const tradeRows = win.document.querySelectorAll("#trades-tbody tr, table tbody tr").length;

console.error = origErr;
console.log("KPI nodes rendered:", kpis);
console.log("Tab buttons:", tabs);
console.log("Canvas elements:", charts);
console.log("Trade rows rendered:", tradeRows);
console.log("Refresh-time text:", win.document.getElementById("refresh-time") ? win.document.getElementById("refresh-time").textContent : "(none)");
console.log("");
if (errors.length===0){ console.log("RESULT: ✓ No errors during load + full render"); process.exit(0); }
else { console.log("RESULT: ✗ "+errors.length+" error(s):"); errors.forEach(e=>console.log("  - "+e.slice(0,300))); process.exit(1); }
