// Read-only historical replay. Live stock events stay in their own stream.
let historical=false,liveSnapshot,timeline,timelineIndex=8,playTimer,isGenerating=false,sliderFrame;
function displayStatus(p){return !p?'unmapped':historical?(p.previous_unmet>=5?'shortage':p.inventory>=15?'surplus':'normal'):p.status;}
function displayLabel(p){if(!p)return '尚未接入单车数据';if(historical)return p.previous_unmet>0?'上小时未满足 '+whole(p.previous_unmet)+' 次':p.inventory>=15?'当前库存较多 · '+whole(p.inventory)+' 辆':'上小时无未满足记录';if(p.status==='unavailable')return '当前风险计算不可用';return p.status==='shortage'?'预计缺车 '+whole(shortageCount(p.shortage))+' 辆':p.status==='surplus'?'预计富余 '+whole(surplusCount(p.spare))+' 辆':'正常 / 轻微偏差';}
function stopPlayback(){clearInterval(playTimer);playTimer=null;if(sliderFrame){cancelAnimationFrame(sliderFrame);sliderFrame=null;}$('[data-time-play]').textContent='播放一天';}
function renderTimeline(){
  $('[data-time-controls]').hidden=!historical;$('[data-replay]').hidden=historical||$('[data-map-view]').hidden;
  $('[data-mode]').value=historical?'history':'live';
  $('[data-legend-normal]').textContent=historical?'未达突出阈值':'正常 / 轻微偏差';
  $('[data-legend-shortage]').textContent=historical?'上小时未满足≥5次':'明显缺车';
  $('[data-legend-surplus]').textContent=historical?'当前库存≥15辆':'明显富余';
  const note=$('[data-period-detail]');note.hidden=!historical;
  $('[data-day-history]').hidden=!historical||!reading(selected);
  const current=reading(selected);$('[data-mobile-selection]').textContent=current?current.name+' · 当前'+whole(current.inventory)+'辆 · 查看详情':'查看选中建筑详情';
  $('.assumption').textContent=historical?'逐时库存及借车失败来自无调度校园模拟。借还预测来自冻结模型在对应时刻的留出结果，仅用此前订单。历史失败不作为预测输入；回放不执行调度。':'直接使用官方建筑描边表示关联停车点，未实测停车占地。预测来自固定历史窗口的MLlib模型，新事件更新库存和风险。';
  $('[data-borrow-label]').textContent=historical?'下小时借出预测':'窗口借出预测';
  $('[data-return-label]').textContent=historical?'下小时归还预测':'窗口归还预测';
  if(!historical)return;
  $('[data-connection]').textContent='历史回放已就绪';$('[data-connection]').dataset.ok='true';
  const t={...data.temporal,previous_label:(timelineIndex===0?'前日':'')+data.temporal.previous_label},p=reading(selected);$('[data-advice]').textContent=p?'未满足次数来自上一小时模拟真值；候选任务依据下一小时借还预测单独计算。':'此建筑尚无单车数据。';$('[data-time]').value=timelineIndex;$('[data-time-select]').value=String(timelineIndex);
  $('[data-time-prev]').disabled=timelineIndex===0;$('[data-time-next]').disabled=timelineIndex===23;
  $('[data-time-summary]').textContent=`第${Math.floor(data.metadata.reference_time_s/86400)+1}日 ${t.label} 库存 · ${t.previous_label}未满足 ${whole(t.previous_unmet)} 次，涉及 ${t.affected_nodes} 个点（含未定位点）`;
  note.textContent=p?`${t.previous_label}：借出 ${p.previous_borrows} 次，归还 ${p.previous_returns} 次，最终未满足 ${p.previous_unmet} 次。`:'该建筑尚未关联回放数据。';
  $('[data-count]').textContent='无调度校园模拟 · 逐时历史回放';$('[data-updated]').textContent='当前库存：'+t.label;
  $('[data-latest]').textContent='历史回放不写入实时库存。下方借还预测对应所选时刻之后一小时。';
  renderDayHistory(p);
  $('[data-footer-scope]').textContent='红色为上一小时模拟借车失败，蓝色仅表示当前库存较多；不等于已验证的现实缺车或可搬运量。';
  $('[data-window]').textContent='历史回放：'+data.metadata.reference_time_label;
  $('[data-engine]').textContent='Spark SQL逐时汇总 · 已保存的MLlib滚动预测';
  const box=$('[data-hotspots]');box.replaceChildren();
  const points=data.points.filter(p=>p.previous_unmet>=5&&p.polygon).sort((a,b)=>b.previous_unmet-a.previous_unmet||a.id.localeCompare(b.id)).slice(0,3);
  if(!points.length){box.textContent='此时段没有达到突出阈值的已定位点。';return;}
  for(const p of points){const button=document.createElement('button');button.textContent=p.name+' · '+p.previous_unmet+'次';button.addEventListener('click',()=>{choose(p.id);focus(p.id);});box.append(button);}
}
function renderDayHistory(p){
  const box=$('[data-day-history]');if(!p){box.replaceChildren();return;}
  const stocks=timeline.frames.map(f=>f.points.find(q=>q.id===p.id));
  const high=Math.max(1,...stocks.map(q=>q.inventory)),x=i=>24+i/23*228,y=v=>14+(1-v/high)*76;
  const trace=stocks.map((q,i)=>x(i)+','+y(q.inventory)).join(' ');
  box.innerHTML=`<h3>全天历史库存</h3><svg viewBox="0 0 276 116" role="img" aria-label="${esc(p.name)}全天模拟库存，最高${high}辆，当前${p.inventory}辆"><line x1="24" x2="252" y1="90" y2="90" stroke="#b8c0ca"/><polyline points="${trace}" fill="none" stroke="#075bc1" stroke-width="2"/><line x1="${x(timelineIndex)}" x2="${x(timelineIndex)}" y1="12" y2="92" stroke="#54606e" stroke-dasharray="3 3"/>${stocks.map((q,i)=>q.previous_unmet>=5?`<circle cx="${x(i)}" cy="${y(q.inventory)}" r="3" fill="#b4232f"/>`:'').join('')}<text x="0" y="18">${high}</text><text x="8" y="93">0</text>${[0,6,12,18,23].map(i=>`<text x="${x(i)-8}" y="110">${i}时</text>`).join('')}</svg><p>蓝线为模拟库存，红点表示上一小时未满足≥5次；竖线为所选时刻。完整日历史仅供回顾。</p>`;
}
function showFrame(index){if(!timeline)return;timelineIndex=Math.max(0,Math.min(23,Number(index)));historical=true;applySnapshot(timeline.frames[timelineIndex]);}
function replayHour(search){const hour=new URLSearchParams(search).get('hour');return /^(?:\d|1\d|2[0-3])$/.test(hour??'')?Number(hour):8;}
async function loadTimeline(){
  if(location.protocol==='file:')return;
  const controller=new AbortController(),deadline=setTimeout(()=>controller.abort(),10000);
  try{const response=await fetch('/api/replay',{cache:'no-store',signal:controller.signal});if(!response.ok)throw new Error('时段数据未就绪');timeline=await response.json();if(timeline.frames?.length!==24)throw new Error('时段数据不完整');await loadBuildings(controller.signal);$('[data-mode] option[value="history"]').disabled=false;if(typeof startLocalHourView==='function'){await startLocalHourView();return;}if(!new URLSearchParams(location.search).has('hour')){startLivePolling();return;}pause();showFrame(replayHour(location.search));const hotspot=data.points.filter(p=>p.polygon&&p.previous_unmet>=5).sort((a,b)=>b.previous_unmet-a.previous_unmet)[0];if(hotspot){choose(hotspot.id);focus(hotspot.id);}}
  catch(error){$('[data-time-error]').hidden=false;$('[data-time-error]').textContent='时段回放暂不可用，已切换实时库存并自动重试。';historical=false;startLivePolling();}
  finally{clearTimeout(deadline);$('[data-mode]').disabled=false;if(typeof renderTimeBrowser==='function')renderTimeBrowser();}
}
$('[data-mode]').addEventListener('change',e=>{stopPlayback();pause();stopLivePolling();if(e.target.value==='history'){showFrame(timelineIndex);}else{historical=false;if(liveSnapshot)applySnapshot(liveSnapshot);startLivePolling();}});
$('[data-time]').addEventListener('input',e=>{stopPlayback();const target=e.target.value;sliderFrame=requestAnimationFrame(()=>{sliderFrame=null;showFrame(target);});});
$('[data-time-prev]').addEventListener('click',()=>{stopPlayback();showFrame(timelineIndex-1);});
$('[data-time-next]').addEventListener('click',()=>{stopPlayback();showFrame(timelineIndex+1);});
$('[data-time-select]').addEventListener('change',e=>{stopPlayback();showFrame(e.target.value);});
$('[data-mobile-selection]').addEventListener('click',()=>{$('aside').scrollIntoView({block:'start',behavior:'auto'});$('[data-name]').focus({preventScroll:true});});
$('[data-mobile-back]').addEventListener('click',()=>{$('.map-panel').scrollIntoView({block:'start',behavior:'auto'});$('.campus').focus({preventScroll:true});});
$('[data-time-play]').addEventListener('click',()=>{if(playTimer){stopPlayback();return;}if(timelineIndex===23)showFrame(0);$('[data-time-play]').textContent='暂停播放';playTimer=setInterval(()=>{if(timelineIndex===23){stopPlayback();return;}showFrame(timelineIndex+1);},1800);});
$('[data-search]').addEventListener('input',e=>{if(!data)return;const query=e.target.value.trim().toLowerCase();const picker=$('[data-point-picker]');const rows=data.points.filter(p=>(p.name+' '+p.node_id).toLowerCase().includes(query));picker.replaceChildren(new Option(rows.length?'选择地点（'+rows.length+'）':'没有匹配地点',''),...rows.map(p=>new Option(p.name+(p.polygon?'':' · 未定位'),p.id)));picker.value=rows.some(p=>p.id===selected)?selected:'';});
document.addEventListener('visibilitychange',()=>{if(document.hidden){stopPlayback();pause();stopLivePolling();}else if(initialized&&!historical)startLivePolling();});
connectData();
loadTimeline();
