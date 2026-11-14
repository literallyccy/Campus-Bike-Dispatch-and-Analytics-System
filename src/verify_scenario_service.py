"""Real SQLite/scenario/dispatch integration with labelled synthetic risk fixtures."""
import copy,json,tempfile,sys,uuid
from pathlib import Path
import manager_loop as M
import scenario_service as S
checks=[]
def check(name,fn):
    fn();checks.append(name)
def equal(a,b):assert a==b,(a,b)
def rejected(fn):
    try:fn()
    except M.Conflict:return
    raise AssertionError('expected Conflict')
with tempfile.TemporaryDirectory() as temp:
    base=Path(temp).resolve();M.ROOT=base
    (base/'data').mkdir();(base/'config').mkdir();(base/'models/borrow').mkdir(parents=True);(base/'models/return').mkdir(parents=True)
    for kind in ['borrow','return']:(base/f'models/{kind}/runtime_model.json').write_text('{}')
    (base/'data/trips.jsonl').write_text('')
    start=42*86400+9*3600
    demands=[dict(day_index=42,time_s=start+1200+i,origin_node='B',destination_node='A',speed_kmh=10,handling_s=5) for i in range(5)]
    (base/'data/demands.jsonl').write_text('\n'.join(map(json.dumps,demands)))
    c=json.loads((Path(__file__).resolve().parents[1]/'config/dispatch_v1.json').read_text());c.update(depot_node='A',operating_hours=[0,24],truck_capacity=20,daily_move_budget=100,lookahead_minutes=60)
    (base/'config/dispatch_v1.json').write_text(json.dumps(c))
    nodes={n:dict(name=n,parking_id=n,simulation_zone_id='z') for n in ['A','B']};dist={(a,b):0 if a==b else 100 for a in nodes for b in nodes};M.context=lambda:(nodes,dist,c)
    cfg=dict(manager_enabled=True,points=[dict(node_id=n,id=n,name=n,kind='dormitory',initial_inventory=q) for n,q in [('A',10),('B',0)]],initial_in_transit=0,total_bicycles=10,bbox=[],boundary=[],background='',metadata=dict(reference_time_s=start,dataset_dir='data',replay_path='replay.json',model_sha256={f'models/{k}/runtime_model.json':'fixture' for k in ['borrow','return']}))
    run=base/'primary';run.mkdir();(run/'config.json').write_text(json.dumps(cfg));(run/'sentinel').write_text('original')
    fs=[dict(metadata=dict(reference_time_s=t),points=[dict(node_id=n,inventory=q) for n,q in [('A',10),('B',0)]],in_transit=0) for t in [start,start+3600]]
    (base/'replay.json').write_text(json.dumps(dict(frames=fs)))
    def risk(s,cfg):return [dict(p,inventory=s['stock'][p['node_id']],available_to_borrow=s['stock'][p['node_id']]-M.reserved(s)[p['node_id']],reserved=M.reserved(s)[p['node_id']],spare=max(0,s['stock'][p['node_id']]-M.reserved(s)[p['node_id']]-2) if p['node_id']=='A' else 0,shortage=5 if p['node_id']=='B' else 0,status='shortage' if p['node_id']=='B' else 'surplus',first_shortage_minute=20) for p in cfg['points']]
    M.risks=risk
    v=S.create(run,start);sid=v['scenario']['id'];other=S.create(run,start+3600)['scenario']['id']
    check('select time restores same scene',lambda:equal(S.create(run,start)['scenario']['id'],sid))
    check('new copy is isolated',lambda:equal(S.create(run,start,True)['scenario']['id']!=sid,True))
    check('invalid time rejected',lambda:rejected(lambda:S.create(run,start+1)))
    def op(action,**kw):return S.operation(run,dict(action=action,scenario_id=sid,state_version=M.read(S.resolve(run,sid))['version'],op_id=uuid.uuid4().hex,**kw))
    task=dict(origin='A',destination='B',quantity=5,departure_s=start+60)
    op('draft',revision=0,tasks=[task]);t=op('trial',revision=1)
    check('trial uses same demands for all strategies',lambda:equal(t['same_potential_hash'],t['system']['same_potential_hash']))
    check('trial does not reserve source stock',lambda:equal(M.reserved(M.read(S.resolve(run,sid)))['A'],0))
    check('persist trial and draft on new read',lambda:equal(S.view(run,sid)['scenario']['trial']['id'],t['id']))
    check('cross-scene trial cannot confirm',lambda:rejected(lambda:S.operation(run,dict(action='confirm',scenario_id=other,state_version=0,op_id=uuid.uuid4().hex,trial_id=t['id']))))
    op('draft',revision=1,tasks=[dict(task,quantity=4)])
    check('edited draft invalidates trial on server',lambda:rejected(lambda:op('confirm',trial_id=t['id'])))
    t2=op('trial',revision=2);body=dict(action='confirm',scenario_id=sid,state_version=0,op_id='confirm-once',trial_id=t2['id'])
    result=S.operation(run,body);equal(S.operation(run,body),result)
    check('confirm retry does not duplicate tasks',lambda:equal(len(M.read(S.resolve(run,sid))['tasks']),1))
    check('conflicting idempotency payload rejected',lambda:rejected(lambda:S.operation(run,dict(body,trial_id=t['id']))))
    check('other scene remains unchanged',lambda:equal(M.read(S.resolve(run,other))['tasks'],[]))
    op('advance',to_time_s=start+3600)
    state=M.read(S.resolve(run,sid));M.conserved(state,cfg)
    check('real simulator progresses task to completion',lambda:equal(state['tasks'][0]['status'],'completed'))
    check('primary untouched',lambda:equal((run/'sentinel').read_text(),'original'))
    check('unmet and cost metrics retained',lambda:equal(all(k in t2['results']['current_plan'] for k in ['unmet','moved','work_minutes']),True))
    check('failed confirmations retained',lambda:equal(any('失效' in json.loads(line)['error'] for line in (S.root(run)/'failed_operations.jsonl').read_text().splitlines()),True))
    original_radius=c['walking_radius_m'];c['walking_radius_m']=0
    original_demands=(base/'data/demands.jsonl').read_text()
    worse_demands=[dict(day_index=42,time_s=start+1200+i,origin_node='A',destination_node='B',speed_kmh=10,handling_s=5) for i in range(10)]
    (base/'data/demands.jsonl').write_text('\n'.join(map(json.dumps,worse_demands)))
    bad=S.create(run,start,True)['scenario']['id']
    def badop(action,**kw):return S.operation(run,dict(action=action,scenario_id=bad,state_version=0,op_id=uuid.uuid4().hex,**kw))
    badop('draft',revision=0,tasks=[task]);negative=badop('trial',revision=1)
    check('worse plan retains higher unmet result',lambda:equal(negative['results']['current_plan']['unmet']>negative['results']['no_new_dispatch']['unmet'],True))
    check('worse result persists unchanged',lambda:equal(S.view(run,bad)['scenario']['trial']['results'],negative['results']))
    check('negative trial recommends no new dispatch',lambda:equal(S.advice(negative)['code'],'worse'))
    check('negative confirmation requires reason on server',lambda:rejected(lambda:badop('confirm',trial_id=negative['id'])))
    before=M.read(S.resolve(run,bad))
    retained=badop('retain',trial_id=negative['id'])
    check('retaining baseline does not mutate stock clock or tasks',lambda:equal(M.read(S.resolve(run,bad)),before))
    check('retaining choice is persisted',lambda:equal(S.view(run,bad)['scenario']['decision']['action'],'retain'))
    listed={x['id']:x for x in S.catalogue(run)['scenarios']}
    check('catalogue shows retained state and clock',lambda:equal((listed[bad]['status'],listed[bad]['clock_s']),('不新增调运',start)))
    proof=S.view(run,bad)['scenario']['trial']['decision_evidence']
    check('evidence uses measured location delta and reservation',lambda:equal(any(x['node_id']=='A' and x['delta']>0 and x['reserved_at_start']==5 for x in proof),True))
    approved=badop('confirm',trial_id=negative['id'],reason='验收负例保留代价')
    check('override reason and negative assessment persist',lambda:equal((approved['reason'],approved['assessment']['code']),('验收负例保留代价','worse')))
    listed={x['id']:x for x in S.catalogue(run)['scenarios']}
    check('catalogue preserves multiple copies and confirmed status',lambda:equal(listed[bad]['status'],'已确认'))
    pending=copy.deepcopy(negative);pending['results']['current_plan']['pending_walks']=1
    check('pending outcomes are not recommended as final improvement',lambda:equal(S.advice(pending)['code'],'unsettled'))
    neutral=copy.deepcopy(negative);neutral['results']['current_plan']['unmet']=neutral['results']['no_new_dispatch']['unmet']
    check('no gain does not recommend extra work',lambda:equal(S.advice(neutral)['requires_reason'],True))
    check('positive result remains eligible without override',lambda:equal(S.advice(t2)['requires_reason'],False))
    (base/'data/demands.jsonl').write_text(original_demands)
    c['walking_radius_m']=original_radius
    (base/'models/borrow/runtime_model.json').write_text('{"changed":true}')
    check('changed model bytes invalidate scene writes',lambda:rejected(lambda:op('draft',revision=2,tasks=[task])))
report=dict(passed=True,checks=checks,limitations=['Synthetic two-node risk fixture; no formal campus-v2 MLlib inference or browser acceptance','Real SQLite transactions, actual confirm and advance and two-strategy simulator exercised'])
output=Path(sys.argv[1]);output.parent.mkdir(parents=True,exist_ok=True);output.write_text(json.dumps(report,ensure_ascii=False,indent=2));print(json.dumps(report,ensure_ascii=False))
