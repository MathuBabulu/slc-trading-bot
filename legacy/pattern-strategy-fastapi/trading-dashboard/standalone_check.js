const fs=require("fs"); const {JSDOM}=require("jsdom");
const html=fs.readFileSync("/sessions/vibrant-funny-newton/mnt/outputs/dashboard_standalone.html","utf8");
const errors=[];
const dom=new JSDOM(html,{runScripts:"dangerously",pretendToBeVisual:true,resources:"usable",url:"file:///"});
const win=dom.window;
win.onerror=(m)=>errors.push("onerror: "+m);
const oe=console.error; console.error=(...a)=>errors.push("console.error: "+a.join(" "));
win.fetch=()=>Promise.resolve({ok:false,json:()=>Promise.resolve({}),text:()=>Promise.resolve("")});
win.HTMLCanvasElement.prototype.getContext=function(){return{canvas:this,fillRect(){},clearRect(){},getImageData:()=>({data:[]}),putImageData(){},createImageData:()=>([]),setTransform(){},drawImage(){},save(){},restore(){},beginPath(){},moveTo(){},lineTo(){},closePath(){},stroke(){},fill(){},arc(){},measureText:()=>({width:0}),fillText(){},scale(){},rotate(){},translate(){},transform(){},rect(){},clip(){}};};
// stub Chart (CDN won't load in jsdom)
function Chart(c,cfg){this.cfg=cfg;this.destroy=()=>{};this.update=()=>{};this.resize=()=>{};} Chart.defaults={font:{},color:""}; Chart.register=()=>{};
win.Chart=Chart;
setTimeout(()=>{
  try{ const e=new win.Event("DOMContentLoaded",{bubbles:true}); win.document.dispatchEvent(e); }catch(err){errors.push("THROWN: "+(err.stack||err));}
  const kpis=win.document.querySelectorAll("#kpi-grid .kpi").length;
  const rows=win.document.querySelectorAll("#trades-table tbody tr").length;
  const op=win.document.querySelectorAll("#open-positions-table tbody tr").length;
  console.error=oe;
  console.log("KPI cards:",kpis,"| Trade rows:",rows,"| Open-position rows:",op);
  if(errors.length===0){console.log("✓ standalone loads error-free");process.exit(0);}
  console.log("✗ errors:");errors.forEach(e=>console.log("  -",e.slice(0,200)));process.exit(1);
},300);
