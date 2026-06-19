const fs=require("fs"); const {JSDOM}=require("jsdom");
const full=fs.readFileSync("/sessions/vibrant-funny-newton/mnt/outputs/dashboard_standalone.html","utf8");
// strip the two inline <script> blocks to run them manually exactly once
const errors=[];
const dom=new JSDOM(full,{runScripts:"outside-only",pretendToBeVisual:true,url:"file:///"});
const win=dom.window;
win.onerror=(m)=>errors.push("onerror: "+m);
const oe=console.error; console.error=(...a)=>errors.push("console.error: "+a.join(" "));
// real-browser globals jsdom lacks:
win.ResizeObserver=class{observe(){}unobserve(){}disconnect(){}};
win.fetch=()=>Promise.resolve({ok:false,json:()=>Promise.resolve({}),text:()=>Promise.resolve("")});
win.HTMLCanvasElement.prototype.getContext=function(){return{canvas:this};};
function Chart(c,cfg){this.cfg=cfg;this.destroy=()=>{};this.update=()=>{};this.resize=()=>{};} Chart.defaults={font:{},color:""}; Chart.register=()=>{};
win.Chart=Chart;
// run each inline script body once, in order
const scripts=[...win.document.querySelectorAll("script")].filter(s=>!s.src);
try{ scripts.forEach(s=>win.eval(s.textContent)); win.document.dispatchEvent(new win.Event("DOMContentLoaded",{bubbles:true})); }
catch(err){ errors.push("THROWN: "+(err.stack||err)); }
const kpis=win.document.querySelectorAll("#kpi-grid .kpi").length;
const rows=win.document.querySelectorAll("#trades-table tbody tr").length;
const op=win.document.querySelectorAll("#open-positions-table tbody tr").length;
console.error=oe;
console.log("KPI cards:",kpis,"| Trade rows:",rows,"| Open-position rows:",op);
if(errors.length===0){console.log("✓ standalone single-load: zero errors");process.exit(0);}
console.log("✗ "+errors.length+" error(s):");errors.forEach(e=>console.log("  -",e.slice(0,200)));process.exit(1);
