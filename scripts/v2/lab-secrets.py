"""Create synthetic lab credentials without printing secrets or replacing existing files."""
from pathlib import Path
import os
import secrets
import argparse

OWNERS = 'listener source_parser pre_ai ai catalog search notifications identity control taxonomy content analytics lab_producer lab_consumer'.split()
parser = argparse.ArgumentParser()
parser.add_argument('--output', default='.env.local', choices=('.env.local', '.env.verification.local'))
args = parser.parse_args()
target = Path(__file__).resolve().parents[2] / 'infra/v2' / args.output
if target.exists():
    print('Lab credentials already exist; preserved.')
else:
    values = {'EFV2_MODE': 'FAKE_ONLY', 'POSTGRES_PASSWORD': secrets.token_hex(24), 'RABBITMQ_DEFAULT_USER': 'efv2_lab', 'RABBITMQ_DEFAULT_PASS': secrets.token_hex(24)}
    values['EFV2_ENV_FILE'] = args.output
    values['EFV2_AUTH_KEY'] = secrets.token_hex(32)
    values['EFV2_FAKE_BOT_TOKEN'] = 'synthetic:' + secrets.token_hex(24)
    values['EFV2_ADMIN_DSN'] = f"postgresql://postgres:{values['POSTGRES_PASSWORD']}@postgres/estateflow_v2_lab"
    values['EFV2_BROKER_URL'] = f"amqp://efv2_lab:{values['RABBITMQ_DEFAULT_PASS']}@rabbitmq:5672/%2F"
    for owner in OWNERS:
        password = secrets.token_hex(24)
        values[f'EFV2_PASSWORD_{owner.upper()}'] = password
        values[f'EFV2_MIGRATE_PASSWORD_{owner.upper()}'] = secrets.token_hex(24)
        values[f'EFV2_DSN_{owner.upper()}'] = f'postgresql://ef_{owner}_app:{password}@postgres/estateflow_v2_lab'
    target.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as output:
        output.writelines(f'{key}={value}\n' for key, value in values.items())
    print('Created ignored synthetic lab credentials (mode 0600).')
