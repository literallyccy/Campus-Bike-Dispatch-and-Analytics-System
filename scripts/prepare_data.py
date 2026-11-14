from pathlib import Path
import gzip,shutil,hashlib,json
ROOT=Path(__file__).resolve().parents[1]
manifest=json.loads((ROOT/'FILE_MANIFEST.json').read_text())
for rel,expected in manifest.items():
 p=ROOT/rel
 if not p.is_file() or hashlib.sha256(p.read_bytes()).hexdigest()!=expected:raise RuntimeError('Missing or changed: '+rel)
for source in sorted((ROOT/'data/packed').glob('*.gz')):
 target=ROOT/'data/simulated/campus_v2'/source.name[:-3];target.parent.mkdir(parents=True,exist_ok=True)
 with gzip.open(source,'rb') as f:
  if target.exists():
   if hashlib.sha256(target.read_bytes()).digest()!=hashlib.sha256(f.read()).digest():raise RuntimeError('Refuse overwrite: '+str(target))
  else:
   with target.open('wb') as out:shutil.copyfileobj(f,out)
print('All bundled hashes verified; complete campus v2 inputs restored.')
