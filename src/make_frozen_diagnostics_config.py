"""Build explicit diagnostics input from existing model outputs; never trains."""
import argparse
import json
from pathlib import Path

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--models', type=Path, required=True)
    p.add_argument('--nodes', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    a=p.parse_args()
    from prepare_forecast_inputs import sha256_file
    runs=[]
    for target in ('borrow','return'):
        folder=(a.models/target).resolve()
        meta=json.loads((folder/'metrics.json').read_text())
        if not (folder/'test_predictions.parquet').is_dir(): raise FileNotFoundError(folder/'test_predictions.parquet')
        if meta['input_contract']['source_sha256'].get('nodes.jsonl') != sha256_file(a.nodes):
            raise ValueError('Node registry does not match frozen model lineage')
        runs.append(dict(id='campus_'+target,directory=str(folder),nodes=str(a.nodes.resolve())))
    settings=dict(runs=runs,bootstrap_repetitions=1000,bootstrap_seed=20261112,day_block_lengths=[1,2,7],primary_block_length=2)
    with a.output.open('x') as stream: json.dump(settings,stream,ensure_ascii=False,indent=2)
if __name__=='__main__': main()
