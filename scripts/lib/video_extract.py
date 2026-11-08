"""Unified bounded media contract. Legacy algorithms are retained byte-for-byte.

No caller-selected paths, executable names, providers or code. Installation pins
every package file. The native Mini task supplies source evidence, never cookies.
"""
import argparse
import contextlib
import datetime as dt
import hashlib
import importlib.util
import ipaddress
import json
import math
import os
from pathlib import Path
import pwd
import re
import secrets
import signal
import socket
import stat
import subprocess
import time
import urllib.parse
import urllib.request

_spec = importlib.util.spec_from_file_location('video_legacy', Path(__file__).with_name('video_legacy.py'))
legacy = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(legacy)
_payload_manifest = legacy.payload_manifest
_command = legacy.command
Blocked = legacy.Blocked
need = legacy.need
sha = legacy.sha
child = legacy.child
safe_dir = legacy.safe_dir
write_json = legacy.write_json
read_json = legacy.read_json
size = legacy.size
check_budget = legacy.check_budget
media_address_allowed = legacy.media_address_allowed
LIMITS = {'facebook': (1200, 256 * 1024**2, 2700), 'youtube': (3600, 2 * 1024**3, 5400)}
YOUTUBE_SOURCE_HOSTS = frozenset(('www.youtube.com','youtube.com','m.youtube.com','youtu.be'))
RUN_RE = r'(facebook-([0-9]{1,24})|youtube-([A-Za-z0-9_-]{11}))-[0-9]{8}T[0-9]{6}Z-[a-f0-9]{8}'
MINI_FAKE_IP_ROUTE = False
NAS = Path('/Volumes/k2b-captures')
VERSION = '2'
BYTE_BUDGET_SCOPE = 'active_operation_local_plus_nas'
PACKAGE = Path(__file__).resolve().parent.parent
CURRENT = {}
OPERATION_ROOTS = []
CONTENT_CONTRACT = 'content_first_2026-11-08'
CONTENT_FILES = ('caption-metadata.json','analysis.md','claims.json','prior-visual-notes.json','visual-observations.json')
# Keith's explicit recovery authority covers this retained attempt only.
LATE_RECOVERY_RUN = 'youtube-bc7QaDmLmoA-20261108T090447Z-d57543ed'
LATE_RECOVERY_REQUEST = 'e7a2c9fd-666f-4a81-92fc-0ffd972a09c9'
LATE_RECOVERY_PACKAGE = 'a4027401f8b37a511c0f07f55fb29fcbcb3b3ff4c3691be61c9c2a6672155e5e'
LATE_RECOVERY_CODE = '8bf7971b71b34b594c59c18a903b639e944a273756450ae969560f02b747ce3c'


def parse_source(value):
    need(isinstance(value, str) and len(value) <= 2048 and not re.search(r'[\s\x00-\x1f]', value), 'invalid_source_url')
    try: p = urllib.parse.urlsplit(value)
    except ValueError: raise Blocked('invalid_source_url') from None
    need(p.scheme == 'https' and p.netloc == p.hostname, 'unsupported_source_host')
    host = p.hostname
    if host in ('www.facebook.com', 'facebook.com', 'm.facebook.com'):
        match = re.fullmatch(r'/(?:reel/([0-9]{1,24})|share/[rv]/[A-Za-z0-9]{1,64})/?', p.path)
        need(match is not None, 'unsupported_source_path')
        identity = match[1]
        path = '/reel/'+identity+'/' if identity else p.path
        return {'platform':'facebook', 'video_id':identity, 'url':'https://www.facebook.com'+path}
    need(host in YOUTUBE_SOURCE_HOSTS, 'unsupported_source_host')
    if host == 'youtu.be': identity = p.path.lstrip('/')
    elif p.path == '/watch':
        query = urllib.parse.parse_qs(p.query)
        need(len(query.get('v', [])) == 1, 'ambiguous_video_identity')
        identity = query['v'][0]
    else:
        match = re.fullmatch(r'/(?:shorts|embed)/([A-Za-z0-9_-]{11})/?', p.path)
        need(match is not None, 'unsupported_source_path'); identity = match[1]
    need(bool(re.fullmatch(r'[A-Za-z0-9_-]{11}', identity)), 'invalid_video_identity')
    return {'platform':'youtube', 'video_id':identity, 'url':'https://www.youtube.com/watch?v='+identity}


def validate_duration(platform, duration):
    need(type(duration) in (int, float) and math.isfinite(duration) and 0 < duration <= LIMITS[platform][0], 'source_duration_limit')
    return duration


def remaining_deadline(deadline, now, minimum):
    need(type(deadline) in (int, float) and math.isfinite(deadline) and deadline-now >= minimum, 'expired_or_insufficient_deadline')
    return deadline-now


def run_identity(run):
    match = re.fullmatch(RUN_RE, run)
    need(match is not None, 'invalid_run_id')
    return ('facebook', match[2]) if match[2] else ('youtube', match[3])


def check_remote(value, media=False):
    p = urllib.parse.urlsplit(value); host = p.hostname or ''
    need(p.scheme == 'https' and p.netloc == host, 'invalid_remote_url')
    domains = ('fbcdn.net', 'fbsbx.com', 'googlevideo.com') if media else ('facebook.com', 'youtube.com', 'youtu.be')
    need(any(host == d or host.endswith('.'+d) for d in domains), 'unexpected_remote_host')
    addresses = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    # Keith approved this named Mini's existing fake-IP route on 8 November.
    # The actual source and selected CDN hosts passed normal TLS verification.
    # No proxy/DNS changes, other private ranges or arbitrary YouTube subdomains.
    meta = any(host == d or host.endswith('.'+d) for d in ('facebook.com','fbcdn.net','fbsbx.com'))
    youtube_route = host in YOUTUBE_SOURCE_HOSTS or (media and (host == 'googlevideo.com' or host.endswith('.googlevideo.com')))
    need(bool(addresses) and all(ipaddress.ip_address(a[4][0]).is_global or
         (MINI_FAKE_IP_ROUTE and (meta or youtube_route) and ipaddress.ip_address(a[4][0]) in legacy.MINI_FAKE_IP_RANGE) for a in addresses), 'unsupported_media_address')


def media_address_allowed(value):
    address = ipaddress.ip_address(value)
    return address.is_global or (MINI_FAKE_IP_ROUTE and address in legacy.MINI_FAKE_IP_RANGE)


def verify(root, digest):
    need(not root.is_symlink(), 'symlink_run')
    need(isinstance(digest, str) and re.fullmatch('[a-f0-9]{64}', digest), 'invalid_manifest_digest')
    manifest = child(root, 'manifest.json')
    need(manifest.is_file() and sha(manifest) == digest, 'manifest_digest_mismatch')
    m = read_json(manifest)
    need(isinstance(m, dict) and isinstance(m.get('files'), list) and 0 < len(m['files']) <= 2000, 'invalid_manifest')
    platform = m.get('platform')
    if platform is None and m.get('run_id'): platform = run_identity(m['run_id'])[0]
    need(platform in LIMITS, 'invalid_manifest_platform')
    seen = set(); total = 0
    for item in m['files']:
        need(isinstance(item, dict) and set(item) == {'name','bytes','sha256'}, 'invalid_manifest_item')
        name = item['name']
        need(isinstance(name, str) and name not in seen and name not in ('manifest.json','completion.json'), 'duplicate_or_reserved_manifest_path')
        seen.add(name); p = child(root, name)
        need(p.is_file() and stat.S_ISREG(p.stat().st_mode), 'missing_payload')
        need(type(item['bytes']) is int and item['bytes'] >= 0 and isinstance(item['sha256'], str) and re.fullmatch('[a-f0-9]{64}', item['sha256']), 'invalid_manifest_item')
        total += p.stat().st_size
        need(total <= LIMITS[platform][1], 'payload_byte_budget')
        need(p.stat().st_size == item['bytes'] and sha(p) == item['sha256'], 'payload_hash_mismatch')
    return m


def payload_manifest(root, extra):
    if 'source_manifest_sha256' in extra:
        extra = dict(extra,processor_id=processor_id(),platform=run_identity(extra['run_id'])[0])
    return _payload_manifest(root, extra)


def mount_check(path, role):
    lines = subprocess.check_output(['/sbin/mount','-t','smbfs'], text=True)
    server = '100.75.5.21' if role == 'sjm' else '192.168.50.67'
    account = 'k2b-capture-mini' if role == 'mini' else 'cheung'
    expression = re.escape('//'+account+'@'+server+'/k2b-captures on '+str(path))+r' \(smbfs,[^\n]*\)'
    need(any(re.fullmatch(expression,line) for line in lines.splitlines()), 'nas_not_mounted_or_wrong_binding')
    need(path.is_dir() and not path.is_symlink(), 'invalid_nas_root')


def nas_run(run):
    platform, identity = run_identity(run)
    return child(NAS, platform+'/'+identity+'/'+run)


def install_config(package=None):
    package = PACKAGE if package is None else package
    c = read_json(child(package,'config.json'))
    need(c.get('role') in ('home','sjm','mini'), 'invalid_install_role')
    validate_config_identity(c,package.name)
    role = c['role']; expected = {'home':'keithmbpm2','sjm':'keithcheung','mini':'fastshower'}[role]
    need(pwd.getpwuid(os.getuid()).pw_name == expected, 'wrong_host_user')
    need(set(c.get('files', {})) == {'video-extract.py','lib/video_extract.py','lib/video_legacy.py','media/record.swift'}, 'invalid_package_file_set')
    for name, digest in c['files'].items():
        p = child(package, name); s = p.stat()
        need(s.st_uid == os.getuid() and not s.st_mode & 0o222 and sha(p) == digest, 'installed_code_changed')
    if role == 'mini':
        artifacts = [('yt-dlp','downloader_sha256')]
        if c.get('workflow_contract') != CONTENT_CONTRACT:artifacts.append(('record','recorder_sha256'))
        for name, field in artifacts:
            artifact = child(package,'tools/'+name)
            need(artifact.is_file() and not artifact.stat().st_mode & 0o222 and sha(artifact) == c.get(field), 'installed_tool_changed')
        need(sha(Path('/opt/homebrew/bin/deno').resolve()) == c.get('deno_sha256'), 'pinned_deno_changed')
    return c


def validate_config_identity(config,release_name):
    pins = config.get('host_pins')
    need(isinstance(pins,dict),'invalid_host_pins')
    identity = hashlib.sha256(json.dumps({'files':config.get('files'),'role':config.get('role'),'host_pins':pins},sort_keys=True,separators=(',',':')).encode()).hexdigest()
    need(config.get('package') == identity == release_name and all(config.get(k) == v for k,v in pins.items()),'installed_configuration_changed')
    return True


