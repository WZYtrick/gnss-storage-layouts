"""Shared, side-effect-free paths and artifact helpers for EXP-027."""
from pathlib import Path
import datetime as dt
import hashlib
import importlib.util
import json
import os
import sys

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parent
BASE = ROOT.parent
OLD = BASE / 'EXP-024'
PRIOR = BASE / 'EXP-025'
PREVIOUS = BASE / 'EXP-026'
PYTHON = BASE / 'EXP-001/.venv/Scripts/python.exe'
sys.path.insert(0, str(OLD))
import storage as storage
s = storage
CORE = ['W', 'C', 'Cstar', 'T', 'F']
CONTRACTS = ['ordered_dense', 'unordered_dense', 'unordered_arrow']
ENV_THREADS = dict(PYTHONIOENCODING='utf-8', PYTHONDONTWRITEBYTECODE='1', OMP_NUM_THREADS='1', OPENBLAS_NUM_THREADS='1', MKL_NUM_THREADS='1', NUMEXPR_NUM_THREADS='1')

def utc():
    return dt.datetime.now(dt.timezone.utc).isoformat()

def load(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))

def dump(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + '.tmp')
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    os.replace(tmp, path)

def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda: f.read(1048576), b''):
            h.update(chunk)
    return h.hexdigest()

def entry(path):
    path = Path(path).resolve()
    return dict(path=str(path), bytes=path.stat().st_size, sha256=sha(path))

def verify_entries(items):
    for item in items:
        p = Path(item['path'])
        assert p.stat().st_size == item['bytes'] and sha(p) == item['sha256'], str(p)

def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    obj = importlib.util.module_from_spec(spec)
    sys.modules[name] = obj
    spec.loader.exec_module(obj)
    return obj

def inputs(dataset):
    p = ROOT / 'inputs' / dataset
    return tuple(load(p / (name + '.json')) for name in ['sources', 'queries', 'truth', 'layouts'])
