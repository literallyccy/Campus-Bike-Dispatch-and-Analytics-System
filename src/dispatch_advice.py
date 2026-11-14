"""One feasible advisory task from current stock and the declared replay forecasts."""
from functools import lru_cache
import json
from pathlib import Path

from evaluate_dispatch import Policy, distances_for

ROOT = Path(__file__).resolve().parents[1]


@lru_cache(maxsize=1)
def context():
    nodes = {n['node_id']: n for n in map(json.loads, (ROOT / 'data/simulated/campus_v1/nodes.jsonl').read_text().splitlines())}
    distances = distances_for(nodes, json.loads((ROOT / 'data/simulated/campus_v1/synthetic_zone_edges.json').read_text()))
    constraints = json.loads((ROOT / 'config/dispatch_v1.json').read_text())
    return nodes, distances, constraints


def recommendation(snapshot):
    if snapshot['metadata'].get('mode') != 'model_backed_synthetic_replay':
        return None
    nodes, distances, constraints = context()
    points = {p['node_id']: p for p in snapshot['points'] if p.get('polygon')}
    if not any(p['status']=='shortage' for p in points.values()):
        return None
    nodes = {node: meta for node,meta in nodes.items() if node in points}
    class ForecastPolicy(Policy):
        def predict(self, t, borrow, returns):
            # Reserve against the largest projected draw within the window,
            # including early shortages that a later return would conceal.
            return {node: (p['max_cumulative_net_outflow'],0.)
                    if 'max_cumulative_net_outflow' in p else
                    (p['expected_borrows'],p['expected_returns']) for node,p in points.items()}
    policy = ForecastPolicy('model', constraints, nodes, distances, {}, None)
    task = policy.task(snapshot['metadata']['reference_time_s'],
                       {node: p['inventory'] for node, p in points.items()}, None, None,
                       constraints['depot_node'], constraints['daily_move_budget'],
                       eligible_receivers={n for n,p in points.items() if p['status']=='shortage'})
    if task:
        if points[task['destination']]['status']!='shortage':
            return None
        task.update(origin_name=nodes[task['origin']]['name'], destination_name=nodes[task['destination']]['name'],
                    origin_parking=nodes[task['origin']]['parking_id'], destination_parking=nodes[task['destination']]['parking_id'],
                    total_distance_km=(task['empty_distance_m'] + task['loaded_distance_m']) / 1000,
                    duration_minutes=task['estimated_duration_s'] / 60,
                    status='advisory_not_executed',
                    limitation='Hypothetical transport graph and operator assumptions; no surveyed road route')
        first=points[task['destination']].get('first_shortage_minute')
        task['estimated_first_shortage_minute']=first
        task['may_arrive_after_shortage']=first is not None and task['duration_minutes']>first
    return task