def configure(c, platform, state):
    global CURRENT, MINI_FAKE_IP_ROUTE, OPERATION_ROOTS
    CURRENT = c; MINI_FAKE_IP_ROUTE = c['role'] == 'mini'; OPERATION_ROOTS = []
    legacy.MAX_SECONDS, legacy.MAX_BYTES, _ = LIMITS[platform]
    # Source admission uses validate_duration. Audio processing may include the
    # explicitly declared recording padding, up to 120 seconds.
    legacy.MAX_SECONDS += 120
    legacy.NAS = NAS; legacy.VERSION = VERSION; legacy.RUN_RE = RUN_RE
    legacy.MINI_FAKE_IP_ROUTE = MINI_FAKE_IP_ROUTE
    legacy.check_remote = check_remote; legacy.verify = verify
    legacy.run_video_id = lambda run: run_identity(run)[1]
    legacy.nas_run = nas_run
    legacy.mount_check = lambda path, unused: mount_check(path, c['role'])
    legacy.persist_home = persist_prepared
    legacy.payload_manifest = payload_manifest
    legacy.command = _command
    # Every monitor tick includes failed partials and all preparations already on
    # this store. The source plus derived budget is cumulative, not per-file.
    legacy.check_budget = cumulative_budget


def cumulative_budget(root, maximum=None, reserve=4*1024**2):
    maximum = maximum or legacy.MAX_BYTES
    roots = [Path(root), *OPERATION_ROOTS]
    unique = []
    for p in sorted(set(roots), key=lambda p: len(p.parts)):
        if not any(p.is_relative_to(parent) for parent in unique): unique.append(p)
    need(sum(size(p) for p in unique if p.exists()) <= maximum-reserve, 'run_byte_budget')
    for p in unique:
        if p.exists():
            s = os.statvfs(p); need(s.f_bavail*s.f_frsize >= 2*maximum, 'insufficient_free_space')


def native_source(url, state):
    source = parse_source(url)
    path = child(state, 'native-source.json'); e = read_json(path)
    # Pin a syntactically valid request before any source prerequisite can fail.
    # Refreshing browser evidence must never refresh this request's deadline.
    request_receipt(state,e)
    need(isinstance(e, dict) and e.get('submitted_url') == source['url'], 'native_source_binding_missing')
    canonical = parse_source(e.get('canonical_url', ''))
    need(canonical['platform'] == source['platform'] and canonical['video_id'] and
         (source['video_id'] is None or canonical['video_id'] == source['video_id']), 'native_source_identity_mismatch')
    need(e.get('host') == 'Macmini' and e.get('user') == 'fastshower' and e.get('control') == 'native_chrome', 'native_source_control_unverified')
    need(type(e.get('playback_start')) in (int,float) and type(e.get('playback_end')) in (int,float) and
         0 <= e['playback_start'] < e['playback_end'] <= e.get('duration', 0), 'playback_not_advancing')
    validate_duration(source['platform'], e.get('duration'))
    need(isinstance(e.get('title'), str) and e['title'].strip() and isinstance(e.get('publisher'), str) and e['publisher'].strip(), 'source_description_missing')
    now = time.time(); created = e.get('verified_epoch')
    need(type(created) in (int,float) and 0 <= now-created <= 240, 'native_source_evidence_expired')
    remaining_deadline(e.get('deadline_epoch'), now, 60)
    need(e['deadline_epoch'] <= created + LIMITS[source['platform']][2], 'deadline_exceeds_platform_limit')
    # Persist only known fields. Never copy arbitrary browser URL/storage dumps.
    allowed = ('submitted_url','canonical_url','host','user','control','playback_start','playback_end','duration','title','publisher','verified_epoch','deadline_epoch',
               'audio_state','source_reset','ad_state','request_id','window','opening_picture_verified','window_corroborated')
    if 'window' in e: validate_window(e['window'])
    return {k:e[k] for k in allowed if k in e}


def request_path(state,native):
    identity = native.get('request_id')
    need(isinstance(identity,str) and re.fullmatch('[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}',identity),'invalid_request_id')
    return child(state,'request-'+identity+'.json')


def request_receipt(state,native):
    need(isinstance(native,dict),'invalid_request_binding')
    path = request_path(state,native)
    need(isinstance(native.get('canonical_url'),str),'invalid_request_binding')
    parse_source(native['canonical_url'])
    need(type(native.get('deadline_epoch')) in (int,float) and math.isfinite(native['deadline_epoch']) and native['deadline_epoch'] > 0,'invalid_request_binding')
    binding = {k:native[k] for k in ('request_id','canonical_url','deadline_epoch')}
    if 'request_created_epoch' in native:binding['request_created_epoch'] = native['request_created_epoch']
    exists = path.exists()
    receipt = read_json(path) if exists else dict(binding,attempted=[])
    need(all(receipt.get(k) == v for k,v in binding.items()),'immutable_request_binding_changed')
    if not exists:write_json(path,receipt)
    return path,receipt


def reserve_attempt(state, native, method, run=None):
    path,receipt = request_receipt(state,native)
    need(method in ('direct','record','content') and method not in receipt['attempted'], 'attempt_already_dispatched_collect_existing')
    receipt['attempted'].append(method)
    if run is not None:
        run_identity(run)
        receipt.setdefault('runs',[]).append(run)
        receipt.setdefault('by_method',{})[method] = run
    write_json(path, receipt)
    return receipt


def recording_ready(native):
    need(native.get('audio_state') == 'unmuted_control_observed' and native.get('source_reset') == 'paused_at_zero' and
         native.get('ad_state') == 'source_without_ad', 'recording_source_audio_reset_or_ad_unverified')
    validate_window(native.get('window'))
    need(native.get('opening_picture_verified') is True and native.get('window_corroborated') is True,'native_recording_opening_picture_unverified')
    return True


def validate_window(window):
    keys = {'id','process_id','app','title','x','y','width','height'}
    need(isinstance(window,dict) and set(window) == keys,'invalid_native_window')
    need(type(window['id']) is int and 0 < window['id'] <= 2**32-1 and type(window['process_id']) is int and window['process_id'] > 0,'invalid_native_window')
    need(window['app'] == 'com.google.Chrome' and isinstance(window['title'],str) and 0 < len(window['title']) <= 2048,'invalid_native_window')
    need(all(type(window[k]) in (int,float) and math.isfinite(window[k]) for k in ('x','y','width','height')) and
         window['width'] > 320 and window['height'] > 200,'invalid_native_window_geometry')
    return True


def window_binding(expected,actual):
    validate_window(expected); validate_window(actual)
    need(expected == actual,'recording_target_changed_or_ambiguous')
    return True


def recovery_binding(old,current):
    need(all(old.get(k) == current.get(k) for k in ('canonical_url','duration','title','publisher')),'source_changed_recovery_refused')
    return True


def committed_source(root,run):
    commit = read_json(child(root,'source-commit.json'))
    need(commit.get('run_id') == run,'source_commit_binding_mismatch')
    digest = commit.get('manifest_sha256'); verify(root,digest)
    return digest


def direct_metadata(url, root, deadline):
    check_remote(url)
    # Installed official artifact includes bundled EJS; fixed Deno, no plugins.
    tool = PACKAGE/'tools/yt-dlp'
    need(tool.is_file() and sha(tool) == CURRENT.get('downloader_sha256'), 'pinned_downloader_unavailable')
    args = [str(tool),'--ignore-config','--no-plugin-dirs','--no-cache-dir','--no-playlist','--skip-download','--dump-single-json',
        '--js-runtimes','deno:/opt/homebrew/bin/deno','--socket-timeout','15','--retries','0','--proxy','']
    end = min(deadline,time.monotonic()+120)
    try: return json.loads(legacy.command([*args,url],root,end))
    except Blocked as error:
        if not str(error).startswith('command_failed_'): raise
        profile = CURRENT.get('browser_profile')
        need(isinstance(profile,str) and re.fullmatch(r'Default|Profile [0-9]{1,3}',profile), 'selected_mini_browser_profile_unverified')
        need(time.monotonic()+15 < end,'direct_metadata_deadline')
        # Credentials remain in the owning Mini process. No cookies file or
        # browser database is included in source payloads, NAS or diagnostics.
        return json.loads(legacy.command([*args,'--cookies-from-browser','chrome:'+profile,url],root,end))


def recorder_accepted(state):
    path = child(state,'recorder-acceptance.json')
    if not path.is_file(): return False
    evidence = read_json(path)
    required = ('picture_reviewed','actual_source_speech_reviewed','opening_reviewed','ending_reviewed','seams_reviewed')
    return (evidence.get('recorder_sha256') == CURRENT.get('recorder_sha256') and
            evidence.get('package') == CURRENT.get('package') and
            all(evidence.get(k) is True for k in required) and bool(evidence.get('retained_run_id')))


def caption_format_valid(extension,data):
    try:
        if extension == 'vtt': return data.lstrip().startswith(b'WEBVTT')
        if extension == 'json3':
            obj = json.loads(data)
            return isinstance(obj,dict) and isinstance(obj.get('events'),list)
        if extension in ('ttml','srv1','srv2','srv3') and b'<!DOCTYPE' not in data.upper() and b'<!ENTITY' not in data.upper():
            import xml.etree.ElementTree as ET
            root = ET.fromstring(data)
            return root.tag.split('}')[-1] in ('tt','transcript','timedtext')
    except (ValueError,TypeError): return False
    except Exception: return False
    return False


def caption_evidence(metadata, root, deadline):
    retained = []; phase_deadline = min(deadline,time.monotonic()+90); examined = 0
    for kind in ('subtitles','automatic_captions'):
        tracks = metadata.get(kind) or {}
        if not isinstance(tracks,dict): continue
        for language,choices in tracks.items():
            examined += 1
            if examined > 64:
                retained.append({'kind':kind,'status':'additional_tracks_omitted_bounded_caption_phase'}); break
            if not isinstance(language,str) or not re.fullmatch('[A-Za-z0-9_-]{1,32}',language):
                retained.append({'kind':kind,'status':'unsupported_language_identifier'}); continue
            item = None
            if isinstance(choices,list):
                for extension in ('vtt','json3','ttml','srv3','srv2','srv1'):
                    item = next((x for x in choices if isinstance(x,dict) and x.get('ext') == extension),None)
                    if item is not None: break
            if item is None:
                retained.append({'language':language,'kind':kind,'status':'unsupported_timed_caption_format'}); continue
            if time.monotonic()+5 >= phase_deadline:
                retained.append({'language':language,'kind':kind,'status':'unavailable_caption_phase_deadline'}); continue
            url = item.get('url','')
            # Explicitly validate redirects before a request, and peers after.
            class CaptionRedirect(urllib.request.HTTPRedirectHandler):
                def redirect_request(self, req, fp, code, msg, headers, newurl):
                    check_remote(newurl)
                    return super().redirect_request(req,fp,code,msg,headers,newurl)
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}),CaptionRedirect())
            name = 'captions-'+kind+'-'+language+'.'+item['ext']
            try:
                check_remote(url)
                need(time.monotonic() < deadline,'run_deadline')
                with opener.open(url,timeout=min(20,max(1,phase_deadline-time.monotonic()))) as response:
                    check_remote(response.geturl()); legacy.check_response_peer(response)
                    data = response.read(2*1024**2+1)
                    need(len(data) <= 2*1024**2 and caption_format_valid(item['ext'],data), 'invalid_or_large_caption')
                cumulative_budget(root,reserve=len(data)+1024*1024)
                partials = safe_dir(root/'partials')
                staged = child(partials,name+'.partial-'+secrets.token_hex(8))
                try:
                    with staged.open('xb') as target:
                        target.write(data); target.flush(); os.fsync(target.fileno())
                    need(not (root/name).exists(),'caption_target_exists')
                    staged.rename(root/name)
                except OSError:
                    retained.append({'language':language,'kind':kind,'status':'caption_write_failed_preserved_partial','partial':str(staged.relative_to(root))})
                    continue
                retained.append({'name':name,'language':language,'kind':kind,'format':item['ext'],'source':'extractor_timed_caption_track'})
            except urllib.error.HTTPError as error:
                retained.append({'language':language,'kind':kind,'status':'unavailable_http_'+str(error.code)})
            except Blocked as error:
                # Captions cannot weaken the operation's hard limits or leave
                # a failed write disguised as auxiliary-source unavailability.
                if str(error) in ('run_deadline','run_byte_budget','insufficient_free_space'): raise
                retained.append({'language':language,'kind':kind,'status':'unavailable_validation'})
            except (urllib.error.URLError, OSError, TimeoutError, ValueError):
                retained.append({'language':language,'kind':kind,'status':'unavailable_transport'})
    return retained


