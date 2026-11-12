"""Read only scoped lab metadata; never print container environment or credentials."""
import argparse
import json
import subprocess
from pathlib import Path
parser=argparse.ArgumentParser()
parser.add_argument('--project',default='estateflow-v2-isolated-verify20261110')
parser.add_argument('--env-file',default='infra/v2/.env.verification.local',choices=('infra/v2/.env.local','infra/v2/.env.verification.local'))
parser.add_argument('--output',default='.artifacts/v2/final/isolation.json')
args=parser.parse_args()
if not (args.project=='estateflow-v2-isolated' or args.project.startswith('estateflow-v2-isolated-verify')):raise ValueError('isolated V2 projects only')
base=['docker','compose','--env-file',args.env_file,'-f','infra/v2/compose.yaml','-p',args.project]
ids=subprocess.check_output(base+['ps','-aq'],text=True).split()
assert ids,'no scoped containers'
containers=json.loads(subprocess.check_output(['docker','inspect',*ids],text=True))
# Ephemeral test clients intentionally mount ignored evidence; they are not runtime services.
containers=[item for item in containers if item['Config']['Labels'].get('com.docker.compose.oneoff','false').lower()!='true']
assert len(containers)==20, 'expected 20 declared lab containers including completed migration'
result={'project':args.project,'containers':[]}
for item in containers:
    service=item['Config']['Labels']['com.docker.compose.service']
    host=item['HostConfig'];assert not host['PortBindings'] and host['NetworkMode']!='host'
    networks=list(item['NetworkSettings']['Networks']);assert networks==[args.project+'_private']
    mounts=item['Mounts']
    assert not any(m['Type']=='bind' for m in mounts),'unexpected host mount'
    assert all(m['Name'].startswith(args.project+'_') for m in mounts if m['Type']=='volume')
    if service not in ('postgres','rabbitmq','migrate'):assert item['Config']['User']=='10001'
    result['containers'].append({'service':service,'running':item['State']['Running'],'user':item['Config']['User'],'host_ports':False,'networks':networks,'volumes':[m['Name'] for m in mounts if m['Type']=='volume']})
network=json.loads(subprocess.check_output(['docker','network','inspect',args.project+'_private'],text=True))[0]
assert network['Internal'] is True
result['network_internal']=True
path=Path(args.output)
if path.parent.resolve()!=Path('.artifacts/v2/final').resolve():raise ValueError('only ignored final evidence output permitted')
path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(result,indent=2)+'\n')
print(f'{len(containers)} scoped containers: no host ports/bind mounts; isolated internal network; runtime user10001 PASS')
