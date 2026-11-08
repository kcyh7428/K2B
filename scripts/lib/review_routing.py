"""Bounded, same-packet Qwen review and standard Kimi escalation.

Network work runs in disposable subprocesses. Socket activity cannot extend the
wall deadline and a terminated worker has no access to result publication.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import time
import urllib.request
from urllib.parse import urlparse

QWEN_MODEL = '/Users/keithcheung/Models/mlx/Qwen3.8-27B-4bit'
KIMI_MODEL = 'kimi-for-coding'
KIMI_URL = 'https://api.kimi.com/coding/v1'
TOKEN_LIMIT = 12000
LOCAL_SECONDS = 120
OUTPUT_TOKENS = 8192
MAX_CLOUD_SECONDS = 350
POLICY = 'qwen-12000-120s-kimi28-v1'
MAX_WIRE_BYTES = 8 * 1024 * 1024


class RouteError(ValueError):
    pass


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise RouteError('provider redirect refused')


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate JSON key')
        result[key] = value
    return result


def strict_json(text):
    def invalid_constant(_value):
        raise ValueError('non-finite JSON number')
    return json.loads(text, object_pairs_hook=_unique_pairs, parse_constant=invalid_constant)


def parse_review(text):
    from minimax_review import SCHEMA_PATH, validate_review_output
    text = text.strip()
    fence = re.fullmatch(r'```(?:json)?\s*\n(.*)\n```', text, re.DOTALL)
    if fence:
        text = fence.group(1)
    review = strict_json(text)
    error = validate_review_output(review, json.loads(SCHEMA_PATH.read_text()))
    if error:
        raise ValueError('invalid review schema')
    if (review['verdict'] == 'approve') != (len(review['findings']) == 0):
        raise ValueError('inconsistent verdict and findings')
    for finding in review['findings']:
        if finding['line_end'] < finding['line_start']:
            raise ValueError('reversed finding lines')
    return review


def local_endpoint(config_path=None):
    path = Path(config_path or Path.home() / '.config/k2b/review-routing.json')
    default = 'http://127.0.0.1:18081/v1' if Path.home().name == 'keithmbpm2' else 'http://127.0.0.1:8081/v1'
    if not path.exists() and not path.is_symlink() and config_path is None:
        return default
    fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
    with os.fdopen(fd, 'r') as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise ValueError('routing config must be an owned private regular file')
        if info.st_size > 4096:
            raise ValueError('routing config oversized')
        config = strict_json(handle.read(4097))
    if not isinstance(config, dict) or set(config) != {'qwen_url'}:
        raise ValueError('routing config accepts only qwen_url')
    return validate_local_url(config['qwen_url'])


def validate_local_url(url):
    if not isinstance(url, str):
        raise ValueError('invalid local endpoint')
    parsed = urlparse(url)
    if (parsed.scheme != 'http' or parsed.hostname not in {'127.0.0.1', 'localhost', '::1'}
            or not parsed.port or parsed.username or parsed.password or parsed.query or parsed.fragment
            or parsed.path.rstrip('/') != '/v1'):
        raise ValueError('Qwen endpoint must be loopback HTTP /v1')
    return url.rstrip('/')


def _request(url, body=None, key=None):
    headers = {'Content-Type': 'application/json'}
    if key:
        headers['Authorization'] = 'Bearer ' + key
        headers['User-Agent'] = 'KimiCLI/1.0'
    request = urllib.request.Request(url, data=None if body is None else json.dumps(body).encode(), headers=headers)
    # No application proxy for the loopback server. Cloud follows machine policy.
    handlers = [_NoRedirect()]
    if urlparse(url).hostname in {'127.0.0.1', 'localhost', '::1'}:
        handlers.append(urllib.request.ProxyHandler({}))
    opener = urllib.request.build_opener(*handlers)
    return opener.open(request, timeout=120)


def _json_request(url, body=None, key=None):
    with _request(url, body, key) as response:
        data = response.read(MAX_WIRE_BYTES + 1)
    if len(data) > MAX_WIRE_BYTES:
        raise RouteError('provider response oversized')
    return strict_json(data.decode())


def _completion(base, body, key):
    text = []
    model = None
    finish = None
    usage = {}
    done = False
    size = 0
    with _request(base + '/chat/completions', body, key) as response:
        while True:
            line = response.readline(MAX_WIRE_BYTES + 1)
            if not line:
                break
            size += len(line)
            if size > MAX_WIRE_BYTES:
                raise RouteError('provider response oversized')
            line = line.decode().strip()
            if not line or line.startswith(':'):
                continue
            if not line.startswith('data:'):
                raise RouteError('invalid completion stream')
            data = line[5:].strip()
            if data == '[DONE]':
                done = True
                break
            chunk = strict_json(data)
            if chunk.get('model'):
                if model and model != chunk['model']:
                    raise RouteError('changing model identity')
                model = chunk['model']
            if chunk.get('usage'):
                usage = chunk['usage']
            choices = chunk.get('choices', [])
            if len(choices) > 1:
                raise RouteError('multiple completion choices')
            for choice in choices:
                if choice.get('index', 0) != 0:
                    raise RouteError('invalid completion choice')
                delta = choice.get('delta', {})
                if finish is not None:
                    raise RouteError('content after completion finish')
                if delta.get('content'):
                    text.append(delta['content'])
                if choice.get('finish_reason') is not None:
                    if finish is not None:
                        raise RouteError('multiple completion finishes')
                    finish = choice['finish_reason']
    if not done:
        raise RouteError('missing stream terminal completion')
    if finish != 'stop':
        raise RouteError('unsuccessful finish: ' + ('length' if finish == 'length' else 'missing or unsupported'))
    if model != body['model']:
        raise RouteError('completion model mismatch')
    if any(type(usage.get(key)) is not int or usage[key] < 1 for key in ('prompt_tokens', 'completion_tokens')):
        raise RouteError('missing or invalid token usage')
    content = ''.join(text)
    # Some MLX templates place reasoning inside the content channel. Remove only
    # a complete leading thinking block, never search prose for a verdict.
    content = re.sub(r'^\s*<think>.*?</think>\s*', '', content, count=1, flags=re.DOTALL)
    return {'review': parse_review(content), 'finish_reason': finish, 'model': model,
            'prompt_tokens': usage.get('prompt_tokens'), 'completion_tokens': usage.get('completion_tokens')}


def _worker(request):
    provider = request['provider']
    local = provider == 'qwen'
    base = request['local_url'] if local else KIMI_URL
    key = None
    if not local:
        from minimax_common import load_kimi_api_key
        key = load_kimi_api_key()
        if not key:
            raise RouteError('Kimi credential unavailable')
    model = QWEN_MODEL if local else KIMI_MODEL
    models = _json_request(base + '/models', key=key).get('data', [])
    eligible = [item for item in models if item.get('id') == model]
    if not eligible or (not local and eligible[0].get('display_name') != 'K2.8 Preview'):
        raise RouteError('expected model not available')
    messages = [{'role': 'user', 'content': request['prompt']}]
    body = {'model': model, 'messages': messages, 'reasoning_effort': 'low'}
    count = None
    if local:
        count = _json_request(base + '/messages/count_tokens', body).get('input_tokens')
        if type(count) is not int or count < 1:
            raise RouteError('invalid token count')
        if count > TOKEN_LIMIT:
            raise RouteError(f'over-limit: {count} input tokens exceeds {TOKEN_LIMIT}')
    body.update(temperature=1, max_tokens=OUTPUT_TOKENS, stream=True, stream_options={'include_usage': True})
    if local:
        body['enable_thinking'] = True
    result = _completion(base, body, key)
    if local and (type(result['prompt_tokens']) is not int or result['prompt_tokens'] != count):
        raise RouteError('token usage mismatch')
    result['counted_tokens'] = count
    return result


def _bounded_attempt(request, timeout):
    process = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), '--worker'],
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                               text=True)
    try:
        stdout, _ = process.communicate(json.dumps(request), timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        process.communicate()
        raise RouteError('wall deadline exceeded')
    if process.returncode or len(stdout) > MAX_WIRE_BYTES:
        raise RouteError('worker failed')
    result = strict_json(stdout)
    if 'error' in result:
        raise RouteError(result['error'])
    return result


def validate_evidence(evidence, digest, model):
    if not isinstance(evidence, dict) or evidence.get('policy') != POLICY or evidence.get('input_sha256') != digest:
        raise ValueError('invalid routing binding')
    selected = evidence.get('selected')
    if selected not in {'qwen', 'kimi'} or model != (QWEN_MODEL if selected == 'qwen' else KIMI_MODEL):
        raise ValueError('invalid routed model')
    attempts = evidence.get('attempts')
    if not isinstance(attempts, list) or not attempts or len(attempts) > 2:
        raise ValueError('invalid routing attempts')
    final = attempts[-1]
    if len(attempts) == 2 and (attempts[0].get('provider') != 'qwen'
                              or attempts[0].get('status') != 'failed' or selected != 'kimi'):
        raise ValueError('invalid escalation sequence')
    if final.get('provider') != selected or final.get('status') != 'valid':
        raise ValueError('invalid selected attempt')
    elapsed = final.get('elapsed_seconds')
    if type(elapsed) not in {int, float} or not math.isfinite(elapsed) or elapsed < 0:
        raise ValueError('invalid routing duration')
    if any(type(final.get(key)) is not int or final[key] < 1 for key in ('prompt_tokens', 'completion_tokens')):
        raise ValueError('invalid routing token usage')
    if selected == 'qwen':
        count = final.get('counted_tokens')
        if (type(count) is not int or not 1 <= count <= TOKEN_LIMIT
                or type(final.get('prompt_tokens')) is not int or final['prompt_tokens'] != count
                or elapsed > LOCAL_SECONDS or len(attempts) != 1):
            raise ValueError('local review outside tested bounds')


def route_review(prompt, mode='auto', builder_family=None, local_url=None, budget_end=None):
    if mode not in {'auto', 'kimi'} or builder_family == 'kimi':
        raise ValueError('reviewer independence violation')
    local_url = validate_local_url(local_url) if local_url else local_endpoint(os.environ.get('K2B_REVIEW_ROUTING_CONFIG'))
    # The runner supplies a monotonic deadline. Clamp even a mistaken epoch
    # value so standalone/internal callers cannot create unbounded cloud work.
    if budget_end is None:
        budget_end = time.monotonic() + MAX_CLOUD_SECONDS
    if type(budget_end) not in {int, float} or not math.isfinite(budget_end):
        raise ValueError('invalid monotonic review deadline')
    input_digest = hashlib.sha256(prompt.encode()).hexdigest()
    evidence = {'policy': POLICY, 'input_sha256': input_digest, 'attempts': []}
    providers = ['qwen', 'kimi'] if mode == 'auto' and builder_family != 'qwen' else ['kimi']
    for provider in providers:
        start = time.monotonic()
        timeout = min(LOCAL_SECONDS if provider == 'qwen' else MAX_CLOUD_SECONDS, budget_end - start)
        if timeout <= 0:
            evidence['attempts'].append({'provider':provider,'status':'not-attempted',
                                        'reason':'review budget exhausted','elapsed_seconds':0})
            break
        try:
            result = _bounded_attempt({'provider': provider, 'prompt': prompt, 'local_url': local_url}, timeout)
            if time.monotonic() > start + timeout:
                raise RouteError('wall deadline exceeded before result acceptance')
            attempt = {'provider': provider, 'status': 'valid', 'elapsed_seconds': time.monotonic() - start,
                       'counted_tokens': result.get('counted_tokens'), 'prompt_tokens': result.get('prompt_tokens'),
                       'completion_tokens': result.get('completion_tokens')}
            evidence['attempts'].append(attempt)
            evidence['selected'] = provider
            validate_evidence(evidence, input_digest, result['model'])
            result['routing'] = evidence
            return result
        except (ValueError, OSError) as exc:
            evidence['attempts'].append({'provider': provider, 'status': 'failed', 'reason': str(exc)[:160],
                                        'elapsed_seconds': time.monotonic() - start})
            print(f'[review-route] {provider} failed: {str(exc)[:160]}', file=sys.stderr, flush=True)
    raise RouteError('no valid review: ' + json.dumps(evidence, separators=(',', ':')))


if __name__ == '__main__':
    try:
        output = _worker(strict_json(sys.stdin.read()))
    except Exception as exc:
        # Never serialize raw HTTP errors, response bodies, headers or keys.
        output = {'error': str(exc) if isinstance(exc, RouteError) else 'provider transport or validation failure'}
    print(json.dumps(output, allow_nan=False))
