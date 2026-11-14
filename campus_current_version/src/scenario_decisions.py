"""Post-simulation advice; never a predictor or a rewrite of formal evaluation."""

def advice(trial):
    b=trial['results']['no_new_dispatch'];p=trial['results']['current_plan']
    pending=b.get('pending_walks',0)+p.get('pending_walks',0)
    delta=p['unmet']-b['unmet']
    cost={k:p.get(k,0)-b.get(k,0) for k in ('moved','work_minutes','transport_km')}
    code='unsettled' if pending else 'worse' if delta>0 else 'no_gain' if delta==0 else 'improved'
    title={'unsettled':'结果尚未结算，暂不推荐新增调运','worse':'建议保留不新增调运方案','no_gain':'没有减少未满足，建议不新增调运','improved':'模拟显示服务改善，请结合投入确认'}[code]
    return dict(code=code,title=title,unmet_delta=delta,extra_cost=cost,requires_reason=code!='improved',
                scope='模拟评估后选择，不代表现实预知需求')


def evidence(trial):
    """Describe observed frame differences, explicitly avoid causal attribution."""
    rows=[]
    frames=trial.get('playback',{})
    baseline={f['time_s']:f for f in frames.get('no_new_dispatch',[])}
    for loc in trial.get('location_effects',[]):
        delta=loc['plan']-loc['baseline']
        if not delta:continue
        node=loc['node_id'];first_loss=None;first_less=None
        for f in frames.get('current_plan',[]):
            b=baseline.get(f['time_s'])
            if b is None:continue
            if first_loss is None and f.get('unmet',{}).get(node,0)>b.get('unmet',{}).get(node,0):first_loss=f['time_s']
            if first_less is None and f.get('available',{}).get(node,0)<b.get('available',{}).get(node,0):first_less=f['time_s']
        origins=[t for t in trial.get('proposed_tasks',[]) if t.get('origin')==node]
        destinations=[t for t in trial.get('proposed_tasks',[]) if t.get('destination')==node]
        rows.append(dict(loc,delta=delta,first_sample_with_more_unmet_s=first_loss,
            first_sample_with_less_available_s=first_less,
            reserved_at_start=sum(t['quantity'] for t in origins),
            planned_unload_s=[t['finish_s'] for t in destinations if 'finish_s' in t]))
    return sorted(rows,key=lambda r:(-r['delta'],r['name']))


def enrich(trial):
    if trial is None:return None
    return dict(trial,decision_advice=advice(trial),decision_evidence=evidence(trial))
