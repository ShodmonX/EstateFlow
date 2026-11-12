"""Real container restart tests restricted to the dedicated lab Compose project."""
from pathlib import Path
import subprocess
import argparse
import time

root = Path(__file__).resolve().parents[2]
parser = argparse.ArgumentParser()
parser.add_argument('--project', default='estateflow-v2-isolated')
parser.add_argument('--env-file', default='infra/v2/.env.local', choices=('infra/v2/.env.local','infra/v2/.env.verification.local'))
args=parser.parse_args()
if not (args.project=='estateflow-v2-isolated' or args.project.startswith('estateflow-v2-isolated-verify')):
    raise ValueError('Only isolated V2 lab projects permitted')
base = ['docker', 'compose', '--env-file', args.env_file, '-f', 'infra/v2/compose.yaml', '-p', args.project]


def command(*args):
    subprocess.run(base + list(args), cwd=root, check=True)


def probe(action):
    command('run', '--rm', '--no-deps', '-v', f'{root}/v2:/app/v2:ro', '-v', f'{root}/contracts:/app/contracts:ro', 'v2-test-runner', 'python', '-m', 'v2.tests.foundation.restart_probe', action)


def healthy(service):
    for _ in range(45):
        result = subprocess.run(base + ['exec', '-T', service] + (['pg_isready', '-U', 'postgres', '-d', 'estateflow_v2_lab'] if service == 'postgres' else ['rabbitmq-diagnostics', '-q', 'ping']), cwd=root, capture_output=True)
        if result.returncode == 0:
            return
        time.sleep(1)
    raise RuntimeError('Lab service failed recovery readiness')


probe('prepare')
command('restart', 'rabbitmq')
healthy('rabbitmq')
probe('after_broker')
command('restart', 'postgres')
healthy('postgres')
probe('after_db')
command('stop', 'rabbitmq')
try:
    probe('outage')
finally:
    command('start', 'rabbitmq')
    healthy('rabbitmq')
probe('after_outage')
print('FND-05/FND-23/FND-24 genuine scoped restart/outage scenarios PASS')
