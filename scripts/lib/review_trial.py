"""Machine-local, expiring reviewer trial and API-equivalent cost reporting."""
import json
import fcntl
import os
import re
import stat
import sys
from datetime import datetime, timezone
from pathlib import Path

STATE = Path.home() / '.local/state/k2b/reviewer-trial'
# USD per million tokens, checked 2026-10-27. Estimates, not subscription bills.
RATES = {
    'kimi-k2.7-code': (0.95, 0.19, 4.0),
    'kimi-k2.7-code-highspeed': (1.90, 0.38, 8.0),
    'kimi-for-coding-highspeed': (1.90, 0.38, 8.0),
    'k3.1-pro': (2.0, 0.20, 10.0),
    'kimi-k3.1-pro': (2.0, 0.20, 10.0),
    'k3.1-pro-highspeed': (4.0, 0.20, 20.0),
    'kimi-k3.1-pro-highspeed': (4.0, 0.20, 20.0),
}


def normalized_model(value):
    """Normalize reported IDs for pricing and comparisons, not request routing."""
    if not isinstance(value, str):
        return None
    aliases = {
        'kimi-k3.1-pro': 'k3.1-pro',
        'kimi-k3.1-pro-highspeed': 'k3.1-pro-highspeed',
        'kimi-k2.7-code-highspeed': 'kimi-for-coding-highspeed',
    }
    value = value.strip().lower()
    return aliases.get(value, value)


def model_matches(requested, returned):
    """Require a returned identity; compare the known protocol aliases."""
    return bool(normalized_model(returned)) and normalized_model(requested) == normalized_model(returned)


def private_read(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, encoding='utf-8') as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600:
            raise ValueError(f'Trial file must be regular, owned locally and mode 0600: {path}')
        fcntl.flock(handle, fcntl.LOCK_SH)
        return handle.read()


def configure(env, now=None):
    """Mutate only the child environment. An expired trial uses normal settings."""
    if env.get('K2B_REVIEW_TRIAL_DISABLED') == '1':
        return
    if env.get('K2B_LLM_PROVIDER', 'kimi').lower() != 'kimi':
        return
    if env.get('K2B_LLM_MODEL') and not model_matches(env['K2B_LLM_MODEL'], 'kimi-for-coding-highspeed'):
        return
    selected_host = env.get('KIMI_API_HOST', 'https://api.kimi.com/coding').rstrip('/')
    if selected_host not in {'https://api.kimi.com/coding', 'https://api.kimi.ai/coding'}:
        return
    config_path = STATE / 'config.json'
    if not config_path.exists():
        return
    config = json.loads(private_read(config_path))
    if not isinstance(config.get('id'), str) or not config['id'].strip():
        raise ValueError('Trial id must be a nonempty string')
    now = now or datetime.now(timezone.utc)
    expiry = datetime.fromisoformat(config['expires_at'])
    if expiry.tzinfo is None:
        raise ValueError('Trial expiry requires a timezone')
    if now >= expiry:
        return
    if config['model'] != 'kimi-for-coding-highspeed':
        raise ValueError('Unexpected trial model')
    credential_source = config.get('credential_source', 'dedicated-key')
    credential_env = {}
    if credential_source == 'dedicated-key':
        key = private_read(STATE / 'key').strip()
        if not key or any(c.isspace() for c in key):
            raise ValueError('Invalid trial credential')
        credential_env['KIMI_API_KEY'] = key
    elif credential_source == 'local-default':
        credential_file = Path.home() / '.k2b-env'
        match = re.search(
            r'^\s*(?:export\s+)?KIMI_API_KEY\s*=\s*(?:"([^"]+)"|\x27([^\x27]+)\x27|([^\s#]+))',
            private_read(credential_file), re.MULTILINE,
        )
        if match is None:
            raise ValueError('Local trial credential is absent')
        key = next(value for value in match.groups() if value is not None)
        if not key or any(c.isspace() for c in key):
            raise ValueError('Invalid local trial credential')
        credential_env.update(KIMI_API_KEY=key, K2B_ENV_FILE=str(credential_file))
    else:
        raise ValueError('Unexpected trial credential source')
    env.update(credential_env, K2B_LLM_PROVIDER='kimi',
               K2B_LLM_MODEL=config['model'], KIMI_DEFAULT_MODEL=config['model'],
               K2B_LLM_MAX_TOKENS=env.get('K2B_LLM_MAX_TOKENS') or '32768',
               KIMI_API_HOST=selected_host,
               K2B_REVIEW_TRIAL_ID=config['id'])
    if credential_source == 'dedicated-key':
        env.pop('K2B_ENV_FILE', None)
    return dict(id=config['id'], model=config['model'], expires_at=config['expires_at'],
                credential_source=credential_source, max_tokens=env['K2B_LLM_MAX_TOKENS'])


