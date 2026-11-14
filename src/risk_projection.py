"""Estimate within-window stock using frozen historical timing fractions."""
from functools import lru_cache
import hashlib
import json
import math
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


@lru_cache(maxsize=8)
def load_profile(relative,expected_hash):
    path=(ROOT/relative).resolve()
    if ROOT not in path.parents:raise ValueError('Risk profiles must belong to the project')
    payload=path.read_bytes()
    if hashlib.sha256(payload).hexdigest()!=expected_hash:raise ValueError('Risk profile changed since run initialization')
    return json.loads(payload)


def project(stock,borrows,returns,reserve,borrow_fractions,return_fractions,slot_minutes):
    if any(type(v) not in (int,float) or not math.isfinite(v) or v<0 for v in [stock,borrows,returns,reserve]):
        raise ValueError('Inventory, forecasts and reserve must be finite nonnegative numbers')
    if type(slot_minutes) is not int or slot_minutes<=0:
        raise ValueError('Slot minutes must be a positive integer')
    if len(borrow_fractions)!=len(return_fractions) or not borrow_fractions or len(borrow_fractions)*slot_minutes!=60:
        raise ValueError('Expected a complete hour of matched slots')
    for values in [borrow_fractions,return_fractions]:
        if any(type(v) not in (int,float) or not math.isfinite(v) or v<0 for v in values) or abs(sum(values)-1)>1e-8:
            raise ValueError('Invalid fractions')
    curve=[{'minute':0,'inventory':float(stock)}]
    first=None;value=float(stock);draw=0.
    for i,(b,r) in enumerate(zip(borrow_fractions,return_fractions)):
        previous=value
        value+=returns*r-borrows*b
        if not math.isfinite(value):raise ValueError('Projected inventory exceeds finite numeric range')
        curve.append({'minute':(i+1)*slot_minutes,'inventory':value})
        draw=max(draw,stock-value)
        if first is None and value<0:
            first=i*slot_minutes+(previous/(previous-value)*slot_minutes if previous>0 else 0)
    minimum=min(p['inventory'] for p in curve)
    return dict(projected_stock_curve=curve,projected_min_inventory=minimum,
                projected_end_inventory=value,shortage=max(0.,-minimum),
                spare=max(0.,minimum-reserve),max_cumulative_net_outflow=max(0.,draw),
                first_shortage_minute=first,
                risk_method='training_timing_profile_linear_slots',
                risk_limitation='Expected slot-level trajectory, not observed stock or guaranteed time')


def project_point(point,stock,config):
    metadata=config['metadata']
    if metadata.get('risk_profile_path'):
        profile=load_profile(metadata['risk_profile_path'],metadata['risk_profile_sha256'])
        reference=metadata['reference_time_s']
        if type(reference) is not int or reference<0 or profile['training_range_s'][1]>reference:
            raise ValueError('Risk profile must precede the nonnegative forecast reference time')
        hour=str(reference//3600%24)
        fractions=profile['profiles'][point['kind']][hour]
        result=project(stock,point['expected_borrows'],point['expected_returns'],point['reserve'],
            fractions['borrow']['fractions'],fractions['return']['fractions'],profile['slot_minutes'])
    else:
        result=project(stock,point['expected_borrows'],point['expected_returns'],point['reserve'],[1.],[1.],60)
        result['risk_method']='uniform_hour_fallback'
    return result
