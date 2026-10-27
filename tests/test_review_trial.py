import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts/lib'))
import review_trial as trial
import pytest


def test_expiry_and_credential_isolation(tmp_path):
    config = tmp_path / 'config.json'
    config.write_text(json.dumps(dict(id='test', model='kimi-for-coding-highspeed',
                                    expires_at='2026-10-30T00:00:00+00:00')))
    config.chmod(0o600)
    key = tmp_path / 'key'
    key.write_text('synthetic-key')
    key.chmod(0o600)
    with patch.object(trial, 'STATE', tmp_path):
        env = {'KIMI_API_KEY': 'original', 'K2B_ENV_FILE': '/foreign/path'}
        trial.configure(env, datetime(2026, 10, 27, tzinfo=timezone.utc))
        assert env['K2B_LLM_MODEL'] == 'kimi-for-coding-highspeed'
        assert env['KIMI_API_KEY'] == 'synthetic-key'
        assert 'K2B_ENV_FILE' not in env
        manual = {'K2B_LLM_MODEL': 'kimi-k2.7-code'}
        assert trial.configure(manual, datetime(2026, 10, 27, tzinfo=timezone.utc)) is None
        assert manual == {'K2B_LLM_MODEL': 'kimi-k2.7-code'}
        retired = {'K2B_LLM_PROVIDER': 'minimax'}
        assert trial.configure(retired, datetime(2026, 10, 27, tzinfo=timezone.utc)) is None
        assert retired == {'K2B_LLM_PROVIDER': 'minimax'}
        budget = {'K2B_LLM_MAX_TOKENS': '4096'}
        trial.configure(budget, datetime(2026, 10, 27, tzinfo=timezone.utc))
        assert budget['K2B_LLM_MAX_TOKENS'] == '4096'
        env = {'KIMI_API_KEY': 'original'}
        trial.configure(env, datetime(2026, 10, 30, tzinfo=timezone.utc))
        assert env == {'KIMI_API_KEY': 'original'}


def test_unknown_usage_is_not_zero_cost():
    assert trial.estimate('k3.1-pro-highspeed', {}) is None
    assert trial.estimate('unknown', dict(prompt_tokens=50, completion_tokens=20)) is None
    assert trial.estimate('kimi-k2.7-code', dict(prompt_tokens=50000, completion_tokens=10000)) == 0.0875
    assert trial.estimate('KIMI-K2.7-CODE-HIGHSPEED', dict(prompt_tokens=50000, completion_tokens=10000)) == 0.175
    assert trial.estimate('k3.1-pro-highspeed', dict(prompt_tokens=50000, completion_tokens=10000)) == 0.4


def test_aliases_are_symmetric_but_missing_model_is_rejected():
    assert trial.model_matches('kimi-k3.1-pro-highspeed', 'k3.1-pro-highspeed')
    assert trial.model_matches('k3.1-pro-highspeed', 'kimi-k3.1-pro-highspeed')
    assert trial.model_matches('kimi-for-coding-highspeed', 'KIMI-K2.7-CODE-HIGHSPEED')
    assert not trial.model_matches('kimi-for-coding-highspeed', 'kimi-k2.7-code')
    assert not trial.model_matches('kimi-for-coding-highspeed', None)


def test_client_preserves_highspeed_model_id():
    import minimax_common as client
    with patch.object(client, '_kimi_chat_completion', return_value={}) as request:
        client.chat_completion(model='kimi-for-coding-highspeed', messages=[], max_tokens=20, temperature=0.2)
        assert request.call_args.kwargs['model'] == 'kimi-for-coding-highspeed'


def test_private_log_and_summary(tmp_path):
    with patch.object(trial, 'STATE', tmp_path):
        trial.record('k3.1-pro-highspeed', None, {}, 2, 'transport-error', 'diff')
        result = trial.report()['k3.1-pro-highspeed']
        assert result['unknown_cost'] == 1
        assert result['outcomes'] == {'transport-error': 1}
        assert (tmp_path / 'usage.jsonl').stat().st_mode & 0o777 == 0o600


