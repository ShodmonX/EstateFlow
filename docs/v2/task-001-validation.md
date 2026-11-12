# Task 001 validation, review and exact changed paths

Date: 2026-11-09. Baseline main / addb856a99d24946ef127384121b99de371c017b. Status: draft documentation/contracts delivered for review, Task 002 NOT APPROVED / NOT STARTED.

## Actual commands and results

| Command | Observed result / limit |
|---|---|
| git status --short; git branch --show-current; git rev-parse HEAD; git log -5 --oneline | main at baseline SHA; pre-existing untracked .agents/, AGENTS.md, CODEX_START_PROMPT.md, CODEX_TASK_001_PROMPT.md, README_INSTALL.md, docs/v2/ |
| python3 importlib.util.find_spec('jsonschema') probe; importlib.metadata.version('jsonschema') | Existing jsonschema 4.10.3; Draft202012Validator and RefResolver available |
| First python3 /tmp/validate_estateflow_task001.py | Failed with ModuleNotFoundError: referencing; no schema success claimed for that attempt, no installation |
| PYTHONDONTWRITEBYTECODE=1 python3 /tmp/validate_estateflow_task001.py after using existing RefResolver | PASS: 17 valid Draft 2020-12 schemas; 25 valid scenario wrappers; 47 event instances, 38 accepted / 9 rejected as expected; FormatChecker enabled, local store only, HTTP/HTTPS resolution prohibited |
| PYTHONDONTWRITEBYTECODE=1 python3 /tmp/check_estateflow_task001.py | PASS: 42 strict JSON files, 26 exact table-owner rows, 26 declared FK edges / 7 cross-owner, 33 local schema references; content-hash/scope/newline/trailing-whitespace checks |
| git diff --check | Exit 0. Since V2 artifacts are untracked, this is supplemented by direct whitespace and content-hash checks |
| git diff --exit-code; git rev-parse HEAD; git status --short | Tracked content and HEAD unchanged; final scope check ensures all changed/untracked additions fall in authorized paths |

No dependencies installed, no app tests/consumer state machine tests, V1 compatibility suite, runtime import changes, DB/migration/grants/sequence tests, RabbitMQ/container/restore/codec checks or network services executed. Synthetic expected state transitions are specifications, not passing implementation tests. None of Phase 0's runtime defects is closed.

## Reproduce schema validation offline with the existing library

The following is the actual schema validation script stored temporarily at /tmp/validate_estateflow_task001.py for this run. It remains documented here so no checked-in runtime/test Python is needed. Run from repository root with PYTHONDONTWRITEBYTECODE=1 python3 and this script on stdin or in a temporary file. The existing library is required; do not install it under Task 001.

```python
from pathlib import Path
import json,hashlib,subprocess,ast,re
from jsonschema import Draft202012Validator,FormatChecker,RefResolver
S=Path('contracts/v2/schemas');F=Path('tests/fixtures/v2/contracts')
ss={p.name:json.loads(p.read_text()) for p in sorted(S.glob('*.json'))}
store={s['$id']:s for s in ss.values()}
def offline(uri):raise RuntimeError('Network resolution forbidden: '+uri)
def validator(schema):
 return Draft202012Validator(schema,resolver=RefResolver.from_schema(schema,store=store,handlers={'http':offline,'https':offline}),format_checker=FormatChecker())
for s in ss.values():Draft202012Validator.check_schema(s)
valid=invalid=steps=0
for p in sorted(F.glob('*.json')):
 scenario=json.loads(p.read_text());validator(ss['scenario.v1.schema.json']).validate(scenario)
 for step in scenario['steps']:
  steps+=1; errors=list(validator(ss[step['schema_file']]).iter_errors(step['event']))
  assert bool(errors)!=step['schema_valid'],(p.name,step['event']['event_type'],[e.message for e in errors])
  if errors:invalid+=1
  else:valid+=1
print(f'PASS: {len(ss)} schemas valid Draft 2020-12; {len(list(F.glob("*.json")))} scenarios; {steps} events; {valid} accepted; {invalid} rejected as expected (format checks enabled, offline registry).')
print('Consumer transition expectations are specifications; no application/state-machine execution performed.')
```

## Standard-library and scope verification

The following actual check script was stored at /tmp/check_estateflow_task001.py. The before-snapshot is a temporary JSON mapping of all 322 pre-existing tracked/nonignored file paths to SHA256, captured before edits using git ls-files --cached --others --exclude-standard -z and hashlib.sha256. It includes the pre-existing untracked materials, so unrelated changes/deletions would fail. It does not claim anything about ignored deployment data, which was not touched. The temporary snapshot is needed to reproduce the session-relative hash comparison; JSON/reference/ownership checks can be rerun independently.