def validate_recording(probe, evidence, source_duration):
    validate_duration(evidence['platform'], source_duration)
    streams = {s.get('codec_type') for s in probe.get('streams', [])}
    need({'audio','video'} <= streams, 'missing_recording_audio_or_video')
    actual = float(probe['format']['duration'])
    need(math.isfinite(actual) and source_duration <= actual <= source_duration+120, 'recording_duration_or_padding_mismatch')
    need(evidence.get('microphone') is False and evidence.get('system_audio') is True, 'recording_audio_route_unverified')
    # Actual words/boundaries are assessed after ASR and source review, never
    # inferred from stream existence or a volume statistic.
    return {'coverage_status':'requires_boundary_and_speech_review', 'source_duration':source_duration,
            'recording_duration':actual, 'padding_seconds':actual-source_duration, 'audio_verified':False}


def recording_seconds(native):
    return validate_duration(parse_source(native['canonical_url'])['platform'],native['duration'])+60


def recording_handshake(root,native,recording,launched_epoch,completed_epoch):
    # Read only fixed run-local files. A callback marker is evidence of actual
    # output startup, while native CUA reports its own visible observations.
    recording_ready(native)  # Visible source opening remains mandatory before capture.
    ready_path = child(root,'recording-started.json'); start_path = child(root,'native-playback-started.json')
    need(ready_path.is_file(),'recording_ready_marker_missing_or_invalid')
    need(start_path.is_file(),'native_playback_start_binding_missing')
    ready = read_json(ready_path); start = read_json(start_path)
    need(isinstance(ready,dict) and ready.get('version') == 1,'recording_ready_marker_missing_or_invalid')
    for key in ('request_id','canonical_url','deadline_epoch'):
        need(ready.get(key) == native.get(key),'recording_ready_request_mismatch')
    need(ready.get('source_duration') == native['duration'] and ready.get('package') == CURRENT.get('package') and
         ready.get('recorder_sha256') == CURRENT.get('recorder_sha256'),'recording_ready_artifact_mismatch')
    window_binding(native['window'],ready.get('window')); window_binding(native['window'],recording.get('window'))
    epoch = ready.get('ready_epoch'); uptime = ready.get('ready_uptime')
    need(type(epoch) in (int,float) and math.isfinite(epoch) and launched_epoch <= epoch <= completed_epoch < native['deadline_epoch'] and
         type(uptime) in (int,float) and math.isfinite(uptime) and uptime > 0,'recording_ready_timing_invalid')
    digest = sha(ready_path)
    need(recording.get('ready_marker_sha256') == digest and recording.get('ready_epoch') == epoch,'recording_ready_output_mismatch')
    need(isinstance(start,dict) and all(start.get(k) == native.get(k) for k in ('request_id','canonical_url','title')) and
         start.get('ready_marker_sha256') == digest and start.get('control') == 'native_chrome','native_playback_start_binding_missing')
    need(start.get('version') == 2 and start.get('observation_clock_basis') == 'native_call_completion_epoch' and
         'opening_picture_verified' not in start,'unsupported_native_start_observation_protocol')
    need(start.get('source_reset') == 'paused_at_zero' and start.get('ad_state') == 'source_without_ad' and
         start.get('audio_state') == 'unmuted_control_observed' and start.get('window_corroborated') is True and
         start.get('source_identity_corroborated') is True,'native_post_ready_source_identity_or_control_unverified')
    picture = start.get('picture_observation')
    need(picture in ('source_picture_visible','ambiguous_black_during_capture'),'native_picture_observation_invalid')
    window_binding(ready['window'],start.get('window'))
    observed = start.get('observed_epoch'); clock = start.get('playback_clock')
    call_started = start.get('observation_call_started_epoch'); written = start.get('receipt_written_epoch')
    need(type(call_started) in (int,float) and math.isfinite(call_started) and
         type(observed) in (int,float) and math.isfinite(observed) and epoch <= call_started <= observed and
         type(written) in (int,float) and math.isfinite(written) and observed <= written <= completed_epoch,
         'native_observation_call_timing_invalid')
    need(type(observed) in (int,float) and math.isfinite(observed) and epoch <= observed <= completed_epoch and
         type(clock) in (int,float) and math.isfinite(clock) and 0 < clock <= min(native['duration'],observed-epoch+1),
         'native_playback_start_missing_or_invalid')
    target_met = observed <= epoch+30
    return {'ready_marker_sha256':digest,'ready_epoch':epoch,'native_start_observed_epoch':observed,
        'native_start_target_seconds':30,'native_start_target_met':target_met,
        'warnings':[] if target_met else ['late_native_playback_confirmation_requires_full_content_proof'],
        'full_episode_content_proof_required':True,
        'native_start_delay_seconds':observed-epoch,'observed_playback_clock':clock,
        'source_clock_offset_status':'uncertain_observations_only','ending_and_source_speech_review_required':True,
        'native_observation_call_uncertainty_seconds':observed-call_started,'receipt_written_epoch':written,
        'native_picture_observation':picture,'recorded_opening_and_ending_review_required':True}


def run_recorder(args,root,deadline):
    try: return legacy.command(args,root,deadline)
    except Blocked as error:
        marker = ' diagnostics='
        if marker not in str(error): raise
        path = child(root,Path(str(error).split(marker,1)[1]).name)
        diagnostic = read_json(path)
        try: report = json.loads(diagnostic.get('stderr_redacted',''))
        except (ValueError,TypeError): raise error
        reason = report.get('reason') if isinstance(report,dict) else None
        if isinstance(report,dict) and report.get('status') == 'blocked' and isinstance(reason,str) and re.fullmatch('[a-z0-9_]{1,47}',reason):
            raise Blocked(reason+marker+str(path)) from None
        raise error


def validate_late_recovery(root,ledger,run,media_digest,context_digest,now):
    need(run == LATE_RECOVERY_RUN,'recovery_not_authorized_for_run')
    need(re.fullmatch('[a-f0-9]{64}',media_digest or '') and re.fullmatch('[a-f0-9]{64}',context_digest or ''),'invalid_recovery_digest')
    context_path = child(root,'recovery-context.json')
    need(sha(context_path) == context_digest,'recovery_context_hash_mismatch')
    context = read_json(context_path)
    need(context.get('version') == 1 and context.get('run_id') == run and context.get('request_id') == LATE_RECOVERY_REQUEST and
         context.get('authority') == 'keith_2026-11-08_late_confirmation','recovery_authority_binding_mismatch')
    files = context.get('files'); expected = {'source.mp4','native-source.json','recording-started.json','native-playback-started.json','original-command-result.json'}
    need(isinstance(files,dict) and set(files) == expected,'invalid_recovery_file_set')
    for name,digest in files.items():
        need(isinstance(digest,str) and re.fullmatch('[a-f0-9]{64}',digest) and sha(child(root,name)) == digest,'recovery_payload_hash_mismatch')
    need(files['source.mp4'] == media_digest,'recovery_media_hash_mismatch')
    native = read_json(root/'native-source.json'); ready = read_json(root/'recording-started.json')
    need(native.get('request_id') == LATE_RECOVERY_REQUEST and all(ledger.get(k) == native.get(k) for k in ('request_id','canonical_url','deadline_epoch')) and
         ledger.get('attempted') == ['record'] and ledger.get('runs') == [run] and ledger.get('by_method') == {'record':run},'recovery_existing_attempt_mismatch')
    need(parse_source(native.get('canonical_url','')) == {'platform':'youtube','video_id':'bc7QaDmLmoA','url':'https://www.youtube.com/watch?v=bc7QaDmLmoA'},'recovery_source_mismatch')
    need(ready.get('package') == LATE_RECOVERY_PACKAGE,'recovery_capture_package_mismatch')
    original = read_json(root/'original-command-result.json')
    need(original.get('exit_code') == 2 and original.get('result') == {'status':'blocked','reason':'native_playback_start_missing_or_late'},'recovery_original_failure_mismatch')
    started = native.get('verified_epoch'); finished = original.get('observed_epoch')
    need(type(started) in (int,float) and math.isfinite(started) and type(finished) in (int,float) and math.isfinite(finished) and
         started <= ready.get('ready_epoch',-1) <= finished <= now < native['deadline_epoch'] and
         finished-started <= validate_duration('youtube',native.get('duration'))+120,'recovery_hard_timing_violation')
    return {'status':'pending_content_verification','request_id':native['request_id'],'run_id':run,'context_sha256':context_digest,
        'raw_media_sha256':media_digest,'original_result':original['result'],'original_result_sha256':files['original-command-result.json'],
        'unretained_recorder_stdout':'unavailable_not_reconstructed',
        'prior_checks_proven_by_pinned_control_flow':['recorder_command_success','recorder_json_parsed','returned_window_binding'],
        'complete_source_identity_opening_speech_ending_and_episode_coverage_required':True}


def publish_source(root,digest,deadline):
    manifest = verify(root,digest)
    mount_check(NAS,'mini')
    need(time.monotonic() < deadline,'run_deadline')
    # Completed-copy reuse can skip every copy-loop monitor. Count the full
    # local operation and existing NAS run before reporting publication again.
    dest = nas_run(root.name);OPERATION_ROOTS.append(dest)
    cumulative_budget(root)
    content = manifest.get('evidence_type') == 'caption_visual'
    legacy.space_check(root,legacy.MAX_BYTES);safe_dir(dest)
    for item in [*manifest['files'],{'name':'manifest.json','sha256':digest}]:
        need(time.monotonic() < deadline,'run_deadline')
        legacy.publish_file(child(root,item['name']),child(dest,item['name']),item['sha256'],dest,deadline)
    verify(dest,digest)
    need(time.monotonic() < deadline,'run_deadline');cumulative_budget(root)
    result = {'status':'content_evidence_published' if content else 'pending_content_verification' if manifest.get('recovery') else 'captured','run_id':root.name,'manifest_sha256':digest,
        'nas_path':str(dest),'local_path':str(root),'bytes':size(root),'evidence_type':'caption_visual' if content else 'media'}
    if content:result['complete_media_available'] = False
    elif manifest.get('recovery'):result['recovery'] = manifest['recovery']
    write_json(root/'completion.json',result)
    return result


