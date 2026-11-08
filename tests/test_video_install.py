import importlib.util
from pathlib import Path
import pytest


def installer():
    path = Path(__file__).parents[1]/'scripts/install-video-extract.py'
    assert path.is_file(), 'role-specific installer missing'
    spec = importlib.util.spec_from_file_location('video_install',path)
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m


def test_install_role_is_fixed_to_owner():
    m = installer()
    with pytest.raises(ValueError): m.validate_owner('sjm','keithmbpm2')
    assert m.validate_owner('sjm','keithcheung') == 'sjm'


def test_package_digest_binds_all_source_bytes():
    m = installer(); first = m.package_digest({'a':'0'*64,'b':'1'*64})
    assert first != m.package_digest({'a':'0'*64,'b':'2'*64})
    assert first == m.package_digest({'b':'1'*64,'a':'0'*64})


def test_activation_requires_real_acceptance():
    m = installer()
    with pytest.raises(ValueError): m.validate_acceptance({'package':'a'*64,'live_examples':[]},'a'*64,'home')
    with pytest.raises(ValueError): m.validate_acceptance({'package':'b'*64,'live_examples':list(range(1,9))},'a'*64,'home')


def test_host_pin_change_creates_a_new_retained_release():
    m = installer(); files = {'worker':'a'*64}
    first = m.release_identity(files,'mini',{'deno':'b'*64,'profile':'Profile 9'})
    assert first != m.release_identity(files,'mini',{'deno':'c'*64,'profile':'Profile 9'})
    assert first != m.release_identity(files,'mini',{'deno':'b'*64,'profile':'Profile 8'})


def test_runtime_configuration_identity_binds_host_settings():
    from test_video_extract import load
    v = load(); m = installer(); files = {'worker':'a'*64}; pins = {'browser_profile':'Profile 9'}
    identity = m.release_identity(files,'mini',pins)
    config = {'package':identity,'files':files,'role':'mini','host_pins':pins,'browser_profile':'Profile 9'}
    assert v.validate_config_identity(config,identity)
    with pytest.raises(v.Blocked): v.validate_config_identity(dict(config,browser_profile='Default'),identity)
