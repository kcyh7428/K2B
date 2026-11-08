import hashlib
import json
import sys
import threading
import time
import subprocess
import os
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts/lib'))
import review_routing as routing

GOOD = {'verdict': 'approve', 'summary': 'Checked.', 'findings': [], 'next_steps': []}


@pytest.mark.parametrize('text', [json.dumps(GOOD), '```json\n' + json.dumps(GOOD) + '\n```'])
def test_whole_object_or_fence(text):
    assert routing.parse_review(text) == GOOD


@pytest.mark.parametrize('text', [
    'prefix ' + json.dumps(GOOD), json.dumps(GOOD) + '{}',
    json.dumps(dict(GOOD, **{'$schema': 'echo'})),
    json.dumps(GOOD).replace('"approve"', '"approve", "verdict": "approve"'),
    json.dumps(GOOD).replace('"Checked."', 'NaN'),
    json.dumps(dict(GOOD, verdict='needs-attention')),
])
def test_invalid_output_fails_closed(text):
    with pytest.raises(ValueError):
        routing.parse_review(text)


@pytest.mark.parametrize('count,selected', [(12000, 'qwen'), (12001, 'kimi')])
def test_size_boundary_and_same_packet(monkeypatch, count, selected):
    calls = []
    def attempt(request, timeout):
        calls.append(request)
        if request['provider'] == 'qwen' and count > 12000:
            raise routing.RouteError('over-limit')
        return {'review': GOOD, 'model': routing.QWEN_MODEL if request['provider'] == 'qwen' else routing.KIMI_MODEL,
                'finish_reason': 'stop', 'counted_tokens': count if request['provider'] == 'qwen' else None,
                'prompt_tokens': count, 'completion_tokens':1}
    monkeypatch.setattr(routing, '_bounded_attempt', attempt)
    response = routing.route_review('instructions schema ALL context', local_url='http://127.0.0.1:1/v1')
    assert response['routing']['selected'] == selected
    assert all(c['prompt'] == 'instructions schema ALL context' for c in calls)
    routing.validate_evidence(response['routing'], hashlib.sha256(calls[0]['prompt'].encode()).hexdigest(), response['model'])


def test_qwen_builder_bypasses_local(monkeypatch):
    calls = []
    def attempt(request, timeout):
        calls.append(request['provider'])
        return {'review': GOOD, 'model': routing.KIMI_MODEL, 'finish_reason': 'stop', 'prompt_tokens': 2, 'completion_tokens':1}
    monkeypatch.setattr(routing, '_bounded_attempt', attempt)
    routing.route_review('packet', builder_family='qwen', local_url='http://127.0.0.1:1/v1')
    assert calls == ['kimi']


def test_kimi_builder_rejected():
    with pytest.raises(ValueError):
        routing.route_review('packet', builder_family='kimi')


@pytest.mark.parametrize('verdict', ['approve','needs-attention'])
def test_valid_local_verdict_does_not_escalate(monkeypatch, verdict):
    calls = []
    review = dict(GOOD, verdict=verdict)
    if verdict == 'needs-attention':
        review['findings'] = [{'severity':'high','title':'Defect','body':'Demonstrable defect',
                              'file':'a.py','line_start':1,'line_end':1,'confidence':1,'recommendation':'Fix'}]
    def attempt(request, timeout):
        calls.append(request['provider'])
        return {'review':review,'model':routing.QWEN_MODEL,'finish_reason':'stop',
                'counted_tokens':12000,'prompt_tokens':12000,'completion_tokens':1}
    monkeypatch.setattr(routing, '_bounded_attempt', attempt)
    response = routing.route_review('packet', local_url='http://127.0.0.1:1/v1')
    assert calls == ['qwen']
    assert response['review']['verdict'] == verdict


def test_both_providers_invalid_fail_without_third_provider(monkeypatch):
    calls = []
    def attempt(request, timeout):
        calls.append(request['provider'])
        raise routing.RouteError('invalid review schema')
    monkeypatch.setattr(routing, '_bounded_attempt', attempt)
    with pytest.raises(routing.RouteError, match='no valid review'):
        routing.route_review('packet', local_url='http://127.0.0.1:1/v1')
    assert calls == ['qwen','kimi']


def test_epoch_deadline_cannot_disable_cloud_ceiling(monkeypatch):
    timeouts = []
    def attempt(request, timeout):
        timeouts.append(timeout)
        return {'review':GOOD,'model':routing.KIMI_MODEL,'finish_reason':'stop','prompt_tokens':1,'completion_tokens':1}
    monkeypatch.setattr(routing, '_bounded_attempt', attempt)
    routing.route_review('packet', mode='kimi', budget_end=time.time()+99999, local_url='http://127.0.0.1:1/v1')
    assert timeouts == [350]