```python
from pathlib import Path
import json,ast,re,hashlib,subprocess
roots=[Path('contracts/v2/schemas'),Path('tests/fixtures/v2/contracts')]
def no_duplicates(pairs):
 d={}
 for k,v in pairs:
  if k in d:raise ValueError('duplicate JSON key '+k)
  d[k]=v
 return d
def no_constant(x):raise ValueError('nonstandard JSON '+x)
files=[p for r in roots for p in sorted(r.glob('*.json'))]
for p in files:json.loads(p.read_text(),object_pairs_hook=no_duplicates,parse_constant=no_constant)
print('stdlib JSON PASS:',len(files),'files; no duplicate keys or NaN/Infinity')
text=Path('docs/v2/database-ownership.md').read_text();owners={}
for line in text.splitlines():
 if not line.startswith('| ') or ' | ' not in line:continue
 cells=[c.strip() for c in line.split('|')[1:-1]]
 if len(cells)==3 and cells[0] in ['listener','source_parser','pre_ai','ai','catalog','search','notifications','identity','control','taxonomy','content','analytics']:
  for t in cells[1].split('; '):
   if t=='None':continue
   assert t not in owners,t
   owners[t]=cells[0]
models={};edges=[]
for p in Path('src/estateflow/models').glob('*.py'):
 for c in ast.parse(p.read_text()).body:
  if not isinstance(c,ast.ClassDef):continue
  table=next((ast.literal_eval(n.value) for n in c.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='__tablename__' for t in n.targets)),None)
  if not table:continue
  models[table]=str(p)
  for n in ast.walk(c):
   if isinstance(n,ast.Call) and isinstance(n.func,ast.Name) and n.func.id=='ForeignKey':edges.append((table,ast.literal_eval(n.args[0]).split('.')[0]))
assert set(owners)==set(models),(set(models)-set(owners),set(owners)-set(models))
print('ownership PASS:',len(owners),'tables;',len(edges),'declared FK edges;',sum(owners[a]!=owners[b] for a,b in edges),'cross-owner edges require projections/scalar references')
ss={p.name:json.loads(p.read_text()) for p in roots[0].glob('*.json')}
ids={s['$id']:s for s in ss.values()};refs=0
def walk(x):
 global refs
 if isinstance(x,dict):
  if '$ref' in x:
   uri,_,pointer=x['$ref'].partition('#');obj=ids[uri]
   for token in pointer.lstrip('/').split('/') if pointer else []:obj=obj[token.replace('~1','/').replace('~0','~')]
   refs+=1
  for v in x.values():walk(v)
 elif isinstance(x,list):
  for v in x:walk(v)
for s in ss.values():walk(s)
print('local reference PASS:',refs,'schema references; no remote resolution')
initial=json.loads(Path('/tmp/estateflow-task001-before.json').read_text())
now=subprocess.check_output(['git','ls-files','--cached','--others','--exclude-standard','-z']).decode().split('\0')
changed=[]
for p in sorted(set(initial)|{x for x in now if x}):
 f=Path(p)
 h=hashlib.sha256(f.read_bytes()).hexdigest() if f.is_file() else None
 if initial.get(p)!=h:changed.append(p)
allowed=[r'^docs/v2/[^/]+\.md$',r'^docs/v2/(?:tasks|decisions)/[^/]+\.md$',r'^contracts/v2/schemas/[^/]+\.json$',r'^tests/fixtures/v2/contracts/[^/]+\.json$']
assert all(any(re.match(rule,p) for rule in allowed) for p in changed),changed
for p in changed:
 s=Path(p).read_text();assert s.endswith('\n'),p
 assert all(l.rstrip()==l for l in s.splitlines()),p
print('scope PASS:',len(changed),'changed/created paths, all authorized; no deletions/unrelated changes')
Path('/tmp/estateflow-task001-changed-paths.json').write_text(json.dumps(changed,indent=2)+'\n')
```

## Architecture review after drafting

- Contexts and sole table owners reconcile all 26 ORM tables; every FK is owner-local or explicitly replaced with scalar IDs/projections. T0 retained, T1–T6 atomicity and compensation spelled out. No runtime import/SQL/migration changes were made.
- No same-name exchange type change in active drafts: V1 estateflow.events remains DIRECT, V2 estateflow.v2.events is TOPIC. Topology, budget, dispatcher and queue replication details still D02.
- Core events have closed payloads, producer/type/kind/version constraints and independent immutable identity layers. Domain equality/stale revision/order/authorization/batch and compensation checks are requirements beyond schema validation. Negative schema instances and schema-valid semantic conflicts are distinguished.
- AI/media initial co-deployment and notifications single owner are explicitly confirmed constraints. Catalog owns review/merge/attachment references. Other allocations remain proposed D01.
- Replay preserves event identity; rebuilding projections uses isolated inbox namespace and prohibits sends. Raw/quarantine/media capabilities, production listing parity and field-minimized audit/metric bindings remain D07 gaps, not implicitly production-safe payloads.
- Unknown sends remain OPEN D08; outbox/inbox cannot prove exactly-once Telegram delivery. Phase 0 findings require later executable crash/restart/parity proof.
- Acceptance with product/operational approval is pending; completing this draft never authorizes Task 002. Deferred noncore schemas block their domain extraction, not the bounded synthetic foundation proposal.