def finite_number(value):
    return type(value) in (int,float) and math.isfinite(value)


def content_context(path,digest):
    need(path.is_file() and not path.is_symlink(),'content_evidence_missing_or_invalid')
    need(path.stat().st_size <= 1024**2,'content_evidence_size_limit')
    with path.open('rb') as stream:data = stream.read(1024**2+1)
    need(len(data) <= 1024**2,'content_evidence_size_limit')
    need(isinstance(digest,str) and re.fullmatch('[a-f0-9]{64}',digest) and hashlib.sha256(data).hexdigest() == digest,'content_evidence_digest_mismatch')
    return json.loads(data)


def validate_content_evidence(root,evidence,now=None):
    need(isinstance(evidence,dict) and evidence.get('version') == 1 and evidence.get('evidence_type') == 'caption_visual','invalid_content_evidence_type')
    source = parse_source(evidence.get('canonical_url',''))
    request_path(root,evidence)
    need(source['platform'] == 'youtube' and evidence.get('submitted_url') == source['url'] == evidence['canonical_url'],'content_source_identity_mismatch')
    need(evidence.get('host') == 'Macmini' and evidence.get('user') == 'fastshower' and evidence.get('control') == 'native_chrome','native_content_control_unverified')
    duration = validate_duration('youtube',evidence.get('source_duration'))
    need(all(isinstance(evidence.get(k),str) and 0 < len(evidence[k]) <= 2048 for k in ('title','publisher')),'source_description_missing')
    created = evidence.get('request_created_epoch'); deadline = evidence.get('deadline_epoch')
    need(finite_number(created) and finite_number(deadline) and 0 < created < deadline <= created+LIMITS['youtube'][2],'invalid_content_request_deadline')
    if now is not None:need(finite_number(now) and created <= now < deadline,'expired_content_publication_request')
    observed = evidence.get('caption_observation_window')
    need(isinstance(observed,dict) and isinstance(observed.get('basis'),str) and observed['basis'].strip() and
         finite_number(observed.get('start_epoch')) and finite_number(observed.get('end_epoch')) and
         0 < observed['start_epoch'] <= observed['end_epoch'] <= created,'invalid_caption_observation_window')
    need(evidence.get('analysis_kind') == 'timestamped_paraphrase' and evidence.get('complete_media_available') is False and
         evidence.get('full_visual_review') is False and evidence.get('caption_end_times') == 'not_exposed' and
         evidence.get('speech_completeness') == 'unverified','unsupported_content_completeness_claim')
    need(evidence.get('raw_caption_retention_status') in ('not_retained','excerpts_only') and
         isinstance(evidence.get('raw_caption_retention_reason'),str) and evidence['raw_caption_retention_reason'].strip(),'raw_caption_retention_unspecified')
    files = evidence.get('files')
    need(isinstance(files,dict) and set(files) == set(CONTENT_FILES),'invalid_content_payload_set')
    for name,digest in files.items():
        p = child(root,name)
        need(p.is_file() and p.stat().st_size <= 2*1024**2 and isinstance(digest,str) and re.fullmatch('[a-f0-9]{64}',digest) and sha(p) == digest,'content_payload_hash_mismatch')
    metadata = read_json(root/'caption-metadata.json'); claims = read_json(root/'claims.json')
    previous = read_json(root/'prior-visual-notes.json'); visuals = read_json(root/'visual-observations.json')
    for data in (metadata,claims,previous,visuals):
        need(isinstance(data,dict) and data.get('source_url') == source['url'],'content_payload_source_mismatch')
    for data in (metadata,claims,visuals):
        need(all(data.get(k) == evidence[k] for k in ('title','publisher')),'content_description_mismatch')
    need(metadata.get('source_duration_seconds') == claims.get('duration_seconds') == visuals.get('source_duration_seconds') == duration,'content_duration_mismatch')
    need(metadata.get('control') == visuals.get('control') == 'native_chrome' and previous.get('full_visual_viewing') is False and
         visuals.get('full_visual_viewing') is False,'native_visual_control_or_coverage_invalid')
    starts = metadata.get('caption_start_seconds_in_sequence'); count = metadata.get('observed_caption_count')
    need(type(count) is int and 0 < count <= 20000 and isinstance(starts,list) and len(starts) == count and
         all(finite_number(t) and 0 <= t < duration for t in starts) and starts == sorted(starts),'invalid_caption_start_sequence')
    need(metadata.get('first_caption_reached') is True and metadata.get('last_caption_reached') is True and
         evidence.get('caption_sequence_status') == 'all_exposed_rows_inspected','native_caption_sequence_incomplete')
    label = metadata.get('provenance_label_exact'); language = metadata.get('caption_language'); provenance = evidence.get('caption_provenance_kind')
    need(isinstance(label,str) and 0 < len(label) <= 512 and isinstance(language,str) and 0 < len(language) <= 256 and
         provenance in ('auto_generated','manual','unknown'),'caption_provenance_missing')
    if provenance == 'auto_generated':need('auto-generated' in label.lower(),'caption_auto_label_mismatch')
    if provenance == 'manual':need(metadata.get('caption_provenance') == 'manual' and 'auto-generated' not in label.lower(),'caption_manual_provenance_unverified')
    need(isinstance(claims.get('claims'),list) and 0 < len(claims['claims']) <= 1000,'timestamped_claims_missing')
    for claim in claims['claims']:
        need(isinstance(claim,dict) and isinstance(claim.get('range'),str) and isinstance(claim.get('claim'),str) and claim['claim'].strip(),'invalid_timestamped_claim')
        if claim['range'] == 'whole source':continue  # Source-wide interpretation, never a visual-coverage claim.
        span = re.fullmatch(r'([0-9]{1,2}):([0-5][0-9])-([0-9]{1,2}):([0-5][0-9])',claim['range'])
        need(span is not None,'invalid_claim_timestamp')
        begin,end = int(span[1])*60+int(span[2]),int(span[3])*60+int(span[4])
        need(0 <= begin <= end <= duration,'claim_timestamp_outside_source')
    analysis = (root/'analysis.md').read_text()
    need(source['url'] in analysis and len(analysis.strip()) > len(source['url'])+10,'source_linked_paraphrase_missing')
    observations = visuals.get('observations')
    need(isinstance(observations,list) and 0 < len(observations) <= 128,'native_visual_observations_missing')
    for item in observations:
        need(isinstance(item,dict) and finite_number(item.get('requested_second')) and 0 <= item['requested_second'] <= duration and
             finite_number(item.get('observation_call_started_epoch')) and finite_number(item.get('observed_epoch')) and
             0 < item['observation_call_started_epoch'] <= item['observed_epoch'] <= created,'invalid_visual_observation_timing')
        clock = item.get('clock_seconds')
        need(clock is None or (finite_number(clock) and 0 <= clock <= duration),'invalid_observed_source_clock')
        need(all(isinstance(item.get(k),str) and 0 < len(item[k]) <= 16384 for k in ('description','uncertainty')),'visual_description_or_uncertainty_missing')
    return {'evidence_type':'caption_visual','method':'native_captions_and_visual_checks','complete_media_available':False,
        'analysis_kind':'timestamped_paraphrase','raw_caption_retention_status':evidence['raw_caption_retention_status'],
        'raw_caption_retention_reason':evidence['raw_caption_retention_reason'],'caption_end_times':'not_exposed','speech_completeness':'unverified',
        'caption_count':count,'first_caption_start':starts[0],'last_caption_start':starts[-1],'caption_language':language,
        'caption_provenance_kind':provenance,'caption_provenance_label':label,'caption_observation_window':observed,
        'caption_sequence_status':evidence['caption_sequence_status'],'caption_coverage_notes':metadata.get('coverage_notes',[]),
        'source_uncertainties':[item.get('limits','Unverified source claim') for item in claims['claims']],
        'prior_visual_limitations':previous.get('limitations','Prior native observations only; timing may be uncertain'),
        'visual_observation_count':len(observations),'visual_observations':observations,'full_visual_review':False,
        'visual_artifacts':'native_observation_notes_not_retained_video_frames',
        'source_uncertainty':'Caption evidence and worker paraphrase are not independently verified source claims or complete speech'}


def publish_content(url,request_id,evidence_digest,state):
    need(parse_source(url)['platform'] == 'youtube','content_requires_youtube')
    request_path(state,{'request_id':request_id})
    draft = child(state,'content-drafts/'+request_id); path = child(draft,'evidence.json')
    evidence = content_context(path,evidence_digest)
    need(isinstance(evidence,dict) and evidence.get('request_id') == request_id and evidence.get('canonical_url') == parse_source(url)['url'],'content_request_binding_mismatch')
    # Preserve the first syntactically valid request, even when expiry or a
    # source prerequisite fails. A corrected draft cannot renew its clock.
    ledger_path,ledger = request_receipt(state,evidence)
    coverage = validate_content_evidence(draft,evidence,time.time())
    deadline = time.monotonic()+remaining_deadline(evidence['deadline_epoch'],time.time(),60)
    mount_check(NAS,'mini'); legacy.space_check(state,LIMITS['youtube'][1])
    old_run = ledger.get('by_method',{}).get('content')
    if old_run:
        root = child(state,old_run); digest = committed_source(root,old_run); manifest = verify(root,digest)
        need(manifest.get('evidence_type') == 'caption_visual' and manifest.get('evidence_sha256') == evidence_digest,'content_committed_binding_mismatch')
        OPERATION_ROOTS[:] = [draft,root,nas_run(old_run)]
        return dict(publish_source(root,digest,deadline),reused=True)
    source = parse_source(url)
    run = f"youtube-{source['video_id']}-{dt.datetime.now(dt.timezone.utc):%Y%m%dT%H%M%SZ}-{secrets.token_hex(4)}"
    reserve_attempt(state,evidence,'content',run)
    root = safe_dir(state/run); OPERATION_ROOTS[:] = [draft,root,nas_run(run)]
    for name in (*CONTENT_FILES,'evidence.json'):
        source_file = child(draft,name); target = child(root,name)
        with source_file.open('rb') as inp,target.open('xb') as out:
            out.write(inp.read(2*1024**2+1));out.flush();os.fsync(out.fileno())
        need(sha(target) == sha(source_file),'content_staging_hash_mismatch');cumulative_budget(root)
    copied = content_context(root/'evidence.json',evidence_digest)
    need(copied == evidence and validate_content_evidence(root,copied,time.time()) == coverage,'content_staging_context_changed')
    (root/'run-marker.txt').write_text(secrets.token_hex(32)+'\n')
    digest = payload_manifest(root,{'version':VERSION,'platform':'youtube','run_id':run,'video_id':source['video_id'],
        'source_duration':evidence['source_duration'],'evidence_type':'caption_visual','method':'native_captions_and_visual_checks',
        'evidence_sha256':evidence_digest,'marker_sha256':sha(root/'run-marker.txt'),'coverage':coverage})
    write_json(root/'source-commit.json',{'run_id':run,'manifest_sha256':digest})
    return publish_source(root,digest,deadline)