def estimate(model, usage):
    rate = RATES.get(normalized_model(model))
    prompt, output = usage.get('prompt_tokens'), usage.get('completion_tokens')
    if not rate or any(type(v) is not int or v < 0 for v in (prompt, output)):
        return None
    # Cache semantics vary by protocol. Use an explicit uncached upper estimate
    # until a compatible cached-input breakdown has been verified.
    return round((prompt * rate[0] + output * rate[2]) / 1_000_000, 8)


def record(requested, actual, usage, duration, outcome, scope, validation_error=None):
    STATE.mkdir(parents=True, exist_ok=True, mode=0o700)
    directory = STATE.lstat()
    if not stat.S_ISDIR(directory.st_mode) or directory.st_uid != os.getuid() or stat.S_IMODE(directory.st_mode) != 0o700:
        raise ValueError('Trial state must be a locally owned directory with mode 0700')
    actual = actual if isinstance(actual, str) and actual.strip() else None
    usage = usage if isinstance(usage, dict) else {}
    entry = dict(timestamp=datetime.now(timezone.utc).isoformat(),
                 trial_id=os.environ.get('K2B_REVIEW_TRIAL_ID'),
                 requested_model=requested, returned_model=actual,
                 usage=usage, duration_seconds=round(duration, 3),
                 outcome=outcome, scope=scope,
                 validation_error=validation_error,
                 outcome_stage='Model response validation, not final ship approval',
                 estimated_usd_uncached=estimate(actual, usage),
                 pricing_date='2026-10-27',
                 billing='API-equivalent estimate, not subscription charge',
                 coverage='Final response only; interrupted or retried requests may add unreported usage')
    path = STATE / 'usage.jsonl'
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'a') as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600:
            raise ValueError('Usage log must be regular, owned locally and mode 0600')
        fcntl.flock(handle, fcntl.LOCK_EX)
        handle.write(json.dumps(entry) + '\n')
        handle.flush()
        os.fsync(handle.fileno())


def try_record(*args, **kwargs):
    """A telemetry failure must not discard an already-paid review result."""
    try:
        record(*args, **kwargs)
    except Exception as exc:
        print(f'[review-trial] Usage log failed ({type(exc).__name__}); cost coverage is incomplete', file=sys.stderr)


def report():
    path = STATE / 'usage.jsonl'
    entries = [json.loads(line) for line in private_read(path).splitlines()] if path.exists() else []
    groups = {}
    for entry in entries:
        key = entry['returned_model'] or entry['requested_model']
        group = groups.setdefault(key, dict(reviews=0, seconds=0, input_tokens=0,
                                          output_tokens=0, estimated_usd_uncached=0,
                                          unknown_cost=0, outcomes={}))
        group['reviews'] += 1
        group['seconds'] += entry['duration_seconds']
        group['input_tokens'] += entry['usage'].get('prompt_tokens') or 0
        group['output_tokens'] += entry['usage'].get('completion_tokens') or 0
        cost = entry['estimated_usd_uncached']
        group['unknown_cost'] += cost is None
        group['estimated_usd_uncached'] += cost or 0
        outcome = entry['outcome']
        group['outcomes'][outcome] = group['outcomes'].get(outcome, 0) + 1
    return groups


if __name__ == '__main__':
    print(json.dumps(report(), indent=2))
