"""Synthetic-only compatibility archive, never a connection to a legacy database."""
import json
from pathlib import Path
from v2.foundation import Store

def backfill(path=Path('v2/fixtures/legacy26.json')):
    doc=json.loads(Path(path).read_text())
    if doc.get('synthetic') is not True or len(doc['records'])!=26:raise ValueError('synthetic_26_table_archive_required')
    result={}
    for row in doc['records']:
        if not row['legacy_id'].startswith('synthetic-') or row['body'].get('synthetic') is not True:raise ValueError('non_synthetic_input')
        store=Store(row['owner'])
        with store.transaction() as tx:
            key=row['legacy_table']+':'+row['legacy_id'];prior=tx.get('legacy_compatibility',key)
            if prior and prior!=row:raise ValueError('legacy_id_conflict')
            tx.put('legacy_compatibility',key,row)
        result[row['legacy_table']]={'owner':row['owner'],'count':1,'id_preserved':True}
    return result

if __name__=='__main__':print(json.dumps(backfill(),sort_keys=True))