def test_routed_cli_refuses_unbound_publication():
    root = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    for key in ['K2B_REVIEW_RESULT_PATH','K2B_REVIEW_ATTEMPT_ID','K2B_REVIEW_SCOPE_SHA256']:
        env.pop(key, None)
    env.update(K2B_LLM_PROVIDER='kimi', K2B_LLM_MODEL='kimi-for-coding')
    result = subprocess.run([sys.executable,str(root/'scripts/lib/minimax_review.py'),
                             '--scope','files','--files','scripts/routed-review.sh','--review-route','auto'],
                            cwd=root, env=env, capture_output=True, text=True, timeout=10)
    assert result.returncode == 3
    assert 'requires central attempt binding' in result.stderr


def test_builder_matrix_and_route_command():
    import review_runner as runner
    assert runner.review_matrix_error('openai','auto',True,None,None) is None
    assert runner.review_matrix_error('deepseek','auto',True,None,None) is None
    assert runner.review_matrix_error('qwen','auto',True,None,None) is None
    assert runner.review_matrix_error('kimi','auto',True,None,None)
    assert runner.review_matrix_error('openai','auto',False,None,None)


def test_parent_requires_bound_routing_evidence(tmp_path, monkeypatch):
    import review_result
    target = tmp_path / 'result.json'
    for key, value in [(review_result.RESULT_PATH_ENV,str(target)),
                       (review_result.ATTEMPT_ID_ENV,'attempt'), (review_result.SCOPE_SHA256_ENV,'scope')]:
        monkeypatch.setenv(key,value)
    digest = review_result.write_review_input('packet')
    review_result.write_attempt_result(GOOD, finish_reason='stop', model=routing.KIMI_MODEL, content_sha256=digest)
    with pytest.raises(ValueError, match='routing'):
        review_result.read_attempt_result(target, attempt_id='attempt', scope_sha256='scope', require_routing=True)


@contextmanager
def server(count=12000, text=None, model=None, finish='stop', done=True, delay=0, usage=None, trickle=False, completion_usage=1):
    calls = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(json.dumps({'data':[{'id': routing.QWEN_MODEL},
                                                {'id':routing.KIMI_MODEL,'display_name':'K2.8 Preview'}]}).encode())
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            calls.append((self.path, body))
            time.sleep(delay)
            self.send_response(200)
            self.end_headers()
            try:
                if self.path.endswith('count_tokens'):
                    self.wfile.write(json.dumps({'input_tokens':count}).encode())
                else:
                    if trickle:
                        for _ in range(50):
                            self.wfile.write(b': keepalive\n\n')
                            self.wfile.flush()
                            time.sleep(.05)
                    chunk = {'model': model or body['model'],
                             'choices':[{'index':0, 'delta':{'content':text if text is not None else json.dumps(GOOD)}, 'finish_reason':finish}],
                             'usage':{'prompt_tokens':count if usage is None else usage, 'completion_tokens':completion_usage}}
                    self.wfile.write(('data: '+json.dumps(chunk)+'\n\n').encode())
                    if done:
                        self.wfile.write(b'data: [DONE]\n\n')
            except (BrokenPipeError, ConnectionResetError):
                pass
    http = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=http.serve_forever, daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{http.server_port}/v1', calls
    finally:
        http.shutdown()
        http.server_close()
        thread.join()


@pytest.mark.parametrize('count', [12001, None, True, 0, -1, '12000', 12000.0])
def test_http_count_guard_prevents_generation(count):
    with server(count=count) as (url, calls):
        with pytest.raises(ValueError):
            routing._worker({'provider':'qwen','prompt':'all instructions and code','local_url':url})
    assert [path for path, _ in calls] == ['/v1/messages/count_tokens']


def test_http_exact_limit_and_identical_message():
    with server() as (url, calls):
        response = routing._bounded_attempt({'provider':'qwen','prompt':'schema instructions code','local_url':url}, 5)
    assert response['review'] == GOOD
    assert response['counted_tokens'] == 12000
    assert calls[0][1]['messages'] == calls[1][1]['messages']
    assert calls[0][1]['reasoning_effort'] == calls[1][1]['reasoning_effort'] == 'low'
    assert calls[1][1]['max_tokens'] == 8192
    assert calls[1][1]['temperature'] == 1


