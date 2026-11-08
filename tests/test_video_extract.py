"""Boundary tests for the common media contract, independent of live accounts."""
import hashlib
import importlib.util
import json
from pathlib import Path
import pytest

PATH = Path(__file__).parents[1] / 'scripts/lib/video_extract.py'


def load():
    spec = importlib.util.spec_from_file_location('video_extract', PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_unified_contract_exists():
    assert PATH.is_file(), 'unified extraction contract is absent'


@pytest.mark.parametrize('url,platform,identity', [
    ('https://youtu.be/bc7QaDmLmoA?t=20', 'youtube', 'bc7QaDmLmoA'),
    ('https://www.youtube.com/watch?v=bc7QaDmLmoA&list=unsafe', 'youtube', 'bc7QaDmLmoA'),
    ('https://www.youtube.com/shorts/bc7QaDmLmoA', 'youtube', 'bc7QaDmLmoA'),
    ('https://www.facebook.com/reel/2372450313602638/?x=1', 'facebook', '2372450313602638'),
    ('https://www.facebook.com/share/r/1MFtSe4XWL/', 'facebook', None),
])
def test_canonical_source(url, platform, identity):
    v = load(); result = v.parse_source(url)
    assert result['platform'] == platform
    assert result['video_id'] == identity
    assert 'list=' not in result['url'] and '?x=' not in result['url']


@pytest.mark.parametrize('url', [
    'https://user:pass@www.youtube.com/watch?v=bc7QaDmLmoA',
    'https://www.youtube.com.evil.test/watch?v=bc7QaDmLmoA',
    'https://www.youtube.com:443/watch?v=bc7QaDmLmoA',
    'http://www.youtube.com/watch?v=bc7QaDmLmoA',
    'https://www.youtube.com/watch?v=bc7QaDmLmoA&v=AAAAAAAAAAA',
    'https://www.youtube.com/redirect?q=https://127.0.0.1',
    'https://www.facebook.com/reel/../private/',
])
def test_reject_ambiguous_sources(url):
    v = load()
    with pytest.raises(v.Blocked): v.parse_source(url)


def test_platform_limits_are_distinct():
    v = load()
    assert v.LIMITS['facebook'] == (1200, 256 * 1024**2, 2700)
    assert v.LIMITS['youtube'] == (3600, 2 * 1024**3, 5400)
    with pytest.raises(v.Blocked): v.validate_duration('facebook', 1201)
    with pytest.raises(v.Blocked): v.validate_duration('youtube', float('nan'))
    assert v.validate_duration('facebook', 1199) == 1199


def test_manifest_checks_independent_digest_and_required_payloads(tmp_path):
    v = load()
    (tmp_path/'source.mp4').write_bytes(b'video')
    (tmp_path/'run-marker.txt').write_bytes(b'marker')
    digest = v.payload_manifest(tmp_path, {'run_id':'youtube-bc7QaDmLmoA-20261108T000000Z-abcdef01','platform':'youtube'})
    assert v.verify(tmp_path, digest)['platform'] == 'youtube'
    with pytest.raises(v.Blocked): v.verify(tmp_path, '0'*64)
    (tmp_path/'source.mp4').write_bytes(b'other')
    with pytest.raises(v.Blocked): v.verify(tmp_path, digest)


def test_symlink_and_traversal_rejected(tmp_path):
    v = load()
    with pytest.raises(v.Blocked): v.child(tmp_path, '../outside')
    (tmp_path/'link').symlink_to(tmp_path.parent)
    with pytest.raises(v.Blocked): v.child(tmp_path, 'link/outside')


def test_media_dns_and_redirect_allowlists(monkeypatch):
    v = load()
    monkeypatch.setattr(v.socket, 'getaddrinfo', lambda *a, **k: [(2,1,6,'',('127.0.0.1',443))])
    with pytest.raises(v.Blocked): v.check_remote('https://rr1.googlevideo.com/videoplayback', media=True)
    with pytest.raises(v.Blocked): v.check_remote('https://evil.test/a', media=True)
    v.MINI_FAKE_IP_ROUTE = True
    assert v.media_address_allowed('198.18.0.2')
    assert not v.media_address_allowed('192.168.50.67')
    monkeypatch.setattr(v.socket, 'getaddrinfo', lambda *a, **k: [(2,1,6,'',('198.18.0.2',443))])
    v.check_remote('https://video.xx.fbcdn.net/a', media=True)
    with pytest.raises(v.Blocked): v.check_remote('https://evil.test/a', media=True)


def test_deadline_rejects_expired_and_insufficient_recording():
    v = load()
    with pytest.raises(v.Blocked): v.remaining_deadline(100, 100, 1)
    with pytest.raises(v.Blocked): v.remaining_deadline(200, 100, 120)
    assert v.remaining_deadline(250, 100, 120) == 150


def test_budget_includes_partials(tmp_path):
    v = load(); (tmp_path/'partials').mkdir()
    (tmp_path/'partials/failure').write_bytes(b'x'*20)
    with pytest.raises(v.Blocked): v.check_budget(tmp_path, maximum=19, reserve=0)


def test_invalid_caption_is_auxiliary_not_capture_failure(tmp_path):
    v = load()
    result = v.caption_evidence({'subtitles':{'en':[{'ext':'vtt','url':'https://evil.test/captions'}]}},tmp_path,999999999999)
    assert result[0]['status'].startswith('unavailable_')
    assert list(tmp_path.iterdir()) == []


def test_approved_mini_youtube_fake_ip_route_is_narrow_and_role_bound(monkeypatch):
    v = load()
    monkeypatch.setattr(v.socket,'getaddrinfo',lambda *a,**k:[(2,1,6,'',('198.18.0.21',443))])
    v.MINI_FAKE_IP_ROUTE = True
    v.check_remote('https://www.youtube.com/watch?v=bc7QaDmLmoA')
    v.check_remote('https://rr1---sn-example.googlevideo.com/videoplayback',media=True)
    for url,media in [('https://www.youtube.com.evil.test/watch',False),('https://evil.youtube.com/watch',False),
                      ('https://googlevideo.com.evil.test/a',True),('https://evilgooglevideo.com/a',True)]:
        with pytest.raises(v.Blocked):v.check_remote(url,media)
    v.MINI_FAKE_IP_ROUTE = False
    with pytest.raises(v.Blocked):v.check_remote('https://www.youtube.com/watch?v=bc7QaDmLmoA')
    with pytest.raises(v.Blocked):v.check_remote('https://rr1---sn-example.googlevideo.com/a',media=True)
    v.MINI_FAKE_IP_ROUTE = True
    monkeypatch.setattr(v.socket,'getaddrinfo',lambda *a,**k:[(2,1,6,'',('192.168.50.67',443))])
    with pytest.raises(v.Blocked):v.check_remote('https://www.youtube.com/watch?v=bc7QaDmLmoA')


def test_caption_coverage_records_other_languages_and_formats(tmp_path):
    v = load()
    result = v.caption_evidence({'subtitles':{'fr':[{'ext':'unsupported-format','url':'https://www.youtube.com/api/timedtext'}]}},tmp_path,999999999999)
    assert result[0]['language'] == 'fr' and result[0]['status'] == 'unsupported_timed_caption_format'


def test_native_json_and_xml_caption_formats_are_retained_as_timed_data():
    v = load()
    assert v.caption_format_valid('json3',b'{"events":[{"tStartMs":0,"dDurationMs":1000,"segs":[{"utf8":"words"}]}]}')
    assert v.caption_format_valid('ttml',b'<tt xmlns="http://www.w3.org/ns/ttml"><body><p begin="0s" end="1s">words</p></body></tt>')
    assert not v.caption_format_valid('json3',b'<html>login</html>')
    assert not v.caption_format_valid('ttml',b'<!DOCTYPE tt><tt/>')
