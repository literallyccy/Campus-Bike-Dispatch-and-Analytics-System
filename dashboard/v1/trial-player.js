// Read-only playback of the two isolated simulator branches. No live writes.
let trialFrame=0,trialTimer=null,trialStrategy='current_plan',trialPoint=null;
function stopTrialPlayback(){clearInterval(trialTimer);trialTimer=null;const b=document.querySelector('[data-trial-play]');if(b){b.textContent='播放模拟';b.setAttribute('aria-label','播放方案模拟');}}
function trialFresh(m){return !trialDraftDirty&&m&&trialResult&&trialResult.source_state_version===m.state_version&&trialResult.window_start_s===m.clock_s&&m.forecast_available;}
function trialShortTime(t){return simTime(t).split(' ')[1];}
reviewTrial=function(m){
  const t=trialResult,plan=m?.plan?.tasks||[];
  if(!t){
    const ready=plan.length&&m.forecast_available;
    return `<div class="trial-empty"><h3>${ready?'待模拟方案':'当前没有待模拟的调运方案'}</h3><p>${ready?`${plan.length} 项任务，共搬运 ${plan.reduce((a,p)=>a+p.quantity,0)} 辆。使用同一批模拟需求，对比采用方案与不新增调运。`:esc(m?.plan?.reason||'请先在地图查看当前风险与调运任务。')}</p>${ready?`<div class="trial-plan-preview">${plan.map(p=>`<p>${esc(p.origin_name)} <span>至</span> ${esc(p.destination_name)} <strong>${p.quantity} 辆</strong></p>`).join('')}</div><button class="primary" data-review-run ${managerBusy?'disabled':''}>${managerBusy?'正在模拟…':'模拟未来60分钟'}</button>`:'<button class="primary" data-review-map>查看地图与任务</button>'}<p class="trial-empty-note">隔离模拟，不改变当前库存或任务。</p></div>`;
  }
  const b=t.results.no_new_dispatch,p=t.results.current_plan,v=ReviewMath.verdict(b,p),cost=ReviewMath.extraCost(b,p);
  const ready=!!t.playback?.current_plan?.length,fresh=trialFresh(m);
  const comparisonMax=Math.max(1,b.unmet,p.unmet);
  const pp=b.potential&&p.potential?((p.served/p.potential-b.served/b.potential)*100):null;
  return `<div class="trial-summary ${v.tone}"><div><h3>${esc(v.text)}</h3><p>${trialShortTime(t.window_start_s)}—${trialShortTime(t.window_end_s)} · 同一批 ${whole(t.potential_demands)} 次模拟需求</p></div><div class="trial-summary-facts"><span>满足率变化 <strong>${pp==null?'—':(pp>0?'+':'')+pp.toFixed(1)+' 个百分点'}</strong></span><span>额外搬运 <strong>${whole(cost.moved)} 辆次</strong></span></div></div>
    ${!fresh?'<p class="review-status">草稿或运行状态已变化，或预测不可用。这是保存的方案模拟，请重新试算后再确认。</p>':''}
    <section class="trial-outcome-compare" aria-label="全窗口未满足次数对比"><h3>全窗口未满足次数</h3><div><span>不新增调运</span><span class="trial-outcome-track"><i style="width:${b.unmet/comparisonMax*100}%"></i></span><strong>${b.unmet} 次</strong></div><div><span>采用方案</span><span class="trial-outcome-track"><i class="${p.unmet>b.unmet?'worse':'plan'}" style="width:${p.unmet/comparisonMax*100}%"></i></span><strong>${p.unmet} 次</strong></div><p>${trialShortTime(t.window_start_s)}—${trialShortTime(t.window_end_s)} · 全窗口累计${b.pending_walks||p.pending_walks?' · 含待结算需求，结果未最终结算':''}</p></section>
    <details class="trial-playback"><summary>查看逐时刻库存 · 与全窗口结果分开</summary><div class="trial-stage">${ready?`<div class="trial-stage-head"><div><h3>库存变化模拟</h3><p>可借库存 · 按模拟时刻查看</p></div><div class="trial-strategies" role="group" aria-label="模拟策略"><button data-trial-strategy="current_plan" aria-pressed="${trialStrategy==='current_plan'}">采用方案</button><button data-trial-strategy="no_new_dispatch" aria-pressed="${trialStrategy==='no_new_dispatch'}">不新增调运</button></div></div><div class="trial-map-wrap"><svg data-trial-map viewBox="70 180 1730 1200" role="group" aria-label="方案模拟校园地图"></svg><div class="trial-map-key"><i></i>此时段已发生未满足 · 正常地点透明</div></div><div class="trial-player"><button class="trial-play" data-trial-play aria-label="播放方案模拟">播放模拟</button><input type="range" data-trial-time min="0" max="${t.playback.current_plan.length-1}" value="${Math.min(trialFrame,t.playback.current_plan.length-1)}" aria-label="方案模拟时刻"><output data-trial-clock></output></div><p class="trial-progress" data-trial-progress aria-live="polite"></p><div class="trial-point" data-trial-point></div>`:'<p>此份旧试算没有时间过程，请返回地图重新模拟。</p>'}</div></details>
    <div class="trial-efficiency">${v.tone==='benefit'?`减少 ${b.unmet-p.unmet} 次未满足，额外作业 ${number(cost.work_minutes)} 分钟${cost.work_minutes>0?` · 每减少一次约 ${number(cost.work_minutes/(b.unmet-p.unmet))} 分钟`:''}`:v.tone==='loss'?'该方案增加未满足次数；请核查变差地点或返回修改。':'目前没有已确定的服务改善；结合投入判断是否值得执行。'}</div><div class="trial-decision"><p>${fresh?'确认后预留车辆。':'这份结果仅供回看。'}<span>额外运输 ${number(cost.transport_km)} 公里 · 作业 ${number(cost.work_minutes)} 分钟</span></p><button class="primary" data-review-adopt ${fresh&&t.proposed_tasks?.length&&!managerBusy&&!historical?'':'disabled'}>确认调运方案</button><button class="trial-text-button" data-review-map>返回地图调整</button></div>
    <details class="trial-more"><summary>查看地点变化与完整对比</summary><div class="review-split"><section><h3>地点效果对比</h3>${reviewLocations(t.location_effects||[],true)}</section><section><h3>整个模拟时段的借车结果</h3>${reviewOutcome(b,'不新增调运')}${reviewOutcome(p,'采用方案')}</section></div><p class="review-muted">${b.pending_walks||p.pending_walks?'仍有需求等待结果，满足率尚未最终结算。':'两种策略各自生成成功骑行和归还。'}已有确认任务在两种策略中均保留；地图表示此段累计已发生的模拟未满足，不是未来风险预测。</p></details>`;
};
function drawTrialMap(root){
  const map=root.querySelector('[data-trial-map]');if(!map)return;
  map.innerHTML=`<image href="${esc(liveSnapshot.background)}" width="1920" height="1450" preserveAspectRatio="none"/>`;
  const a=point([detailBBox[0],detailBBox[3]]),b=point([detailBBox[2],detailBBox[1]]);
  const detail=document.createElementNS(NS,'image');for(const [k,v] of Object.entries({href:'/assets/campus-four-points.png',x:a[0],y:a[1],width:b[0]-a[0],height:b[1]-a[1],preserveAspectRatio:'none'}))detail.setAttribute(k,v);map.append(detail);
  const involved=new Set(trialResult.proposed_tasks.flatMap(t=>[t.origin_parking,t.destination_parking]));
  const coords=officialLayer.features.filter(f=>involved.has(f.id)).flatMap(f=>f.polygon.map(p=>point(p)));
  if(coords.length){const xs=coords.map(p=>p[0]),ys=coords.map(p=>p[1]),x=Math.min(...xs)-180,y=Math.min(...ys)-160,w=Math.max(...xs)-x+180,h=Math.max(...ys)-y+160;map.setAttribute('viewBox',[x,y,Math.max(w,650),Math.max(h,480)].join(' '));}
  for(const feature of officialLayer.features){
    const poly=document.createElementNS(NS,'polygon');poly.setAttribute('points',feature.polygon.map(p=>point(p).join(',')).join(' '));
    poly.dataset.trialNode=feature.node_id||liveSnapshot.points.find(p=>p.id===feature.id)?.node_id;poly.dataset.pointId=feature.id;
    poly.setAttribute('role','button');poly.setAttribute('tabindex','0');poly.setAttribute('aria-label',feature.name);poly.setAttribute('class','trial-building');
    poly.onclick=()=>{trialPoint=poly.dataset.trialNode;updateTrialFrame(root);};
    poly.onkeydown=e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();poly.click();}};map.append(poly);
  }
}
function updateTrialFrame(root){
  const t=trialResult;if(!t?.playback)return;
  const frames=t.playback[trialStrategy];trialFrame=Math.min(trialFrame,frames.length-1);const f=frames[trialFrame];
  const comparison=TrialDelta.compare(t.playback.no_new_dispatch[trialFrame],t.playback.current_plan[trialFrame]);
  root.querySelector('[data-trial-time]').value=trialFrame;
  root.querySelector('[data-trial-clock]').textContent=trialShortTime(f.time_s);
  root.querySelector('[data-trial-play]').textContent=trialTimer?'暂停':'播放模拟';
  root.querySelector('[data-trial-play]').setAttribute('aria-label',trialTimer?'暂停方案模拟':'播放方案模拟');
  const completed=f.tasks.filter(task=>task.status==='completed').length;
  const active=f.tasks.find(task=>!['completed','cancelled','cancelled_delivered','confirmed'].includes(task.status));
  root.querySelector('[data-trial-progress]').textContent=`${trialStrategy==='current_plan'?'采用方案':'不新增调运'} · 截至此刻成功 ${f.metrics.served} 次，未满足 ${f.metrics.unmet} 次${f.metrics.pending_walks?'，待结果 '+f.metrics.pending_walks+' 次':''} · ${active?taskLabels[active.status]+' · ':''}已完成 ${completed} 项调运`;
  for(const poly of root.querySelectorAll('[data-trial-node]')){
    const n=poly.dataset.trialNode,has=(f.unmet[n]||0)>0;poly.dataset.problem=String(has);poly.dataset.selected=String(n===trialPoint);poly.setAttribute('aria-pressed',String(n===trialPoint));
    const delta=comparison.nodes.find(r=>r.id===n)?.delta||0;poly.dataset.stockDelta=delta>0?'more':delta<0?'less':'same';
    const p=liveSnapshot.points.find(p=>p.node_id===n);if(p)poly.setAttribute('aria-label',`${p.name}，可借 ${f.available[n]} 辆，此段未满足 ${f.unmet[n]||0} 次`);
  }
  const p=liveSnapshot.points.find(p=>p.node_id===trialPoint),a=t.playback.no_new_dispatch[trialFrame],b=t.playback.current_plan[trialFrame];
  root.querySelector('[data-trial-point]').innerHTML=p?`<strong>${esc(p.name)}${p.polygon?'':' · 尚未定位'}</strong><span>此刻可借</span><span>不新增调运 <b>${a.available[p.node_id]} 辆</b></span><span>采用方案 <b>${b.available[p.node_id]} 辆</b></span>`:'点击建筑，对比同一时刻的可借车辆。';
  let box=root.querySelector('[data-trial-difference]');
  if(!box){box=document.createElement('section');box.dataset.trialDifference='';box.className='trial-difference';root.querySelector('[data-trial-point]').before(box);}
  const first=TrialDelta.first(t.playback),signed=v=>(v>0?'+':'')+v;
  box.innerHTML=`<div class="trial-difference-head"><strong>${comparison.nodes.length?`${comparison.nodes.length} 处库存不同`:'两方案此刻库存相同'}</strong><span>采用方案相较不新增调运：累计未满足 ${signed(comparison.unmet)} 次</span></div>${trialFrame===0?'<p>模拟起点；预留可先影响可借数，装卸后才改变停放数量。</p>':''}<div class="trial-difference-actions"><button data-trial-jump="${first}" ${first<0?'disabled':''}>${first<0?'全程无差异':'首个差异 · '+trialShortTime(t.playback.current_plan[first].time_s)}</button><button data-trial-jump="${frames.length-1}">模拟结束 · ${trialShortTime(frames[frames.length-1].time_s)}</button></div>${comparison.nodes.length?`<div class="trial-difference-places">${comparison.nodes.slice(0,3).map(r=>{const point=liveSnapshot.points.find(p=>p.node_id===r.id);return `<button data-trial-delta-point="${esc(r.id)}"><strong>${esc(point?.name||r.id)}</strong><span>${r.baseline} → ${r.plan} 辆</span><b>${signed(r.delta)} 辆</b></button>`;}).join('')}</div><p>虚线描边：采用方案后库存更多（蓝）／更少（棕）。库存增加不代表服务改善。</p>`:''}`;
  box.querySelectorAll('[data-trial-jump]').forEach(button=>button.onclick=()=>{stopTrialPlayback();trialFrame=Number(button.dataset.trialJump);updateTrialFrame(root);});
  box.querySelectorAll('[data-trial-delta-point]').forEach(button=>button.onclick=()=>{trialPoint=button.dataset.trialDeltaPoint;updateTrialFrame(root);});
}
function bindTrialPlayer(root,m){
  const run=root.querySelector('[data-review-run]');if(run)run.onclick=()=>{run.disabled=true;run.textContent='正在模拟…';managerAction({action:'trial',tasks:m.plan.tasks,state_version:m.state_version,horizon_minutes:60});};
  if(!root.querySelector('[data-trial-map]'))return;
  const playback=root.querySelector('.trial-playback');if(playback)playback.ontoggle=()=>{if(!playback.open)stopTrialPlayback();};
  if(!trialPoint)trialPoint=trialResult.proposed_tasks[0]?.destination;
  drawTrialMap(root);updateTrialFrame(root);
  root.querySelector('[data-trial-time]').oninput=e=>{stopTrialPlayback();trialFrame=Number(e.target.value);updateTrialFrame(root);};
  root.querySelector('[data-trial-play]').onclick=()=>{
    if(trialTimer){stopTrialPlayback();updateTrialFrame(root);return;}
    if(trialFrame===trialResult.playback.current_plan.length-1)trialFrame=0;
    trialTimer=setInterval(()=>{if($('[data-results-view]').hidden||reviewMode!=='trial'){stopTrialPlayback();return;}trialFrame++;if(trialFrame>=trialResult.playback.current_plan.length-1){trialFrame=trialResult.playback.current_plan.length-1;stopTrialPlayback();}updateTrialFrame(root);},800);updateTrialFrame(root);
  };
  root.querySelectorAll('[data-trial-strategy]').forEach(b=>b.onclick=()=>{trialStrategy=b.dataset.trialStrategy;root.querySelectorAll('[data-trial-strategy]').forEach(q=>q.setAttribute('aria-pressed',String(q===b)));updateTrialFrame(root);});
  root.querySelectorAll('[data-review-tab],[data-review-map],[data-review-adopt]').forEach(b=>b.addEventListener('click',stopTrialPlayback));
}
document.addEventListener('visibilitychange',()=>{if(document.hidden)stopTrialPlayback();});
document.querySelectorAll('[data-view]').forEach(b=>b.addEventListener('click',()=>{document.body.dataset.reviewActive=String(b.dataset.view==='results');stopTrialPlayback();}));