def test_kimi_worker_loads_machine_credential_at_cloud_boundary(monkeypatch):
    import minimax_common
    loaded = []
    monkeypatch.setattr(minimax_common, 'load_kimi_api_key', lambda: loaded.append(True) or 'fixture-only')
    with server() as (url, calls):
        monkeypatch.setattr(routing, 'KIMI_URL', url)
        local = routing._worker({'provider':'qwen','prompt':'packet','local_url':url})
        assert loaded == []
        cloud = routing._worker({'provider':'kimi','prompt':'packet','local_url':url})
    assert loaded == [True]
    assert cloud['model'] == routing.KIMI_MODEL
    assert local['model'] == routing.QWEN_MODEL


@pytest.mark.parametrize('count', [None,True,0,-1,'1'])
def test_missing_or_invalid_cloud_usage_fails_closed(monkeypatch, count):
    import minimax_common
    monkeypatch.setattr(minimax_common,'load_kimi_api_key',lambda:'fixture-only')
    with server(completion_usage=count) as (url, calls):
        monkeypatch.setattr(routing,'KIMI_URL',url)
        with pytest.raises(ValueError, match='token usage'):
            routing._worker({'provider':'kimi','prompt':'packet','local_url':url})


def test_exhausted_budget_retains_bound_attempt_evidence():
    with pytest.raises(routing.RouteError) as caught:
        routing.route_review('packet', budget_end=time.monotonic()-1, local_url='http://127.0.0.1:1/v1')
    assert 'review budget exhausted' in str(caught.value)
    assert hashlib.sha256(b'packet').hexdigest() in str(caught.value)
    assert 'not-attempted' in str(caught.value)


def test_swapped_evidence_digest_rejected():
    with pytest.raises(ValueError, match='binding'):
        routing.validate_evidence({'policy':routing.POLICY,'input_sha256':'a'*64},'b'*64,routing.KIMI_MODEL)


@pytest.mark.parametrize('options', [
    {'text':json.dumps(dict(GOOD, **{'$schema':'bad'}))}, {'model':'wrong'},
    {'finish':None}, {'finish':'length'}, {'done':False}, {'usage':11999},
])
def test_http_invalid_completion_is_not_a_review(options):
    with server(**options) as (url, calls):
        with pytest.raises(ValueError):
            routing._bounded_attempt({'provider':'qwen','prompt':'packet','local_url':url}, 5)


def test_wall_deadline_covers_network_worker():
    with server(delay=1) as (url, calls):
        start = time.monotonic()
        with pytest.raises(routing.RouteError, match='deadline'):
            routing._bounded_attempt({'provider':'qwen','prompt':'packet','local_url':url}, .15)
        assert time.monotonic() - start < .8
        assert not any(path.endswith('completions') for path, _ in calls)


def test_trickling_stream_cannot_extend_wall_deadline():
    with server(trickle=True) as (url, calls):
        start = time.monotonic()
        with pytest.raises(routing.RouteError, match='deadline'):
            routing._bounded_attempt({'provider':'qwen','prompt':'packet','local_url':url}, .3)
        assert time.monotonic() - start < .9
        assert any(path.endswith('completions') for path, _ in calls)


@pytest.mark.parametrize('config', [{'qwen_url':'http://127.0.0.1:8081/v1','limit':99999}, {'qwen_url':'http://remote:8081/v1'}])
def test_invalid_config_does_not_silently_select_default(tmp_path, config):
    path = tmp_path / 'routing.json'
    path.write_text(json.dumps(config))
    path.chmod(0o600)
    with pytest.raises(ValueError):
        routing.local_endpoint(path)


@pytest.mark.parametrize('field,value', [('counted_tokens',12001), ('prompt_tokens',11999), ('elapsed_seconds',120.01)])
def test_parent_evidence_guard(field, value):
    digest = hashlib.sha256(b'packet').hexdigest()
    evidence = {'policy':routing.POLICY,'input_sha256':digest,'selected':'qwen',
                'attempts':[{'provider':'qwen','status':'valid','elapsed_seconds':1,
                             'counted_tokens':12000,'prompt_tokens':12000,'completion_tokens':1}]}
    evidence['attempts'][0][field] = value
    with pytest.raises(ValueError):
        routing.validate_evidence(evidence, digest, routing.QWEN_MODEL)


@pytest.mark.parametrize('url', ['http://192.168.1.2:8081/v1', 'https://127.0.0.1:8081/v1',
                                'http://key@localhost:8081/v1','http://localhost:8081/v1?key=x'])
def test_machine_endpoint_cannot_redirect_data(url):
    with pytest.raises(ValueError):
        routing.validate_local_url(url)
