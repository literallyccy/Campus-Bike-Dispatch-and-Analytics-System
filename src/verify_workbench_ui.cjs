// Component regression with explicit synthetic fixtures; no live runtime writes.
process.chdir(require('path').resolve(__dirname,'..'));
const fs=require('fs'),vm=require('vm'),assert=require('assert/strict');
const checks=[],check=(name,fn)=>{fn();checks.push(name)};
const c={module:{exports:{}},structuredClone,clearInterval,choose(){},renderTask(){},applySnapshot(){},
 esc:s=>String(s??'').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('"','&quot;'),
 simTime:t=>`时间${t}`,whole:String,number:n=>Number(n).toFixed(1),shortageCount:Math.ceil,
 managerBusy:false,trialResult:null,trialDraftDirty:false,planDraft:null,historical:false,
 taskLabels:{confirmed:'待出发',transporting:'运输中'},liveSnapshot:{points:[]},
 document:{querySelectorAll(){return []},addEventListener(){},querySelector(){return null}},
 managerAction:async b=>({ok:true,body:b}),window:{confirm:()=>true,addEventListener(){}}};
vm.createContext(c);
for(const f of ['review.js','trial-player.js','decision-review.js','workbench.js'])vm.runInContext(fs.readFileSync('dashboard/v1/'+f,'utf8'),c);
const tasks=Array.from({length:4},(_,i)=>({id:'t'+i,status:'confirmed',origin_name:'供给'+i,destination_name:'接收'+i,destination:'n'+i,quantity:10,departure_s:100+i*100,finish_s:190+i*100}));
const m={state_version:5,clock_s:100,initial_clock_s:100,forecast_available:true,tasks,metrics:{potential:0},plan:{tasks:[],reason:'已有确认任务'}};
c.liveSnapshot.points=tasks.map((t,i)=>({id:'p'+i,node_id:t.destination,name:t.destination_name,status:'shortage',shortage:9-i,first_shortage_minute:30}));
check('all four risk locations remain discoverable',()=>{const h=c.reviewDiagnosis(m);for(let i=0;i<4;i++)assert(h.includes('data-review-location="p'+i+'"'));assert(h.includes('15—30'));});
check('confirmed plan shows all tasks and correct resource release',()=>{const h=c.reviewDraft(m);for(let i=0;i<4;i++)assert(h.includes('data-workbench-cancel="t'+i+'"'));assert(h.includes('时间490'));assert(!h.includes('data-decision-simulate'));assert(h.includes('没有该次确认的试算记录'));});
check('cancel-empty release uses pickup time',()=>assert(c.reviewTruckAvailability({...m,tasks:[{status:'cancel_empty',pickup_s:125,finish_s:900}]}).includes('时间125')));
check('empty execution has no misleading success rate or empty ranking',()=>{const h=c.reviewExecution(m);assert(h.includes('模拟尚未推进'));assert(!h.includes('未满足记录较多'));assert(!h.includes('%'));});
check('active plan in simulation has a direct return path',()=>assert(c.reviewTrial(m).includes('data-workbench-plan')));
check('completed and cancelled tasks release resources',()=>assert(c.reviewTruckAvailability({...m,tasks:[{status:'completed'},{status:'cancelled_delivered'}]}).includes('无任务占用')));
const row=(unmet)=>({potential:100,served:100-unmet,unmet,pending_walks:0,moved:10,transport_km:1,work_minutes:20});
c.trialResult={source_state_version:5,window_start_s:100,window_end_s:3700,potential_demands:100,results:{no_new_dispatch:row(10),current_plan:row(20)},proposed_tasks:tasks,location_effects:[]};
check('worse trial explicitly retains increased unmet demand',()=>{const h=c.reviewTrial(m);assert(h.includes('多 10 次未满足'));assert(h.includes('增加未满足'));assert(h.includes('全窗口未满足'));assert(h.includes('逐时刻库存'));});
check('changed draft rejects old trial even at same version',()=>{assert(c.trialFresh(m));c.trialDraftDirty=true;assert(!c.trialFresh(m));assert(/data-review-adopt disabled/.test(c.reviewTrial(m)));c.trialDraftDirty=false;});
check('clock version and forecast changes invalidate confirmation',()=>{for(const other of [{clock_s:101},{state_version:6},{forecast_available:false}])assert(!c.trialFresh({...m,...other}));});
check('cancel prompt explains cascade and decline makes no request',()=>{let prompt='',writes=0;c.window.confirm=s=>(prompt=s,false);c.managerAction=()=>writes++;c.reviewCancelTask(m,'t0');assert.equal(writes,0);assert(prompt.includes('另取消 3 项'));});
check('loaded cancellation explains safe delivery',()=>{let prompt='';c.window.confirm=s=>(prompt=s,false);c.reviewCancelTask({...m,tasks:[{...tasks[0],status:'transporting'}]},'t0');assert(prompt.includes('继续送达原目的地'));});
(async()=>{
 const t={module:{exports:{}},location:{search:''},URLSearchParams,liveSnapshot:{},open:0,browse:0,loadSnapshot:async()=>{}};vm.createContext(t);vm.runInContext(fs.readFileSync('dashboard/v1/time-browser.js','utf8'),t);t.openDispatchRun=()=>t.open++;t.browseLocalHour=()=>t.browse++;
 await t.startLocalHourView();check('default enters running instance rather than local-hour history',()=>{assert.equal(t.open,1);assert.equal(t.browse,0)});
 t.location.search='?hour=7';let selected;t.stopLivePolling=()=>{};t.pause=()=>{};t.showFrame=h=>selected=h;t.renderTimeBrowser=()=>{};await t.startLocalHourView();check('explicit historical hour deep link is preserved',()=>assert.equal(selected,7));
 fs.mkdirSync('outputs/workbench_ui',{recursive:true});fs.writeFileSync('outputs/workbench_ui/validation.json',JSON.stringify({passed:true,checks,fixture:'synthetic',limitations:['VM component and time-routing tests, not browser layout or backend acceptance','No live inventory or tasks mutated']},null,2));console.log(JSON.stringify({passed:true,checks},null,2));
})().catch(e=>{console.error(e);process.exitCode=1});
