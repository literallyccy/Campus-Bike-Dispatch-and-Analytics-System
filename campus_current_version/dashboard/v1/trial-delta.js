const TrialDelta={
  compare(a,b){
    if(!a||!b||a.time_s!==b.time_s)throw new Error('模拟帧时刻不一致');
    if(Object.keys(a.available).length!==Object.keys(b.available).length||Object.keys(a.available).some(id=>!Number.isFinite(a.available[id])||!Number.isFinite(b.available[id])))throw new Error('模拟库存数据不完整');
    const nodes=Object.keys(a.available).map(id=>({id,baseline:a.available[id],plan:b.available[id],delta:b.available[id]-a.available[id]})).filter(r=>r.delta!==0).sort((a,b)=>Math.abs(b.delta)-Math.abs(a.delta)||a.id.localeCompare(b.id));
    return {nodes,unmet:b.metrics.unmet-a.metrics.unmet,served:b.metrics.served-a.metrics.served};
  },
  first(playback){return playback.current_plan.findIndex((b,i)=>{const d=this.compare(playback.no_new_dispatch[i],b);return d.nodes.length||d.unmet||d.served;});}
};
if(typeof module!=='undefined')module.exports=TrialDelta;
