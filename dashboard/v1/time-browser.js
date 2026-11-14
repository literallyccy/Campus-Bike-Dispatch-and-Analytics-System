// Local wall-clock selects a saved scenario hour; it never executes live tasks.
const LocalTimeView={hour:date=>date.getHours(),selected(search,date){const h=new URLSearchParams(search).get('hour');return /^(?:\d|1\d|2[0-3])$/.test(h??'')?Number(h):this.hour(date);}};
if(typeof module!=='undefined')module.exports=LocalTimeView;
let followingLocalHour=false;
function localClock(){return new Date().toLocaleTimeString('zh-CN',{hour:'2-digit',minute:'2-digit',hour12:false});}
function setBrowseUrl(hour,running=false){const u=new URL(location.href);u.searchParams.delete('hour');u.searchParams.delete('view');if(hour!=null)u.searchParams.set('hour',String(hour));if(running)u.searchParams.set('view','running');history.replaceState(null,'',u.pathname+u.search+u.hash);}
function browseLocalHour(){if(!timeline)return;stopPlayback();pause();stopLivePolling();followingLocalHour=true;setBrowseUrl(null);showFrame(LocalTimeView.hour(new Date()));renderTimeBrowser();}
function openDispatchRun(){followingLocalHour=false;stopPlayback();pause();stopLivePolling();historical=false;setBrowseUrl(null,true);if(liveSnapshot)applySnapshot(liveSnapshot);startLivePolling();renderTimeBrowser();}
function renderTimeBrowser(){
  const head=$('.map-head');if(!head)return;let controls=head.querySelector('[data-time-browser]');
  if(!controls){
    controls=document.createElement('div');controls.className='time-browser';controls.dataset.timeBrowser='';
    controls.innerHTML='<button data-return-current type="button">回到此刻</button><label><span class="time-browser-label">时间范围</span><select data-browse-hour aria-label="查看校园时刻"><option value="now">电脑时间对应的历史参照</option><option value="running">调度运行</option><optgroup data-browse-hours label="全天无调度参照"></optgroup></select></label>';
    const group=controls.querySelector('optgroup');for(let h=0;h<24;h++)group.append(new Option(String(h).padStart(2,'0')+':00',String(h)));
    head.append(controls);$('[data-mode]').hidden=true;
    const note=document.createElement('p');note.className='time-browse-note';note.dataset.browseContext='';note.setAttribute('role','status');note.hidden=true;head.after(note);
    controls.querySelector('[data-return-current]').onclick=openDispatchRun;
    controls.querySelector('select').onchange=e=>{
      if(e.target.value==='now'){browseLocalHour();return;}if(e.target.value==='running'){openDispatchRun();return;}if(!timeline)return;
      followingLocalHour=false;stopPlayback();pause();stopLivePolling();showFrame(Number(e.target.value));setBrowseUrl(timelineIndex);
    };
  }
  const button=controls.querySelector('[data-return-current]');button.textContent=historical?'返回当前调度':'当前调度实例';button.setAttribute('aria-pressed',String(!historical));button.disabled=!liveSnapshot;
  const select=controls.querySelector('select');select.value=historical?(followingLocalHour?'now':String(timelineIndex)):'running';select.querySelector('optgroup').disabled=!timeline;
  const clock=liveSnapshot?.manager?.clock_s;select.querySelector('[value="running"]').textContent='调度实例'+(Number.isFinite(clock)?' · '+simTime(clock).split(' ')[1]:'');
  const first=timeline?.frames?.[0]?.metadata?.reference_time_s;if(Number.isFinite(first))select.querySelector('optgroup').label=`第${Math.floor(first/86400)+1}日 · 无调度全天参照`;
  const note=$('[data-browse-context]');note.hidden=!historical;
  if(historical)note.textContent=followingLocalHour?`按电脑当地时间 ${localClock()}，显示 ${String(timelineIndex).padStart(2,'0')}:00 的校园模拟参照。只浏览，不执行调运任务。`:`正在查看 ${data.metadata.reference_time_label} 的无调度参照。可前后调整，或返回当前调度实例。`;
  $('[data-time-select]').hidden=true;
}
async function startLocalHourView(){
  if(!liveSnapshot)await loadSnapshot();
  const params=new URLSearchParams(location.search);
  if(params.get('view')==='running'){openDispatchRun();return;}
  if(params.has('hour')){followingLocalHour=false;stopLivePolling();pause();showFrame(LocalTimeView.selected(location.search,new Date()));renderTimeBrowser();return;}
  openDispatchRun();
}
if(typeof document!=='undefined'){
const beforeTimeBrowserTimeline=renderTimeline;
renderTimeline=function(){beforeTimeBrowserTimeline();renderTimeBrowser();};
for(const [selector,event] of [['[data-time]','input'],['[data-time-prev]','click'],['[data-time-next]','click'],['[data-time-play]','click'],['[data-time-select]','change']])$(selector).addEventListener(event,()=>{followingLocalHour=false;requestAnimationFrame(renderTimeBrowser);},true);
const beforeLocalReviewBack=reviewBack;
reviewBack=function(point){followingLocalHour=false;setBrowseUrl(null,true);beforeLocalReviewBack(point);renderTimeBrowser();};
setInterval(()=>{if(followingLocalHour&&timeline&&historical&&!document.hidden){const hour=LocalTimeView.hour(new Date());if(timelineIndex!==hour)showFrame(hour);renderTimeBrowser();}},30000);
document.addEventListener('visibilitychange',()=>{if(!document.hidden&&followingLocalHour&&timeline)browseLocalHour();});
function returnToCurrent(){openDispatchRun();}
}
