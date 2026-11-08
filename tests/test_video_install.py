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


def test_candidate_install_publishes_before_freezing_root(tmp_path,monkeypatch,capsys):
    import json,stat
    from types import SimpleNamespace
    m = installer()
    monkeypatch.setattr(m.pwd,'getpwuid',lambda uid:SimpleNamespace(pw_name='keithmbpm2',pw_dir=str(tmp_path)))
    monkeypatch.setattr(m.sys,'argv',['install-video-extract.py','home'])
    original = Path.rename
    published = []
    def macos_rename(source,destination):
        assert source.stat().st_mode & stat.S_IWUSR, 'macOS requires a writable staging root for rename'
        published.append(destination)
        return original(source,destination)
    monkeypatch.setattr(Path,'rename',macos_rename)
    try:
        m.main()
        report = json.loads(capsys.readouterr().out)
        release = Path(report['path'])
        assert report['status'] == 'candidate_installed' and published == [release]
        assert release.stat().st_mode & 0o777 == 0o500
        assert not (tmp_path/'.local/libexec/k2b-video-v2').exists()
        assert all(m.digest(release/name) == m.digest(Path(m.__file__).parent/name) for name in m.FILES)
        m.main()
        assert json.loads(capsys.readouterr().out)['package'] == report['package']
        assert published == [release]
    finally:
        for path in sorted(tmp_path.rglob('*'),key=lambda p:len(p.parts)):
            if path.is_dir():path.chmod(0o700)


@pytest.mark.parametrize('role',['home','mini'])
def test_explicit_sjm_release_deferral_is_separate_from_passed_examples(role):
    m=installer()
    receipt={'package':'a'*64,'role':role,'live_examples':[1,2,3,4,5,7,8],'independent_review':'approve',
        'deferred_examples':[{'example':6,'authority':'keith_2026-11-08','scope':'sjm_processing_and_activation'}]}
    m.validate_acceptance(receipt,'a'*64,role)


@pytest.mark.parametrize('change',['sjm_role','missing_authority','missing_youtube','missing_recording','extra_waiver','missing_review'])
def test_sjm_deferral_never_waives_other_hosts_or_checks(change):
    m=installer();role='home'
    receipt={'package':'a'*64,'role':role,'live_examples':[1,2,3,4,5,7,8],'independent_review':'approve',
        'deferred_examples':[{'example':6,'authority':'keith_2026-11-08','scope':'sjm_processing_and_activation'}]}
    if change=='sjm_role':role='sjm';receipt['role']=role
    elif change=='missing_authority':receipt['deferred_examples'][0].pop('authority')
    elif change=='missing_youtube':receipt['live_examples'].remove(1)
    elif change=='missing_recording':receipt['live_examples'].remove(5)
    elif change=='extra_waiver':receipt['deferred_examples'].append({'example':8})
    else:receipt['independent_review']='needs_attention'
    with pytest.raises(ValueError):m.validate_acceptance(receipt,'a'*64,role)


@pytest.mark.parametrize('role',['home','mini','sjm'])
def test_complete_real_acceptance_contract_remains_supported(role):
    m=installer()
    m.validate_acceptance({'package':'a'*64,'role':role,'live_examples':list(range(1,9)),'independent_review':'approve'},'a'*64,role)