def test_corrupt_config_stops_without_mutating_environment(tmp_path):
    config = tmp_path / 'config.json'
    config.write_text('{broken')
    config.chmod(0o600)
    env = {'KIMI_API_KEY': 'original'}
    with patch.object(trial, 'STATE', tmp_path), pytest.raises(ValueError):
        trial.configure(env)
    assert env == {'KIMI_API_KEY': 'original'}


def test_sjm_uses_own_credential_without_dedicated_key(tmp_path):
    config = tmp_path / 'config.json'
    config.write_text(json.dumps(dict(id='sjm-test', model='kimi-for-coding-highspeed',
                                    credential_source='local-default',
                                    expires_at='2026-10-30T00:00:00+00:00')))
    config.chmod(0o600)
    credential = tmp_path / '.k2b-env'
    credential.write_text('export KIMI_API_KEY="sjm-own-key"\n')
    credential.chmod(0o600)
    with patch.object(trial, 'STATE', tmp_path), patch.object(trial.Path, 'home', return_value=tmp_path):
        env = {'KIMI_API_KEY': 'foreign-inherited-key', 'K2B_ENV_FILE': '/foreign/path'}
        trial.configure(env, datetime(2026, 10, 27, tzinfo=timezone.utc))
        assert env['KIMI_API_KEY'] == 'sjm-own-key'
        assert env['K2B_LLM_MODEL'] == 'kimi-for-coding-highspeed'
        assert env['K2B_LLM_MAX_TOKENS'] == '32768'
        env = {}
        trial.configure(env, datetime(2026, 10, 27, tzinfo=timezone.utc))
        assert env['KIMI_API_KEY'] == 'sjm-own-key'
        assert env['K2B_ENV_FILE'] == str(credential)


def test_telemetry_failure_does_not_discard_review(capsys):
    with patch.object(trial, 'record', side_effect=OSError('synthetic')):
        trial.try_record('model', None, {}, 0, 'error', 'diff')
    assert 'cost coverage is incomplete' in capsys.readouterr().err


def test_existing_insecure_log_is_not_appended(tmp_path):
    log = tmp_path / 'usage.jsonl'
    log.write_text('')
    log.chmod(0o644)
    with patch.object(trial, 'STATE', tmp_path), pytest.raises(ValueError):
        trial.record('model', None, {}, 0, 'error', 'diff')
    assert log.read_text() == ''


def test_private_read_rejects_symlink(tmp_path):
    source = tmp_path / 'source'
    source.write_text('synthetic')
    source.chmod(0o600)
    link = tmp_path / 'link'
    link.symlink_to(source)
    with pytest.raises(OSError):
        trial.private_read(link)


def test_standard_umask_preserves_private_creation_modes(tmp_path):
    state = tmp_path / 'new-state'
    previous = os.umask(0o022)
    try:
        with patch.object(trial, 'STATE', state):
            trial.record('model', None, None, 0, 'error', 'diff')
    finally:
        os.umask(previous)
    assert state.stat().st_mode & 0o777 == 0o700
    assert (state / 'usage.jsonl').stat().st_mode & 0o777 == 0o600


def test_supported_runtime_accepts_z_expiry(tmp_path):
    config = tmp_path / 'config.json'
    config.write_text(json.dumps(dict(id='expired-test', model='kimi-for-coding-highspeed',
                                    expires_at='2026-10-30T00:00:00Z')))
    config.chmod(0o600)
    with patch.object(trial, 'STATE', tmp_path):
        env = {}
        assert trial.configure(env, datetime(2026, 10, 30, tzinfo=timezone.utc)) is None
        assert env == {}


def test_corrupt_log_stops_report_instead_of_hiding_records(tmp_path):
    log = tmp_path / 'usage.jsonl'
    log.write_text('{"incomplete":')
    log.chmod(0o600)
    with patch.object(trial, 'STATE', tmp_path), pytest.raises(json.JSONDecodeError):
        trial.report()
