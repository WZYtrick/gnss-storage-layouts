"""Portable helpers for the CMSDA revision experiments (EXP-028)."""
from pathlib import Path
import datetime as dt
import hashlib
import json
import os
import sys

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parent
CORE = ['W', 'C', 'Cstar', 'T', 'F']
CONTRACTS = ['ordered_dense', 'unordered_dense', 'unordered_arrow']
METHODS = CORE + ['GeoRinex_R', 'GeoRinex_H']
ENV_THREADS = dict(PYTHONIOENCODING='utf-8', PYTHONDONTWRITEBYTECODE='1',
                  OMP_NUM_THREADS='1', OPENBLAS_NUM_THREADS='1',
                  MKL_NUM_THREADS='1', NUMEXPR_NUM_THREADS='1')
for name, value in ENV_THREADS.items():
    os.environ[name] = value
import storage as s

def utc():
    return dt.datetime.now(dt.timezone.utc).isoformat()

def load(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))

def dump(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    os.replace(temporary, path)

def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda: f.read(1048576), b''):
            h.update(chunk)
    return h.hexdigest()

def entry(path):
    path = Path(path).resolve()
    return dict(path=str(path), bytes=path.stat().st_size, sha256=sha(path))

def inputs(dataset):
    folder = ROOT / 'inputs' / dataset
    return tuple(load(folder / (name + '.json')) for name in ['sources', 'queries', 'truth', 'layouts'])

def local_now():
    return dt.datetime.now(dt.timezone(dt.timedelta(hours=8)))

def in_window(now=None):
    now = now or local_now()
    return 1 <= now.hour < 8

def seconds_left(now=None):
    now = now or local_now()
    return max(0., (now.replace(hour=8, minute=0, second=0, microsecond=0)-now).total_seconds()) if in_window(now) else 0.

def code_manifest():
    return {p.name: sha(p) for p in sorted(ROOT.glob('*.py'))}

def verify_code(expected):
    actual = code_manifest()
    if actual != expected:
        raise RuntimeError('Experiment code differs from the frozen campaign; create a new version.')
