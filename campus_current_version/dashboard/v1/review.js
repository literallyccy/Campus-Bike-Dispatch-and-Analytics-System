/* Operational review: no model leaderboard or course-report content. */
let reviewMode='execution';
const ReviewMath={
  extraCost(b,p){return {moved:p.moved-b.moved,transport_km:p.transport_km-b.transport_km,work_minutes:p.work_minutes-b.work_minutes};},
  valid(r){return !!r&&['potential','served','unmet','pending_walks'].every(k=>Number.isInteger(r[k])&&r[k]>=0)&&r.served+r.unmet+r.pending_walks===r.potential;},
  verdict(b,p){
    if(!this.valid(b)||!this.valid(p)||b.potential!==p.potential)return {tone:'neutral',text:'数据口径不一致，暂不能比较'};
    if(!b.potential)return {tone:'neutral',text:'试算时段内没有借车需求'};
    if(b.pending_walks!==p.pending_walks)return {tone:'neutral',text:'等待结果的需求不同，暂不能确定改善'};
    const gain=b.unmet-p.unmet;
    return {tone:gain>0?'benefit':gain<0?'loss':'neutral',text:gain>0?`本次试算少 ${gain} 次未满足`:gain<0?`本次试算多 ${-gain} 次未满足`:'本次试算没有减少未满足'};
  }
};
if(typeof module!=='undefined')module.exports=ReviewMath;
function reviewOutcome(r,label){
  if(!ReviewMath.valid(r))return '<p class="review-muted">该时段结果暂不可用。</p>';
  if(!r.potential)return `<div class="review-outcome"><strong>${esc(label)}</strong><p class="review-muted">暂无借车需求，不能计算满足率。</p></div>`;
  const rate=r.served/r.potential*100;
  return `<div class="review-outcome"><div class="review-row"><strong>${esc(label)}</strong><span>已满足 ${rate.toFixed(1)}%</span></div><div class="outcome-stack" role="img" aria-label="${esc(label)}：成功${r.served}，未满足${r.unmet}，待结果${r.pending_walks}"><span class="served" style="width:${rate}%"></span><span class="waiting" style="width:${r.pending_walks/r.potential*100}%"></span><span class="unmet" style="width:${r.unmet/r.potential*100}%"></span></div><div class="review-row review-counts"><span>成功 ${whole(r.served)} 次</span><span>未满足 ${whole(r.unmet)} 次${r.pending_walks?` · 待结果 ${r.pending_walks} 次`:''}</span></div></div>`;
}
function reviewCost(r){return `<dl class="review-cost"><div><dt>搬运</dt><dd>${whole(r.moved)} <small>辆次</small></dd></div><div><dt>运输</dt><dd>${number(r.transport_km??r.distance_m/1000)} <small>公里</small></dd></div><div><dt>作业</dt><dd>${number(r.work_minutes??r.work_s/60)} <small>分钟</small></dd></div></dl>`;}
function reviewBack(point){
  historical=false;stopPlayback();if(liveSnapshot)applySnapshot(liveSnapshot);
  $('[data-view="map"]').click();if(point){choose(point);focus(point);}
}
function reviewLocations(rows,trial=false){
  let selected=rows.slice();
  if(trial){
    selected=rows.filter(r=>r.baseline!==r.plan).sort((a,b)=>Math.abs(b.baseline-b.plan)-Math.abs(a.baseline-a.plan)).slice(0,3);
    for(const r of rows.filter(r=>r.plan>r.baseline).sort((a,b)=>(b.plan-b.baseline)-(a.plan-a.baseline)).slice(0,2))if(!selected.includes(r))selected.push(r);
  }else selected.sort((a,b)=>b.unmet-a.unmet);
  selected=selected.slice(0,5);if(!selected.length)return trial?'<p class="review-muted">各地点未满足次数没有变化。</p>':'';
  const max=Math.max(1,...selected.map(r=>trial?Math.abs(r.baseline-r.plan):r.unmet));
  return `<div class="review-locations">${selected.map(r=>{
    const v=trial?r.baseline-r.plan:r.unmet;
    return `<button class="review-location" data-review-location="${esc(r.point_id)}"><span>${esc(r.name)}</span><span class="location-track ${trial?'signed':''}" aria-hidden="true"><i class="${trial&&v>=0?'benefit':'loss'}" style="width:${Math.abs(v)/max*(trial?50:100)}%;${trial?(v>=0?'left:50%':'right:50%'):''}"></i></span><strong>${trial?(v>0?'少 ':'多 '):''}${whole(Math.abs(v))} 次</strong></button>`;
  }).join('')}</div>`;
}
function reviewExecution(m){
  if(!m)return '<h3>当前实例尚无执行结果</h3><button data-review-map>返回调度地图</button>';
  const r={...m.metrics,pending_walks:m.pending_walks};
  const tasks=m.tasks||[],active=tasks.filter(t=>!['completed','cancelled','cancelled_delivered'].includes(t.status));
  return `<div class="review-section-heading"><h3>这段时间的借车情况</h3><p>${simTime(m.initial_clock_s)} — ${simTime(m.clock_s)}</p></div>${m.clock_s===m.initial_clock_s?'<p class="review-status">模拟尚未推进，当前没有可评价的执行效果。</p>':''}${reviewOutcome(r,'本次模拟累计')}${r.pending_walks?`<p class="review-muted">还有 ${r.pending_walks} 次需求等待结果，当前满足率尚未最终结算。</p>`:''}
    <div class="review-split"><section><h3>未满足记录较多的地点</h3><p class="review-muted">累计记录不等于当前缺车。点选地点，查看当前库存。</p>${reviewLocations(m.unmet_locations||[])}${!(m.unmet_locations||[]).length?'<p class="review-muted">尚无地点记录。</p>':''}</section><section><h3>调运投入与进度</h3>${reviewCost(r)}<p class="review-muted">${tasks.length?`待出发 ${active.filter(t=>t.status==='confirmed').length} 项 · 执行中 ${active.filter(t=>t.status!=='confirmed').length} 项 · 已完成 ${tasks.filter(t=>t.status==='completed').length} 项`:'尚未执行调运；上方是当前模拟运行的借车结果。'}</p>${tasks.slice(-4).reverse().map(t=>`<details class="review-task"><summary><span>${esc(t.origin_name)} → ${esc(t.destination_name)}</span><strong>${t.quantity} 辆 · ${esc(taskLabels[t.status]||t.status)}</strong></summary><p>出发 ${simTime(t.departure_s)}<br>预计到达 ${simTime(t.arrival_s)} · 卸毕 ${simTime(t.finish_s)}</p></details>`).join('')}<button class="primary" data-ops-tasks>查看任务与推进模拟</button></section></div>`;
}
function reviewTrial(m){
  const t=trialResult;
  if(!t)return `<div class="review-empty"><h3>暂无方案模拟</h3><p>在地图选择方案并试算，再对比服务改善与搬运投入。</p><button class="primary" data-review-map>到地图制定方案</button></div>`;
  const b=t.results.no_new_dispatch,p=t.results.current_plan,v=ReviewMath.verdict(b,p);
  const fresh=m&&t.source_state_version===m.state_version&&m.forecast_available;
  return `${!fresh?'<p class="review-status">这是已保存的试算，当前状态已变化或预测不可用，不能直接采用。</p>':''}<div class="review-verdict ${v.tone}"><h3>${esc(v.text)}</h3><p>${Number.isFinite(t.window_start_s)?`${simTime(t.window_start_s)} — ${simTime(t.window_end_s)}`:'保存的试算'} · 同一批 ${whole(t.potential_demands)} 次模拟需求</p></div>${reviewOutcome(b,'不新增调运')}${reviewOutcome(p,'采用此方案')}<p class="review-muted">${b.pending_walks||p.pending_walks?'仍有需求等待结果，比较仅截至试算结束。':'蓝色为成功借车，红色为未满足。'}已有确认任务在两种方案中均保留。</p><div class="review-split"><section><h3>采用方案的额外投入</h3>${reviewCost(ReviewMath.extraCost(b,p))}<details class="review-method"><summary>查看本次方案</summary>${(t.proposed_tasks||[]).map(task=>`<p>${esc(task.origin_name)} → ${esc(task.destination_name)} · ${task.quantity} 辆<br>出发 ${simTime(task.departure_s)} · 卸毕 ${simTime(task.finish_s)}</p>`).join('')}</details></section><section><h3>地点效果对比</h3>${reviewLocations(t.location_effects||[],true)}<p class="review-muted">最多显示5处，优先较大变化及变差地点；按最后一次借车尝试地点统计。</p></section></div><div class="review-actions"><button class="primary" data-review-adopt ${fresh&&t.proposed_tasks?.length&&!managerBusy&&!historical?'':'disabled'}>确认调运方案</button><button data-review-map>返回地图调整</button><span>${fresh?'确认后预留车辆；尚未执行。':'运行状态已变化或预测不可用，请返回地图重新试算。'}</span></div>`;
}
function renderManagerReview(){
  const root=$('[data-results-body]'),m=liveSnapshot?.manager;
  // Remove former mixed-scope panels, including panels created before hot reload.
  $('[data-current-trial]')?.remove();$('[data-execution-review]')?.remove();
  $('.scope-note').textContent='校园模拟数据';
  $('[data-connection]').textContent=m?'已读取本次运行结果':'暂无运行结果';
  root.innerHTML=`<div class="review-tabs" role="tablist" aria-label="复盘内容"><button id="review-tab-plan" role="tab" aria-controls="review-plan" aria-selected="${reviewMode==='trial'}" tabindex="${reviewMode==='trial'?0:-1}" data-review-tab="trial">方案试算</button><button id="review-tab-execution" role="tab" aria-controls="review-execution" aria-selected="${reviewMode==='execution'}" tabindex="${reviewMode==='execution'?0:-1}" data-review-tab="execution">执行复盘</button></div><p class="review-status" role="status">${m&&!m.forecast_available?'当前计算或数据连接不可用；以下仅为已保存结果。':''}</p><p data-review-error role="alert" hidden></p><section id="review-plan" class="review-sheet" role="tabpanel" aria-labelledby="review-tab-plan" ${reviewMode==='trial'?'':'hidden'}>${reviewTrial(m)}</section><section id="review-execution" class="review-sheet" role="tabpanel" aria-labelledby="review-tab-execution" ${reviewMode==='execution'?'':'hidden'}>${reviewExecution(m)}</section><p class="review-footnote">模拟结果不能作为真实交大调度收益；运输距离与时延为模拟假设。</p>`;
  const buttons=[...root.querySelectorAll('[data-review-tab]')];
  function select(button){reviewMode=button.dataset.reviewTab;for(const b of buttons){b.setAttribute('aria-selected',String(b===button));b.tabIndex=b===button?0:-1;}$('#review-plan').hidden=reviewMode!=='trial';$('#review-execution').hidden=reviewMode!=='execution';}
  for(const button of buttons){button.onclick=()=>select(button);button.onkeydown=e=>{if(['ArrowLeft','ArrowRight','Home','End'].includes(e.key)){e.preventDefault();const b=e.key==='Home'?buttons[0]:e.key==='End'?buttons[1]:buttons[1-buttons.indexOf(button)];select(b);b.focus();}};}
  root.querySelectorAll('[data-review-map]').forEach(b=>b.onclick=()=>reviewBack());
  root.querySelectorAll('[data-review-location]').forEach(b=>b.onclick=()=>reviewBack(b.dataset.reviewLocation));
  const adopt=root.querySelector('[data-review-adopt]');if(adopt)adopt.onclick=async()=>{
    adopt.disabled=true;
    const response=await managerAction({action:'confirm',tasks:trialResult.proposed_tasks,state_version:trialResult.source_state_version});
    if(response)reviewBack();else{renderManagerReview();const error=$('[data-review-error]');error.hidden=false;error.textContent=$('.error').textContent||'确认未完成，请重新试算。';}
  };
  if(typeof bindTrialPlayer==='function')bindTrialPlayer(root,m);
}
function showReviewTrial(){reviewMode='trial';$('[data-view="results"]').click();renderManagerReview();}
