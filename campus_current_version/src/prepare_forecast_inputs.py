"""Observable-order-only features. No reading of simulator truth or schedules."""
import argparse
from collections import Counter
import hashlib
import json
import math
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
FEATURE_COLUMNS=['hour','weekday','is_weekend','function_type',
                 'borrow_lag_1h','borrow_lag_2h','borrow_lag_24h','borrow_lag_168h',
                 'borrow_mean_3h','borrow_mean_24h','return_lag_1h','return_lag_24h']
ALLOWED_SOURCE_FILES=('nodes.jsonl','trips.jsonl')
LABEL='target_completed_borrows_next_hour'


def json_rows(path):
    with path.open() as f:
        for line in f:yield json.loads(line)


def sha256_file(path):
    path = Path(path)
    if path.is_dir():
        digest = hashlib.sha256()
        for child in sorted(p for p in path.rglob('*') if p.is_file() and not p.name.startswith('.')):
            digest.update(child.relative_to(path).as_posix().encode('utf-8'))
            digest.update(sha256_file(child).encode('ascii'))
        return digest.hexdigest()
    digest=hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''):
            digest.update(block)
    return digest.hexdigest()


def feature_row(node,hour,node_type,borrow,returns,start_weekday=0):
    """Forecast [hour,hour+1), using completed bins strictly before hour."""
    return dict(node_id=node,forecast_origin_s=hour*3600,hour=hour%24,
                weekday=(start_weekday+hour//24)%7,
                is_weekend=int((start_weekday+hour//24)%7>=5),function_type=node_type,
                borrow_lag_1h=borrow[hour-1,node],borrow_lag_2h=borrow[hour-2,node],
                borrow_lag_24h=borrow[hour-24,node],borrow_lag_168h=borrow[hour-168,node],
                borrow_mean_3h=sum(borrow[t,node] for t in range(hour-3,hour))/3,
                borrow_mean_24h=sum(borrow[t,node] for t in range(hour-24,hour))/24,
                return_lag_1h=returns[hour-1,node],return_lag_24h=returns[hour-24,node])


def build(source,output,days=56,*,train_end_day=28,validation_end_day=42,
          start_weekday=0,dataset_kind='synthetic',dataset_name='campus_v1',
          coverage_confirmed=False):
    if not coverage_confirmed:
        raise ValueError('Confirm continuous observation coverage before filling absent bins with zero')
    if not 7 < train_end_day < validation_end_day < days:
        raise ValueError('Require 7 < train_end_day < validation_end_day < days')
    if start_weekday not in range(7) or dataset_kind not in ('synthetic','real'):
        raise ValueError('Invalid weekday or dataset kind')
    if output.exists():
        raise FileExistsError(f'Refusing to overwrite prepared data: {output}')
    nodes={}
    for n in json_rows(source/'nodes.jsonl'):
        node,kind=n.get('node_id'),n.get('function_type')
        if not isinstance(node,str) or not node or node in nodes:
            raise ValueError('node_id must be a unique nonempty string')
        if not isinstance(kind,str) or not kind:
            raise ValueError('function_type must be a nonempty string')
        nodes[node]=kind
    if not nodes:
        raise ValueError('No nodes supplied')
    borrow,returns=Counter(),Counter()
    trip_ids=set()
    for t in json_rows(source/'trips.jsonl'):
        trip=t.get('trip_id')
        if not isinstance(trip,str) or not trip or trip in trip_ids:
            raise ValueError('trip_id must be a unique nonempty string')
        trip_ids.add(trip)
        if not {t['origin_node'],t['destination_node']} <= nodes.keys():
            raise ValueError('unknown node')
        start,end=t.get('start_time_s'),t.get('end_time_s')
        if any(isinstance(x,bool) or not isinstance(x,(int,float)) or not math.isfinite(x)
               for x in (start,end)) or end < start:
            raise ValueError(f'Invalid trip time: {trip}')
        # A departure is visible when borrowed; a return only when it has happened.
        borrow[t['start_time_s']//3600,t['origin_node']]+=1
        returns[t['end_time_s']//3600,t['destination_node']]+=1
    output.mkdir(parents=True)
    counts=Counter()
    ranges=[('train',168,train_end_day*24),
            ('validation',train_end_day*24,validation_end_day*24),
            ('test',validation_end_day*24,days*24)]
    for split,start,end in ranges:
        with (output/(split+'_features.jsonl')).open('w') as ff,(output/(split+'_labels.jsonl')).open('w') as lf:
            for hour in range(start,min(end,days*24)):
                for n,typ in nodes.items():
                    row=feature_row(n,hour,typ,borrow,returns,start_weekday)
                    assert set(row)==set(FEATURE_COLUMNS)|{'node_id','forecast_origin_s'}
                    ff.write(json.dumps(row)+'\n')
                    lf.write(json.dumps(dict(node_id=n,forecast_origin_s=hour*3600,
                                             target_completed_borrows_next_hour=borrow[hour,n]))+'\n')
                    counts[split]+=1
    contract=dict(schema_version=2,
                  dataset={'name':dataset_name,'kind':dataset_kind,'provenance_is_user_declared':True},
                  time={'days':days,'start_weekday':start_weekday,'continuous_coverage_confirmed':True,
                        'unit':'seconds relative to coverage start at local midnight'},
                  split_ranges_s={s:[a*3600,b*3600] for s,a,b in ranges},
                  target='completed borrow count in [forecast_origin_s, forecast_origin_s+3600)',
                  target_is_not_latent_demand=True,feature_columns=FEATURE_COLUMNS,
                  id_columns=['node_id','forecast_origin_s'],allowed_source_files=ALLOWED_SOURCE_FILES,
                  forbidden=['demands.jsonl','activities.jsonl','weather.jsonl','outcomes.jsonl',
                             'config.json','simulation_scenario.json','attraction_weight','speed_kmh','handling_s'],
                  evaluation='rolling one-hour-ahead; features may use earlier realized validation/test orders',
                  caveats=['Offline join with labels is for training/evaluation only.',
                           'All nodes require continuous acquisition coverage; missing collection is not zero.',
                           'The first seven days are history only, so training rows start on day index 7.',
                           'This is a schema/read contract, not OS-level access isolation.',
                           'Source provenance and coverage are operator declarations, not independently verified.'],
                  rows=dict(counts),
                  source_sha256={n:sha256_file(source/n) for n in ALLOWED_SOURCE_FILES})
    (output/'input_contract.json').write_text(json.dumps(contract,ensure_ascii=False,indent=2)+'\n')
    return contract


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source',type=Path,default=ROOT/'data/simulated/campus_v1')
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--days',type=int,default=56)
    p.add_argument('--train-end-day',type=int,default=28)
    p.add_argument('--validation-end-day',type=int,default=42)
    p.add_argument('--start-weekday',type=int,default=0)
    p.add_argument('--dataset-kind',choices=['synthetic','real'],required=True)
    p.add_argument('--dataset-name',required=True)
    p.add_argument('--confirm-continuous-coverage',action='store_true')
    args=p.parse_args()
    result=build(args.source,args.output,args.days,train_end_day=args.train_end_day,
                 validation_end_day=args.validation_end_day,start_weekday=args.start_weekday,
                 dataset_kind=args.dataset_kind,dataset_name=args.dataset_name,
                 coverage_confirmed=args.confirm_continuous_coverage)
    print(json.dumps(result['rows'],ensure_ascii=False))