def read_content_bundle(run,digest,manifest):
    src = nas_run(run); evidence = content_context(child(src,'evidence.json'),manifest.get('evidence_sha256'))
    source = parse_source(evidence.get('canonical_url',''))
    need((source['platform'],source['video_id']) == run_identity(run),'content_run_source_identity_mismatch')
    need(sha(src/'run-marker.txt') == manifest.get('marker_sha256'),'content_manifest_binding_mismatch')
    coverage = validate_content_evidence(src,evidence)
    need(manifest.get('coverage') == coverage and manifest.get('source_duration') == evidence['source_duration'] and
         manifest.get('method') == 'native_captions_and_visual_checks','content_manifest_claims_mismatch')
    # The immutable source already is the retained analysis. Read it directly;
    # no copied caption cache, transcript or second publication state machine.
    need(verify(src,digest) == manifest,'content_source_changed_during_read')
    return dict(coverage,status='content_evidence_verified_for_assistant_read',run_id=run,source_manifest_sha256=digest,
        reader_version=processor_id(),reader_host=CURRENT['role'],source_duration=evidence['source_duration'],
        canonical_url=evidence['canonical_url'],title=evidence['title'],publisher=evidence['publisher'],source_evidence=str(src/'evidence.json'),
        source_bundle=str(src),analysis=str(src/'analysis.md'),claims=str(src/'claims.json'),caption_metadata=str(src/'caption-metadata.json'),
        visual_notes=str(src/'visual-observations.json'),prior_visual_notes=str(src/'prior-visual-notes.json'),
        source_payloads=manifest['files'],reused=True,nas_publication_verified=True,
        content_review_status='requires_assistant_read_and_source_interpretation')


def recover_recording(run,media_digest,context_digest,state):
    root = child(state,run); native = read_json(child(root,'native-source.json'))
    ledger_path = request_path(state,native); need(ledger_path.is_file(),'recovery_existing_attempt_missing')
    ledger = read_json(ledger_path); ledger_digest = sha(ledger_path)
    recovery = validate_late_recovery(root,ledger,run,media_digest,context_digest,time.time())
    old_package = child(PACKAGE.parent,LATE_RECOVERY_PACKAGE); old = install_config(old_package)
    need(old['role'] == 'mini' and old['files']['lib/video_extract.py'] == LATE_RECOVERY_CODE,'recovery_old_capture_code_mismatch')
    ready = read_json(root/'recording-started.json')
    need(old.get('recorder_sha256') == ready.get('recorder_sha256'),'recovery_recorder_hash_mismatch')
    deadline = time.monotonic()+remaining_deadline(native['deadline_epoch'],time.time(),60)
    mount_check(NAS,'mini'); OPERATION_ROOTS[:] = [root,nas_run(run)]
    if (root/'source-commit.json').exists():
        digest = committed_source(root,run); manifest = verify(root,digest)
        need(manifest.get('recovery',{}).get('context_sha256') == context_digest,'recovery_commit_context_mismatch')
        return publish_source(root,digest,deadline)
    need(not (root/'manifest.json').exists() and not (root/'recording.json').exists(),'recovery_unexpected_committed_or_recording_state')
    # The old failure proves those prior checks, not unretained stdout fields.
    # This explicitly derived envelope is never attributed to recorder output.
    evidence = {'window':ready['window'],'ready_marker_sha256':sha(root/'recording-started.json'),'ready_epoch':ready['ready_epoch']}
    original = read_json(root/'original-command-result.json')
    current = dict(CURRENT)
    try:
        CURRENT.update(package=old['package'],recorder_sha256=old['recorder_sha256'])
        handshake = recording_handshake(root,native,evidence,native['verified_epoch'],original['observed_epoch'])
    finally:
        CURRENT.clear(); CURRENT.update(current)
    need(handshake['native_start_target_met'] is False,'recovery_not_a_late_confirmation_case')
    recovery.update(handshake=handshake,capture_package=old['package'],capture_code_sha256=LATE_RECOVERY_CODE,
        recorder_sha256=old['recorder_sha256'],sound_route_basis='hash_verified_pinned_recorder_configuration_microphone_false_system_audio_true')
    media = legacy.probe(root/'source.mp4',root,deadline)
    coverage = validate_recording(media,{'platform':'youtube','microphone':False,'system_audio':True},native['duration'])
    legacy.command([legacy.TOOLS['ffmpeg'],'-nostdin','-v','error','-xerror','-i',str(root/'source.mp4'),'-map','0:v:0','-map','0:a:0','-f','null','-'],root,deadline)
    speech = root/'speech.flac'
    if speech.exists():
        # An interrupted export is retained, never silently reused or deleted.
        speech.rename(safe_dir(root/'partials')/('speech-interrupted-'+secrets.token_hex(8)+'.flac'))
    legacy.command([legacy.TOOLS['ffmpeg'],'-nostdin','-v','error','-n','-i',str(root/'source.mp4'),'-map','0:a:0','-ac','1','-ar','16000','-c:a','flac',str(speech)],root,deadline)
    validate_late_recovery(root,read_json(ledger_path),run,media_digest,context_digest,time.time())
    need(sha(root/'source.mp4') == media_digest and sha(ledger_path) == ledger_digest,'recovery_media_or_ledger_changed')
    # All original evidence remains untouched. These are new pending records.
    write_json(root/'recovery-staging.json',recovery)
    write_json(root/'acquisition.json',dict(native,method='playback_recording',coverage=coverage,captions=[],recovery=recovery))
    marker = root/'run-marker.txt'
    if not marker.exists():marker.write_text(secrets.token_hex(32)+'\n')
    digest = payload_manifest(root,{'version':VERSION,'platform':'youtube','run_id':run,'video_id':'bc7QaDmLmoA','duration':float(media['format']['duration']),
        'source_duration':native['duration'],'method':'playback_recording','marker_sha256':sha(marker),'coverage':coverage,'recovery':recovery})
    write_json(root/'source-commit.json',{'run_id':run,'manifest_sha256':digest})
    return publish_source(root,digest,deadline)


def capture(url, state, record_only=False):
    native = native_source(url, state); source = parse_source(native['canonical_url']); platform = source['platform']
    mount_check(NAS,'mini'); legacy.space_check(state, LIMITS[platform][1])
    deadline = time.monotonic()+remaining_deadline(native['deadline_epoch'],time.time(),60)
    method_key = 'record' if record_only else 'direct'
    previous,receipt = request_receipt(state,native)
    if previous.exists():
        receipt = read_json(previous); old_run = receipt.get('by_method',{}).get(method_key)
        if old_run:
            run_identity(old_run); old_root = child(state,old_run)
            digest = committed_source(old_root,old_run)
            old_manifest = verify(old_root,digest); old_native = read_json(old_root/'native-source.json')
            recovery_binding(old_native,native)
            need(old_manifest.get('run_id') == old_run and old_manifest.get('video_id') == source['video_id'] and
                 old_manifest.get('platform') == platform and abs(old_manifest.get('source_duration',0)-native['duration']) < 1,'source_changed_recovery_refused')
            OPERATION_ROOTS[:] = [child(state,r) for r in receipt.get('runs',[])]
            result = publish_source(old_root,digest,deadline)
            return dict(result,reused=True,recovery='verified_source_publication_only')
    probe_root = None
    if record_only:
        recording_ready(native)
        need(recorder_accepted(state), 'recorder_live_acceptance_required')
        recorder = PACKAGE/'tools/record'
        probe_root = safe_dir(state/('preflight-'+native['request_id']))
        observed = json.loads(run_recorder([str(recorder),'probe',native['window']['title']],probe_root,min(deadline,time.monotonic()+60)))
        window_binding(native['window'],observed.get('window'))
    run = f"{platform}-{source['video_id']}-{dt.datetime.now(dt.timezone.utc):%Y%m%dT%H%M%SZ}-{secrets.token_hex(4)}"
    attempts = reserve_attempt(state,native,'record' if record_only else 'direct',run)
    root = safe_dir(state/run); OPERATION_ROOTS[:] = [child(state,r) for r in attempts.get('runs',[])]+([probe_root] if probe_root else [])
    write_json(root/'native-source.json', native)
    captions = []
    method = 'playback_recording' if record_only else 'direct_media'
    if not record_only:
        direct_deadline = min(deadline,time.monotonic()+600)
        metadata = direct_metadata(source['url'],root,direct_deadline)
        need(str(metadata.get('id')) == source['video_id'], 'extractor_identity_mismatch')
        duration = validate_duration(platform, metadata.get('duration'))
        need(abs(duration-native['duration']) <= 1, 'native_duration_mismatch')
        captions = caption_evidence(metadata,root,direct_deadline)
        v,a,downscale = legacy.choose_formats(metadata.get('formats',[]))
        for f,name in ((v,'video.mp4'), *([(a,'audio.m4a')] if a else [])):
            legacy.download(f['url'],root/name,root,direct_deadline)
        audio_input = root/('audio.m4a' if a else 'video.mp4')
        encoding = ['-c:v','copy'] if not downscale and str(v.get('vcodec','')).startswith(('h264','avc1')) else [
            '-c:v','libx264','-preset','veryfast','-crf','25','-vf','scale=1280:720:force_original_aspect_ratio=decrease:force_divisible_by=2']
        audio_encoding = ['-c:a','copy'] if str((a or v).get('acodec','')).startswith(('aac','mp4a','mp3','alac')) else ['-c:a','aac','-b:a','96k']
        legacy.command([legacy.TOOLS['ffmpeg'],'-nostdin','-v','error','-n','-i',str(root/'video.mp4'),'-i',str(audio_input),'-map','0:v:0','-map','1:a:0',
            *encoding,*audio_encoding,str(root/'source.mp4')],root,direct_deadline)
    else:
        duration = native['duration']; remaining_deadline(native['deadline_epoch'],time.time(),duration+120)
        recorder = PACKAGE/'tools/record'
        need(recorder.is_file() and sha(recorder) == CURRENT.get('recorder_sha256'), 'accepted_recorder_unavailable')
        need(recorder_accepted(state), 'recorder_live_acceptance_required')
        # Recorder always chooses Chrome's selected visible window. Its only
        # arguments come from this verified source and the fixed private root.
        context = {k:native[k] for k in ('request_id','canonical_url','deadline_epoch')}
        context.update(source_duration=duration,package=CURRENT['package'],recorder_sha256=CURRENT['recorder_sha256'])
        launched_epoch = time.time()
        output = run_recorder([str(recorder),str(root/'source.mp4'),str(recording_seconds(native)),native['window']['title'],
            json.dumps(native['window'],separators=(',',':')),json.dumps(context,separators=(',',':'))],root,min(deadline,time.monotonic()+duration+120))
        recording = json.loads(output); recording['platform'] = platform
        window_binding(native['window'],recording.get('window'))
        recording['handshake'] = recording_handshake(root,native,recording,launched_epoch,time.time())
        write_json(root/'recording.json', recording)
    media = legacy.probe(root/'source.mp4',root,deadline)
    actual = float(media['format']['duration'])
    if record_only:
        coverage = validate_recording(media,recording,duration)
        coverage['playback_confirmation'] = recording['handshake']
    else:
        need(abs(actual-duration) < 1, 'source_duration_mismatch')
        need({'audio','video'} <= {s.get('codec_type') for s in media['streams']}, 'missing_media_stream')
        coverage = {'coverage_status':'requires_speech_and_visual_review','source_duration':duration,'media_duration':actual}
    legacy.command([legacy.TOOLS['ffmpeg'],'-nostdin','-v','error','-xerror','-i',str(root/'source.mp4'),'-map','0:v:0','-map','0:a:0','-f','null','-'],root,deadline)
    legacy.command([legacy.TOOLS['ffmpeg'],'-nostdin','-v','error','-n','-i',str(root/'source.mp4'),'-map','0:a:0','-ac','1','-ar','16000','-c:a','flac',str(root/'speech.flac')],root,deadline)
    write_json(root/'acquisition.json',dict(native,method=method,coverage=coverage,captions=captions))
    (root/'run-marker.txt').write_text(secrets.token_hex(32)+'\n')
    digest = payload_manifest(root,{'version':VERSION,'platform':platform,'run_id':run,'video_id':source['video_id'],'duration':actual,
        'source_duration':duration,'evidence_type':'media','method':method,'marker_sha256':sha(root/'run-marker.txt'),'coverage':coverage})
    write_json(root/'source-commit.json',{'run_id':run,'manifest_sha256':digest})
    return publish_source(root,digest,deadline)


