"""Privileged synthetic-only dump/restore rehearsal inside the dedicated V2 cluster.

Creates a new database; never drops or overwrites any database or volume.
No dump contents or environment values are printed or written to reports.
"""
import argparse
import hashlib
import json
import subprocess
from pathlib import Path
from uuid import uuid4
parser=argparse.ArgumentParser()
parser.add_argument('--project',default='estateflow-v2-isolated-verify20261110')
parser.add_argument('--env-file',default='infra/v2/.env.verification.local',choices=('infra/v2/.env.local','infra/v2/.env.verification.local'))
args=parser.parse_args()
if not args.project.startswith('estateflow-v2-isolated'):raise ValueError('V2 lab only')
base=['docker','compose','--env-file',args.env_file,'-f','infra/v2/compose.yaml','-p',args.project,'exec','-T','postgres']
source='estateflow_v2_lab';target='estateflow_v2_restore_'+uuid4().hex[:12]
subprocess.run(base+['createdb','-U','postgres',target],check=True,capture_output=True)
dump=subprocess.check_output(base+['pg_dump','-U','postgres','--dbname',source])
subprocess.run(base+['psql','-X','-v','ON_ERROR_STOP=1','-U','postgres','--dbname',target],input=dump,check=True,capture_output=True)
owners='listener source_parser pre_ai ai catalog search notifications identity control taxonomy content analytics lab_producer lab_consumer'.split()
checks=[]
for owner in owners:
    for table in ('records','outbox','inbox','retry_schedule','parked_events','replay_audit','revisions','schema_versions'):
        sql=f"SELECT count(*)::text || ':' || md5(coalesce(string_agg(row_to_json(t)::text,',' ORDER BY row_to_json(t)::text),'')) FROM {owner}.{table} t"
        def digest(database):return subprocess.check_output(base+['psql','-X','-At','-U','postgres','--dbname',database,'-c',sql],text=True).strip()
        original=digest(source);restored=digest(target);assert original==restored,(owner,table)
        checks.append({'owner':owner,'table':table,'count':int(original.split(':')[0]),'hash_equal':True})
report={'project':args.project,'source_database':source,'restored_database':target,'dump_sha256':hashlib.sha256(dump).hexdigest(),'checks':checks,'limitations':['Same private PostgreSQL cluster; roles already existed. No PITR, offhost backup or production restore proof.','Restored database preserved; no cleanup/destructive operation executed.']}
Path('.artifacts/v2/final/restore.json').write_text(json.dumps(report,indent=2)+'\n')
print(f'Synthetic dump/restore PASS: {len(checks)} table count/content digests equal; new database preserved')
