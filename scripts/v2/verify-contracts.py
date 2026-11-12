"""Offline strict JSON/schema check using only the standard library unless installed."""
import json
from pathlib import Path

root = Path(__file__).resolve().parents[2]

def pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise ValueError('duplicate JSON key')
        result[key] = value
    return result

def reject_constant(value):
    raise ValueError('non-finite JSON value')

paths = sorted((root / 'contracts/v2/schemas').glob('*.json')) + sorted((root / 'tests/fixtures/v2/contracts').glob('*.json'))
docs = {path: json.loads(path.read_text(), object_pairs_hook=pairs, parse_constant=reject_constant) for path in paths}
print(f'Strict JSON: {len(docs)} files PASS')
try:
    from jsonschema import Draft202012Validator
except ImportError:
    print('Schema validation NOT_RUN: jsonschema not installed; no dependency installed')
else:
    schemas = {doc['$id']: doc for path, doc in docs.items() if path.parent.name == 'schemas'}
    for doc in schemas.values():
        Draft202012Validator.check_schema(doc)
    def refs(value):
        if isinstance(value, dict):
            if '$ref' in value:
                target = value['$ref'].split('#')[0]
                if target and target not in schemas:
                    raise ValueError('nonlocal unresolved schema reference')
            for child in value.values():
                refs(child)
        elif isinstance(value, list):
            for child in value:
                refs(child)
    for doc in schemas.values():
        refs(doc)
    print(f'Draft202012 schema/meta/reference checks: {len(schemas)} schemas PASS (offline)')