def persist_prepared(root, run, deadline=None):
    role = CURRENT['role']; mount_check(NAS,role)
    receipt = read_json(root/'prepared.json'); digest = receipt['prepared_manifest_sha256']
    dest = safe_dir(nas_run(run)/('prepared-'+role)/digest)
    # Include the source and all other preparations/partials on NAS in its cap.
    OPERATION_ROOTS[:] = [nas_run(run),root]
    cumulative_budget(nas_run(run)); legacy.space_check(root, legacy.MAX_BYTES)
    manifest = verify(root,digest)
    for name in [*[f['name'] for f in manifest['files']],'manifest.json','prepared.json']:
        p = child(root,name); legacy.publish_file(p,child(dest,name),sha(p),dest,deadline)
    verify(dest,digest)
    write_json(root/'publication.json',{'prepared_manifest_sha256':digest,'nas_path':str(dest),'status':'published'})


def preparation_state(state, digest):
    need(isinstance(digest,str) and re.fullmatch('[a-f0-9]{64}',digest), 'invalid_manifest_digest')
    return safe_dir(child(state,'prepared/'+processor_id()+'/'+digest))


def processor_id():
    return hashlib.sha256((sha(Path(__file__))+sha(Path(legacy.__file__))).encode()).hexdigest()[:24]


def processing_budget(duration):
    need(type(duration) in (int,float) and math.isfinite(duration) and 0 < duration <= 3720,'invalid_processing_duration')
    chunks = max(1,math.ceil((duration-240)/235)+1)
    sheets = math.ceil(math.ceil(duration)/16)
    return 600 + chunks*150 + sheets*30


def processing_command(args,root,deadline,limit=8*1024**2,combined=False):
    return _command(args,root,min(deadline,time.monotonic()+600),limit,combined)


def prepare_sheet_pass(source,root,duration,digest,deadline,visual_duration):
    """Sample the full display timeline before tiling, including held pictures.

    A per-sheet input EOF can omit static VFR intervals or substitute a future
    frame after seeking. One bounded pass avoids both without new source media.
    """
    count = max(1,math.ceil(visual_duration))
    names = [f'sheet-{i+1:03d}.jpg' for i in range(math.ceil(count/16))]
    checkpoint = root/'visual-progress.json'; progress = read_json(checkpoint)
    need(progress.get('source_manifest_sha256') == digest and progress.get('duration') == duration,'visual_progress_source_mismatch')
    need(set(progress['completed']) <= {*names,'ending.jpg'},'unexpected_visual_progress_file')
    def retained_sheet_matches(name,expected):
        partial_root = child(root,'partials')
        if not partial_root.exists():return False
        need(partial_root.is_dir(),'invalid_visual_partials')
        # The generator preserves sheets one level below this same run only.
        # Never search other runs, accept absent bytes or weaken a root mismatch.
        for number,folder in enumerate(partial_root.iterdir(),1):
            need(number <= 2000,'visual_partial_inventory_limit')
            child(root,str(folder.relative_to(root)))
            if folder.is_dir():
                candidate = child(folder,name)
                if candidate.is_file() and sha(candidate) == expected:return True
        return False
    all_at_root = True
    for i,name in enumerate(names):
        if name in progress['completed']:
            path = child(root,name)
            if path.exists():need(path.is_file() and sha(path) == progress['completed'][name],'visual_progress_hash_mismatch')
            else:
                need(retained_sheet_matches(name,progress['completed'][name]),'visual_progress_hash_mismatch')
                all_at_root = False
            need(progress['sample_seconds'].get(name) == list(range(i*16,min(i*16+16,count))),'visual_sample_time_mismatch')
    if all_at_root and all(name in progress['completed'] for name in names): return
    # Preserve every old/incomplete pass, including valid members of a pass that
    # was interrupted before its complete timestamp proof. Never overwrite it.
    existing = list(root.glob('sheet-*.jpg'))
    for path in existing:
        need(path.name in names and path.is_file() and not path.is_symlink(),'unexpected_visual_output')
    # Commit the uncommitted-pass state before moving any sheet. Interruption
    # between moves must not leave a completed entry pointing at a moved file.
    for name in names:
        progress['completed'].pop(name,None); progress['sample_seconds'].pop(name,None)
    write_json(checkpoint,progress)
    if existing:
        partial = safe_dir(root/'partials'/secrets.token_hex(4))
        for path in existing:path.rename(partial/path.name)
    crop = progress['crop']
    filters = 'fps=1:start_time=0:round=up,showinfo,'+('crop='+crop+',' if crop else '')+'scale=320:-2,tile=4x4:nb_frames=16:padding=2:margin=2'
    observed = legacy.command([legacy.TOOLS['ffmpeg'],'-nostdin','-hide_banner','-v','info','-n','-i',str(source),'-map','0:v:0','-an',
        '-vf',filters,'-fps_mode','passthrough','-frames:v',str(len(names)),'-q:v','3','-start_number','1',str(root/'sheet-%03d.jpg')],root,deadline,combined=True)
    times = [float(t) for t in re.findall(rb'n:\s*\d+\s+pts:\s*-?\d+\s+pts_time:([0-9.+-]+)',observed)]
    need(times == list(range(count)) and {p.name for p in root.glob('sheet-*.jpg')} == set(names),'visual_sample_count_or_time_mismatch')
    for i,name in enumerate(names):
        path = child(root,name); need(path.is_file() and path.stat().st_size > 0,'missing_visual_output')
        progress['completed'][name] = sha(path)
        progress['sample_seconds'][name] = times[i*16:min(i*16+16,count)]
        write_json(checkpoint,progress)


def prepare_visuals(source,root,duration,digest,deadline):
    """Preserve v1 sheets, but select the actual final decoded video frame.

    Seeking duration minus 0.1 seconds can miss low-frame-rate endings. Commit
    the observed final PTS before the v1 resumable sheet preparer checks it.
    """
    checkpoint = root/'visual-progress.json'; stage = root/'visual-coverage.json'
    if not stage.exists():
        if checkpoint.exists():
            progress = read_json(checkpoint)
            need(progress.get('source_manifest_sha256') == digest and progress.get('duration') == duration,'visual_progress_source_mismatch')
        else:
            detection = legacy.command([legacy.TOOLS['ffmpeg'],'-nostdin','-hide_banner','-i',str(source),'-vf','cropdetect=24:2','-t','5','-f','null','-'],root,deadline,combined=True)
            candidates = re.findall(rb'crop=(\d+:\d+:\d+:\d+)',detection)
            progress = {'source_manifest_sha256':digest,'duration':duration,'crop':candidates[-1].decode() if candidates else None,'completed':{},'sample_seconds':{}}
        if 'ending.jpg' not in progress['completed']:
            media = legacy.probe(source,root,deadline); video = next(s for s in media['streams'] if s['codec_type'] == 'video')
            visual_duration = min(duration,float(video.get('duration') or media['format']['duration']))
            frames = json.loads(legacy.command([legacy.TOOLS['ffprobe'],'-v','error','-select_streams','v:0','-read_intervals',str(max(0,visual_duration-60))+'%',
                '-show_frames','-show_entries','frame=best_effort_timestamp_time','-of','json',str(source)],root,deadline))
            times = [float(f['best_effort_timestamp_time']) for f in frames.get('frames',[]) if 'best_effort_timestamp_time' in f]
            need(bool(times) and all(math.isfinite(t) and 0 <= t <= visual_duration for t in times),'invalid_final_frame_time')
            final_time = max(times); ending = root/'ending.jpg'
            if ending.exists():
                partial = safe_dir(root/'partials'/secrets.token_hex(4)); ending.rename(partial/'ending.jpg')
            legacy.command([legacy.TOOLS['ffmpeg'],'-nostdin','-v','error','-n','-accurate_seek','-ss',str(final_time),'-i',str(source),
                '-vf','format=yuvj420p','-frames:v','1',str(ending)],root,deadline)
            need(ending.is_file() and ending.stat().st_size > 0,'missing_visual_ending')
            progress['completed']['ending.jpg'] = sha(ending)
            write_json(root/'ending-time.json',{'source_manifest_sha256':digest,'observed_video_frame_second':final_time,'visual_duration':visual_duration})
            write_json(checkpoint,progress)
    final = read_json(root/'ending-time.json')
    if not stage.exists():
        prepare_sheet_pass(source,root,duration,digest,deadline,final['visual_duration'])
    coverage = legacy.prepare_visuals(source,root,duration,digest,deadline)
    coverage['sampling_policy'] = 'full_stream_display_timeline_resampling_with_possible_held_images'
    need(final.get('source_manifest_sha256') == digest,'ending_source_mismatch')
    coverage['ending_observed_second'] = final['observed_video_frame_second']
    write_json(stage,coverage); return coverage