// A single decision stage in the sidebar: read risk first, then plan.
let decisionStage='risk';
const ungroupedTaskRender=renderTask,unguidedChoose=choose;
choose=function(id){decisionStage='risk';unguidedChoose(id);renderDecisionFocus();};
renderTask=function(){ungroupedTaskRender();renderDecisionFocus();};
function renderDecisionFocus(){
  const m=data?.manager,aside=document.querySelector('aside');if(!aside)return;
  let panel=aside.querySelector('[data-decision-focus]');
  const task=aside.querySelector('.advice-task'),detail=aside.querySelector('[data-place-details]');
  if(historical||!m){if(panel)panel.hidden=true;task.hidden=false;detail.hidden=false;return;}
  if(!panel){panel=document.createElement('section');panel.dataset.decisionFocus='';panel.className='decision-focus';aside.insertBefore(panel,aside.querySelector('.advice-task'));}
  panel.hidden=false;task.hidden=decisionStage!=='plan';detail.hidden=decisionStage==='plan';
  if(decisionStage==='plan'){
    const executing=m.tasks.some(t=>!['completed','cancelled','cancelled_delivered'].includes(t.status));
    panel.innerHTML=`<button class="trial-text-button" data-decision-risk>返回风险信息</button><p class="decision-hint">${executing?'查看各任务阶段；车辆卸毕后才计入接收点库存。':'修改数量或出发时间后需重新模拟。'}</p>`;
    panel.querySelector('button').onclick=()=>{decisionStage='risk';renderDecisionFocus();};return;
  }
  const risks=data.points.filter(p=>p.status==='shortage').sort((a,b)=>b.shortage-a.shortage),active=m.tasks.some(t=>!['completed','cancelled','cancelled_delivered'].includes(t.status));
  panel.innerHTML=`<h2>缺车风险</h2><p class="decision-hint">${!m.forecast_available?esc(m.forecast_error):risks.length?`${risks.length} 处未来缺车风险。点选地点查看库存与缺口。`:'暂无明显缺车风险，可推进时刻观察变化。'}</p><div class="decision-risks">${risks.map(p=>`<button data-decision-point="${esc(p.id)}"><span>${esc(p.name)}${reviewRiskTiming(p)}</span><strong>预计缺 ${shortageCount(p.shortage)} 辆</strong></button>`).join('')}</div><button class="${m.plan.tasks.length||active?'primary':'trial-text-button'}" data-decision-plan>${active?'查看执行任务':m.plan.tasks.length?'制定调运方案':'查看方案可行性'}</button>`;
  panel.querySelector('[data-decision-plan]').onclick=()=>{decisionStage='plan';renderDecisionFocus();};
  panel.querySelectorAll('[data-decision-point]').forEach(b=>b.onclick=()=>{choose(b.dataset.decisionPoint);focus(b.dataset.decisionPoint);});
}
const beforePlayerReview=renderManagerReview,beforePlayerBack=reviewBack;
renderManagerReview=function(){stopTrialPlayback();beforePlayerReview();const b=$('#review-tab-plan');if(b)b.textContent='方案模拟';$('.scope-note').textContent='校园模拟数据';};
reviewBack=function(point){decisionStage=point?'risk':trialResult?'plan':'risk';beforePlayerBack(point);renderDecisionFocus();};
const beforePlayerSnapshot=applySnapshot;
applySnapshot=function(next){
  const previous=liveSnapshot?.manager,version=previous?.state_version,available=previous?.forecast_available;
  beforePlayerSnapshot(next);
  const current=liveSnapshot?.manager;
  if(current&&!$('[data-results-view]').hidden&&(version!==current.state_version||available!==current.forecast_available))renderResults();
};