## Exact changed paths

This manifest lists only Task 001 differences from the initial content snapshot. Existing audit/review/dependency/event-inventory history is retained unchanged except the explicitly reconciled table ownership proposal. No application, V1 contract, migration, Compose, CI, configuration, secret or unrelated user path changed.

Total: 56 paths.

- `contracts/v2/schemas/candidate.extracted.v1.schema.json`
- `contracts/v2/schemas/candidate.prechecked.v1.schema.json`
- `contracts/v2/schemas/catalog.listing.changed.v1.schema.json`
- `contracts/v2/schemas/catalog.listing.retracted.v1.schema.json`
- `contracts/v2/schemas/envelope.v1.schema.json`
- `contracts/v2/schemas/media.failed.v1.schema.json`
- `contracts/v2/schemas/media.ready.v1.schema.json`
- `contracts/v2/schemas/media.unavailable.v1.schema.json`
- `contracts/v2/schemas/parser.batch.completed.v1.schema.json`
- `contracts/v2/schemas/parser.candidate.parsed.v1.schema.json`
- `contracts/v2/schemas/parser.candidate.rejected.v1.schema.json`
- `contracts/v2/schemas/raw.message.deleted.v1.schema.json`
- `contracts/v2/schemas/raw.message.edited.v1.schema.json`
- `contracts/v2/schemas/raw.message.received.v1.schema.json`
- `contracts/v2/schemas/scenario.v1.schema.json`
- `contracts/v2/schemas/source.album.changed.v1.schema.json`
- `contracts/v2/schemas/types.v1.schema.json`
- `docs/v2/architecture-v2.md`
- `docs/v2/current-state.md`
- `docs/v2/database-ownership.md`
- `docs/v2/decisions/ADR-001-platform-boundaries.md`
- `docs/v2/decisions/ADR-002-initial-domain-boundaries.md`
- `docs/v2/decisions/ADR-003-v2-broker-isolation.md`
- `docs/v2/decisions/ADR-004-identity-lifecycle.md`
- `docs/v2/decisions/ADR-005-unknown-send-recovery.md`
- `docs/v2/event-contracts.md`
- `docs/v2/implementation-plan.md`
- `docs/v2/table-ownership-map.md`
- `docs/v2/task-001-decisions.md`
- `docs/v2/task-001-validation.md`
- `docs/v2/tasks/001-boundaries-event-contracts.md`
- `tests/fixtures/v2/contracts/01-create-edit-edit-delete.json`
- `tests/fixtures/v2/contracts/02-delete-before-create.json`
- `tests/fixtures/v2/contracts/03-duplicate-reorder.json`
- `tests/fixtures/v2/contracts/04-uncertain-platform-order.json`
- `tests/fixtures/v2/contracts/05-album-late-member.json`
- `tests/fixtures/v2/contracts/06-digest-reparse.json`
- `tests/fixtures/v2/contracts/07-parser-quarantine.json`
- `tests/fixtures/v2/contracts/08-precheck-extract-review.json`
- `tests/fixtures/v2/contracts/09-manual-review.json`
- `tests/fixtures/v2/contracts/10-parent-child-merge.json`
- `tests/fixtures/v2/contracts/11-multi-source-retraction.json`
- `tests/fixtures/v2/contracts/12-media-failed.json`
- `tests/fixtures/v2/contracts/12-media-ready.json`
- `tests/fixtures/v2/contracts/12-media-unavailable.json`
- `tests/fixtures/v2/contracts/13-replay-recovery.json`
- `tests/fixtures/v2/contracts/14-projection-rebuild.json`
- `tests/fixtures/v2/contracts/15-unknown-major.json`
- `tests/fixtures/v2/contracts/16-invalid-envelope-payload.json`
- `tests/fixtures/v2/contracts/17-semantic-mismatch.json`
- `tests/fixtures/v2/contracts/18-buffer-unavailable.json`
- `tests/fixtures/v2/contracts/19-precheck-duplicate-evidence.json`
- `tests/fixtures/v2/contracts/20-review-rejected.json`
- `tests/fixtures/v2/contracts/21-stale-media-result.json`
- `tests/fixtures/v2/contracts/22-invalid-core-state.json`
- `tests/fixtures/v2/contracts/23-stale-extraction-after-delete.json`