def classify_transcript(response,offset,length):
    """Retain structurally valid out-of-chunk intervals as unsupported claims.

    Raw response files are never rewritten. Words from such a segment are not
    clipped, redistributed or used as normalized source speech.
    """
    need(type(offset) in (int,float) and math.isfinite(offset) and offset >= 0 and
         type(length) in (int,float) and math.isfinite(length) and 0 < length <= 240.15,'invalid_audio_chunk_time_map')
    need(isinstance(response,dict),'invalid_transcription_response')
    text = response.get('text'); raw = response.get('segments')
    if 'text' in response and (text is None or isinstance(text,str) and not text.strip()) and (raw is None or raw == []):return [],[]
    need(isinstance(text,str) and bool(text.strip()),'empty_transcription')
    need(isinstance(raw,list) and bool(raw),'missing_transcript_segments')
    words = []; anomalies = []
    for index,segment in enumerate(raw):
        need(isinstance(segment,dict) and {'start','end','text'} <= set(segment),'malformed_transcript_segment')
        a,b = segment['start'],segment['end']
        need(type(a) in (int,float) and type(b) in (int,float) and math.isfinite(a) and math.isfinite(b) and a <= b,'invalid_segment_time')
        need(isinstance(segment['text'],str),'invalid_segment_text')
        if a < 0 or b > length:
            first,last = max(0,a),min(length,b)
            anomalies.append({'raw_segment_index':index,'raw_segment':{k:segment[k] for k in ('start','end','text')},
                'submitted_audio_chunk_duration':length,'submitted_audio_offset_in_media':offset,
                'unverified_audio_range':{'start':offset+first,'end':offset+last} if first < last else None,
                'reason':'provider_timestamp_outside_submitted_audio_chunk'})
        else:words.append({'start':a+offset,'end':b+offset,'text':segment['text'].strip(),'raw_segment_index':index})
    return words,anomalies


def validate_episode_anomalies(anomalies,start,end,video_duration):
    """Check only anomaly intersection, never substitute for source review."""
    need(all(type(t) in (int,float) and math.isfinite(t) for t in (start,end,video_duration)) and
         0 <= start < end <= video_duration and isinstance(anomalies,list),'invalid_source_episode_review_range')
    outside = []
    for anomaly in anomalies:
        need(isinstance(anomaly,dict) and 'unverified_audio_range' in anomaly,'invalid_provider_anomaly_record')
        span = anomaly['unverified_audio_range']
        if span is None:continue
        need(isinstance(span,dict) and set(span) == {'start','end'} and
             all(type(span[k]) in (int,float) and math.isfinite(span[k]) for k in span) and
             0 <= span['start'] < span['end'],'invalid_provider_anomaly_record')
        need(max(start,span['start']) >= min(end,span['end']),'source_episode_has_unresolved_provider_anomaly')
        outside.append(span)
    return {'episode_start':start,'episode_end':end,'anomaly_intersection_check':'none_intersect',
        'unverified_ranges_outside_episode':outside,'source_speech_and_boundary_review_still_required':True}


def prepare_source(run,digest,local,manifest):
    src = nas_run(run); root = safe_dir(local/run)
    need(sha(src/'run-marker.txt') == manifest.get('marker_sha256'),'marker_mismatch')
    duration = manifest['duration']; deadline = time.monotonic()+processing_budget(duration)
    receipt = root/'prepared.json'
    if receipt.exists():
        result = read_json(receipt)
        need(result.get('source_manifest_sha256') == digest and result.get('processor_id') == processor_id(),'prepared_source_mismatch')
        prepared = verify(root,result['prepared_manifest_sha256'])
        need(prepared.get('source_manifest_sha256') == digest and prepared.get('processor_id') == processor_id(),'prepared_source_mismatch')
        need({k:v for k,v in result.items() if k != 'prepared_manifest_sha256'} == read_json(root/'prepared-claims.json'),'prepared_receipt_mismatch')
        persist_prepared(root,run,deadline); return dict(result,reused=True)
    legacy.command = processing_command
    timing = manifest['_observed_timing']
    write_json(root/'source-timing.json',dict(timing,source_manifest_sha256=digest))
    audio_duration = timing['speech_duration']; audio_start = timing['audio_start_second']
    all_segments = []; chunks = []; anomalies = []
    for i,(offset,length) in enumerate(legacy.chunk_schedule(audio_duration)):
        audio = root/f'chunk-{i:03d}.flac'; response = root/f'transcript-{i:03d}.json'
        legacy.ensure_audio_chunk(src/'speech.flac',audio,offset,length,root,deadline,response.exists())
        submitted_duration = float(legacy.probe(audio,root,deadline)['format']['duration'])
        if not response.exists():
            need(time.monotonic()+90 <= deadline,'processing_deadline_before_speech')
            r = legacy.transcribe(audio); classify_transcript(r,offset+audio_start,submitted_duration)
            write_json(response,{'source_manifest_sha256':digest,'audio_sha256':sha(audio),'response':r})
        r = read_json(response)
        need(r.get('source_manifest_sha256') == digest and r.get('audio_sha256') == sha(audio),'transcript_source_mismatch')
        current,unsupported = classify_transcript(r['response'],offset+audio_start,submitted_duration)
        all_segments.extend(dict(s,chunk=i) for s in current)
        anomalies.extend(dict(a,chunk=i,response=response.name,response_sha256=sha(response)) for a in unsupported)
        status = 'provider_timestamp_anomalies_unverified' if unsupported else 'words_returned' if current else 'empty_audio_result_unverified'
        chunks.append({'audio_offset':offset,'offset':offset+audio_start,'duration':submitted_duration,'scheduled_duration':length,'response':response.name,
            'transcript_status':status,'provider_timestamp_anomaly_count':len(unsupported)})
    gaps = [{'start':c['offset'],'end':c['offset']+c['duration'],'reason':'No words returned; source sound remains unverified',
             'neighbor_word_segments':[s for s in all_segments if s['start'] < c['offset']+c['duration'] and s['end'] > c['offset']]} for c in chunks if c['transcript_status'] == 'empty_audio_result_unverified']
    gaps.extend(dict(a['unverified_audio_range'],reason=a['reason'],chunk=a['chunk'],raw_segment_index=a['raw_segment_index'])
        for a in anomalies if a['unverified_audio_range'] is not None)
    gaps.extend(timing['audio_absent_ranges'])
    basis = 'recording_seconds_including_padding' if manifest['method'] == 'playback_recording' else 'source_media_seconds'
    write_json(root/'timeline.json',{'source_manifest_sha256':digest,'processor_id':processor_id(),'timeline_basis':basis,'observed_timing':timing,'chunks':chunks,'segments':all_segments,'audio_gaps':gaps,'provider_timestamp_anomalies':anomalies,
        'overlap_policy':'Five-second overlaps retained with chunk labels; reconcile repeated boundary speech during review'})
    write_json(root/'provider-timestamp-anomalies.json',{'source_manifest_sha256':digest,'processor_id':processor_id(),
        'anomalies':anomalies,'policy':'Whole unsupported segments excluded from normalized words; raw responses retained; no silence inference or automatic retry'})
    normalized = '\n'.join(f"[{s['start']:.2f} - {s['end']:.2f}] {s['text']}" for s in all_segments)+'\n'
    if anomalies:normalized += '\nUnverified provider timestamps: unsupported segments excluded. See provider-timestamp-anomalies.json. This is not a complete-transcription claim.\n'
    (root/'transcript.txt').write_text(normalized)
    coverage = prepare_visuals(src/'source.mp4',root,duration,digest,deadline)
    coverage['unpictured_audio_tail_seconds'] = max(0,audio_start+audio_duration-coverage['visual_duration'])
    write_json(root/'visual-coverage.json',coverage)
    result = {'status':'prepared_for_assistant_review','evidence_type':'media','complete_media_available':True,
        'run_id':run,'source_manifest_sha256':digest,'processor_id':processor_id(),'processing_host':CURRENT['role'],
        'source_method':manifest['method'],'timeline_basis':basis,'source_time_map_status':'requires_actual_boundary_review' if manifest['method'] == 'playback_recording' else 'direct_media',
        'transcript':str(root/'transcript.txt'),'visual_coverage':str(root/'visual-coverage.json'),'sheets':coverage['sheets'],'ending':coverage['ending'],
        'duration':duration,'source_duration':manifest['source_duration'],'media_duration':timing['media_duration'],'video_duration':timing['video_duration'],
        'speech_duration':audio_duration,'audio_start_second':audio_start,'chunks':len(chunks),'segments':len(all_segments),'audio_gaps':gaps,'provider_timestamp_anomalies':anomalies,
        'transcription_coverage_status':'unverified_provider_timestamp_anomalies' if anomalies else 'requires_source_speech_review',
        'content_review_status':'pending_assistant_speech_visual_and_boundary_review',
        'source_capture_review':manifest.get('coverage',{}),'source_recovery':manifest.get('recovery'),
        'source_recovery_status':'pending_content_verification' if manifest.get('recovery') else None}
    write_json(root/'prepared-claims.json',result)
    pd = payload_manifest(root,{'source_manifest_sha256':digest,'run_id':run})
    result = dict(result,prepared_manifest_sha256=pd); write_json(receipt,result)
    persist_prepared(root,run,deadline); return result


def process(run,digest,state):
    platform, identity = run_identity(run); mount_check(NAS,CURRENT['role'])
    manifest = verify(nas_run(run),digest)
    need(manifest.get('platform') == platform and manifest.get('video_id') == identity and manifest.get('run_id') == run, 'source_binding_mismatch')
    if manifest.get('evidence_type') == 'caption_visual':
        need(platform == 'youtube','caption_visual_requires_youtube')
        need({f['name'] for f in manifest['files']} == set(CONTENT_FILES)|{'evidence.json','run-marker.txt'},'unexpected_content_manifest_payload')
        return read_content_bundle(run,digest,manifest)
    need(platform == 'facebook' and manifest.get('method') == 'direct_media' and manifest.get('evidence_type','media') == 'media',
         'unsupported_media_method_content_first_workflow')
    names = {f['name'] for f in manifest['files']}
    need({'source.mp4','speech.flac','run-marker.txt','acquisition.json','native-source.json'} <= names, 'incomplete_source_manifest')
    validate_duration(platform,manifest.get('source_duration'))
    local = preparation_state(state,digest); root = safe_dir(local/run)
    OPERATION_ROOTS[:] = [nas_run(run)]
    if not (root/'prepared.json').exists():
        deadline = time.monotonic()+600
        media = legacy.probe(nas_run(run)/'source.mp4',root,deadline)
        speech = legacy.probe(nas_run(run)/'speech.flac',root,deadline)
        audio = next((s for s in media.get('streams',[]) if s.get('codec_type') == 'audio'),None)
        video = next((s for s in media.get('streams',[]) if s.get('codec_type') == 'video'),None)
        need(audio is not None and video is not None,'missing_media_stream')
        try: video_duration = float(video.get('duration') or 0)
        except (ValueError,TypeError): video_duration = 0
        if not math.isfinite(video_duration) or video_duration <= 0: video_duration = None
        start = max(0,float(audio.get('start_time') or 0)-float(media['format'].get('start_time') or 0))
        manifest['_observed_timing'] = validate_source_timing(manifest,float(media['format']['duration']),float(speech['format']['duration']),
            audio_stream_duration=float(audio.get('duration') or 0),audio_start=start,video_duration=video_duration)
    OPERATION_ROOTS[:] = [nas_run(run)]
    # Legacy full-audio chunks, gap handling and observed visual timestamps are
    # reused. The package/source hashes isolate preparations across versions.
    result = prepare_source(run,digest,local,manifest)
    return dict(result,processor_id=processor_id(),processing_host=CURRENT['role'],source_method=manifest['method'],
                timeline_basis='recording_seconds_including_padding' if manifest['method'] == 'playback_recording' else 'source_media_seconds',
                source_time_map_status='requires_actual_boundary_review' if manifest['method'] == 'playback_recording' else 'direct_media',
                content_review_status='pending_assistant_speech_visual_and_boundary_review')


def validate_source_timing(manifest,media_duration,speech_duration,audio_stream_duration=None,audio_start=0,video_duration=None):
    source = validate_duration(manifest['platform'],manifest.get('source_duration'))
    duration = manifest.get('duration')
    need(type(duration) in (int,float) and math.isfinite(duration) and duration > 0,'invalid_media_duration')
    if manifest.get('method') == 'direct_media': need(abs(duration-source) < 1,'source_duration_mismatch')
    else:
        need(manifest.get('method') == 'playback_recording' and source <= duration <= source+120,'recording_duration_or_padding_mismatch')
    need(math.isfinite(media_duration) and abs(media_duration-duration) < .25,'actual_media_duration_mismatch')
    need(type(audio_stream_duration) in (int,float) and math.isfinite(audio_stream_duration) and audio_stream_duration > 0,'audio_stream_duration_unavailable')
    need(math.isfinite(speech_duration) and speech_duration > 0 and abs(speech_duration-audio_stream_duration) < .25,'actual_speech_duration_mismatch')
    need(math.isfinite(audio_start) and audio_start >= 0 and audio_start+speech_duration <= media_duration+.25,'invalid_audio_time_map')
    gaps = []
    if audio_start > .001:gaps.append({'start':0,'end':audio_start,'reason':'no_audio_stream_in_media_range'})
    if audio_start+speech_duration < media_duration-.001:gaps.append({'start':audio_start+speech_duration,'end':media_duration,'reason':'no_audio_stream_in_media_range'})
    return {'media_duration':media_duration,'video_duration':video_duration,'speech_duration':speech_duration,
            'audio_stream_duration':audio_stream_duration,'audio_start_second':audio_start,'audio_absent_ranges':gaps}


def read_status(run,digest,state):
    mount_check(NAS,CURRENT['role']); manifest = verify(nas_run(run),digest)
    platform,identity = run_identity(run)
    need(manifest.get('platform') == platform and manifest.get('video_id') == identity and manifest.get('run_id') == run,'source_binding_mismatch')
    if manifest.get('evidence_type') == 'caption_visual':
        need(platform == 'youtube' and {f['name'] for f in manifest['files']} == set(CONTENT_FILES)|{'evidence.json','run-marker.txt'},'unexpected_content_manifest_payload')
        return read_content_bundle(run,digest,manifest)
    if CURRENT['role'] == 'mini':
        result = read_json(child(state,run)/'completion.json')
        need(result['manifest_sha256'] == digest, 'completion_digest_mismatch')
        verify(child(state,run),digest)
    else:
        root = child(preparation_state(state,digest),run)
        result = read_json(root/'prepared.json'); publication = read_json(root/'publication.json')
        pd = result['prepared_manifest_sha256']; prepared = verify(root,pd)
        need(result.get('source_manifest_sha256') == digest and prepared.get('source_manifest_sha256') == digest and
             result.get('processor_id') == processor_id() and prepared.get('processor_id') == processor_id(),'prepared_source_mismatch')
        need({k:v for k,v in result.items() if k != 'prepared_manifest_sha256'} == read_json(root/'prepared-claims.json'),'prepared_receipt_mismatch')
        dest = nas_run(run)/('prepared-'+CURRENT['role'])/pd
        need(publication == {'prepared_manifest_sha256':pd,'nas_path':str(dest),'status':'published'}, 'publication_pending_or_mismatch')
        verify(dest,pd); need(sha(dest/'prepared.json') == sha(root/'prepared.json'),'prepared_receipt_mismatch')
    return dict(result,nas_publication_verified=True)


def inventory(state,role):
    local = []; preflights = []
    candidates = list(state.iterdir())
    prep = state/'prepared'
    if prep.exists():
        need(not prep.is_symlink(),'symlink_preparation_root')
        for processor in prep.iterdir():
            need(not processor.is_symlink() and processor.is_dir() and re.fullmatch('[a-f0-9]{24}',processor.name),'invalid_preparation_root')
            for digest in processor.iterdir():
                need(not digest.is_symlink() and digest.is_dir() and re.fullmatch('[a-f0-9]{64}',digest.name),'invalid_preparation_root')
                candidates.extend(digest.iterdir())
    for p in candidates:
        if re.fullmatch(r'preflight-[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}',p.name):
            need(p.is_dir() and not p.is_symlink(),'invalid_preflight_directory')
            preflights.append({'path':str(p),'bytes':size(p)})
            need(len(preflights) <= 2000,'preflight_inventory_limit')
        if not re.fullmatch(RUN_RE,p.name): continue
        need(not p.is_symlink() and p.is_dir(),'symlink_or_invalid_inventory_run')
        local.append({'run_id':p.name,'path':str(p),'bytes':size(p),'modified_utc':dt.datetime.fromtimestamp(p.stat().st_mtime,dt.timezone.utc).isoformat()})
        need(len(local) <= 2000,'inventory_run_limit')
    report = {'status':'inventory','scope':'video-v2 source runs and recorder preflights','local_runs':local,'local_preflights':preflights,'nas_partials':[],'failure_receipts':[],
              'byte_budget_scope':BYTE_BUDGET_SCOPE,'retained_archive_bytes':{'local_runs_and_preflights':sum(i['bytes'] for i in [*local,*preflights]),'nas_v2_runs':None,'other_hosts_private_copies':'not_measured'},
              'retention':'No automatic deletion or stale-lock clearing; cleanup requires exact-path authorization'}
    def failures(root):
        for p in root.rglob('failure-*.json'):
            child(root,str(p.relative_to(root)))
            if p.is_file(): report['failure_receipts'].append({'path':str(p),'bytes':p.stat().st_size})
            need(len(report['failure_receipts']) <= 2000,'failure_inventory_limit')
    for item in [*local,*preflights]: failures(Path(item['path']))
    try:
        mount_check(NAS,role)
        examined = 0; nas_archive_bytes = 0
        for platform in LIMITS:
            base = child(NAS,platform)
            if not base.exists(): continue
            for identity in base.iterdir():
                examined += 1; need(examined <= 4000,'nas_inventory_limit')
                pattern = '[0-9]{1,24}' if platform == 'facebook' else '[A-Za-z0-9_-]{11}'
                if not re.fullmatch(pattern,identity.name): continue
                need(identity.is_dir() and not identity.is_symlink(),'invalid_nas_identity_directory')
                for destination in identity.iterdir():
                    examined += 1; need(examined <= 4000,'nas_inventory_limit')
                    if not re.fullmatch(RUN_RE,destination.name): continue
                    need(run_identity(destination.name) == (platform,identity.name),'nas_run_identity_mismatch')
                    need(not destination.is_symlink() and destination.is_dir(),'symlink_nas_run')
                    failures(destination); nas_archive_bytes += size(destination)
                    for p in destination.rglob('*.partial-*'):
                        child(destination,str(p.relative_to(destination)))
                        if p.is_file(): report['nas_partials'].append({'path':str(p),'bytes':p.stat().st_size})
                        need(len(report['nas_partials']) <= 2000,'nas_partial_inventory_limit')
        report['nas_status'] = 'mounted_checked'
        report['retained_archive_bytes']['nas_v2_runs'] = nas_archive_bytes
    except (Blocked,OSError) as error:
        report['nas_status'] = str(error) if isinstance(error,Blocked) else type(error).__name__
    return report


def arguments(argv=None):
    p = argparse.ArgumentParser(description=__doc__); sub = p.add_subparsers(dest='action',required=True)
    for action in ('version','inventory'): sub.add_parser(action)
    for action in ('capture','record'): sub.add_parser(action).add_argument('url')
    a = sub.add_parser('publish-content');a.add_argument('url');a.add_argument('request_id');a.add_argument('evidence_sha256')
    for action in ('process','status','save-digest'):
        a = sub.add_parser(action); a.add_argument('run_id'); a.add_argument('manifest_sha256')
    a = sub.add_parser('recover-recording'); a.add_argument('run_id'); a.add_argument('media_sha256'); a.add_argument('context_sha256')
    return p.parse_args(argv)


def main():
    os.umask(0o077)
    try:
        a = arguments(); c = install_config(); role = c['role']
        platform = parse_source(a.url)['platform'] if hasattr(a,'url') else run_identity(a.run_id)[0] if hasattr(a,'run_id') else 'facebook'
        state = safe_dir(legacy.own_home()/'.local/state/k2b/video-v2'); configure(c,platform,state)
        seconds = LIMITS[platform][2] if a.action in ('capture','record') else processing_budget(LIMITS[platform][0]+120) if a.action == 'process' else 600
        # The v1 owning worker lock is shared, so v1/v2 acquisitions serialize.
        lock_root = safe_dir(legacy.own_home()/'.local/state/k2b/reels-v1') if role == 'mini' else state
        with legacy.operation_timeout(seconds), (contextlib.nullcontext() if a.action in ('version','inventory','status') else legacy.lock(lock_root)):
            if a.action == 'version': result = {'version':VERSION,'role':role,'processor_id':processor_id(),'limits':LIMITS,'byte_budget_scope':BYTE_BUDGET_SCOPE,
                'workflow_contract':CONTENT_CONTRACT,'youtube_method':'native_captions_and_visual_checks','facebook_method':'direct_media',
                'recording_default_enabled':False,'recorder_status':'experimental_not_activated'}
            elif a.action == 'inventory': result = inventory(state,role)
            elif a.action == 'capture':
                need(role == 'mini','capture_requires_mini')
                need(platform == 'facebook','youtube_requires_native_caption_visual_evidence')
                result = capture(a.url,state)
            elif a.action == 'publish-content':
                need(role == 'mini','content_publication_requires_mini')
                result = publish_content(a.url,a.request_id,a.evidence_sha256,state)
            elif a.action == 'record':raise Blocked('recording_experimental_not_in_default_workflow')
            elif a.action == 'recover-recording':
                raise Blocked('recording_recovery_experimental_not_activated')
            elif a.action == 'process':
                need(role in ('home','sjm'),'process_requires_knowledge_host'); result = process(a.run_id,a.manifest_sha256,state)
            elif a.action == 'status': result = read_status(a.run_id,a.manifest_sha256,state)
            else:
                need(role in ('home','sjm'),'save_requires_knowledge_host')
                need(platform == 'facebook','caption_paraphrase_save_requires_vault_writer')
                # Digest storage reuses the writer's provenance and hash checks.
                result = legacy.save_digest(a.run_id,a.manifest_sha256,preparation_state(state,a.manifest_sha256))
        print(json.dumps(result,ensure_ascii=False)); return 0
    except Exception as e:
        print(json.dumps({'status':'blocked','reason':str(e) if isinstance(e,Blocked) else type(e).__name__}),flush=True); return 2
