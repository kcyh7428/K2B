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


def install_config():
    c = read_json(PACKAGE/'config.json')
    need(c.get('role') in ('home','sjm','mini'), 'invalid_install_role')
    validate_config_identity(c,PACKAGE.name)
    role = c['role']; expected = {'home':'keithmbpm2','sjm':'keithcheung','mini':'fastshower'}[role]
    need(pwd.getpwuid(os.getuid()).pw_name == expected, 'wrong_host_user')
    need(set(c.get('files', {})) == {'video-extract.py','lib/video_extract.py','lib/video_legacy.py','media/record.swift'}, 'invalid_package_file_set')
    for name, digest in c['files'].items():
        p = child(PACKAGE, name); s = p.stat()
        need(s.st_uid == os.getuid() and not s.st_mode & 0o222 and sha(p) == digest, 'installed_code_changed')
    if role == 'mini':
        for name, field in (('yt-dlp','downloader_sha256'),('record','recorder_sha256')):
            artifact = child(PACKAGE,'tools/'+name)
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
    exists = path.exists()
    receipt = read_json(path) if exists else dict(binding,attempted=[])
    need(all(receipt.get(k) == v for k,v in binding.items()),'immutable_request_binding_changed')
    if not exists:write_json(path,receipt)
    return path,receipt


def reserve_attempt(state, native, method, run=None):
    path,receipt = request_receipt(state,native)
    need(method in ('direct','record') and method not in receipt['attempted'], 'attempt_already_dispatched_collect_existing')
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
    need(type(observed) in (int,float) and math.isfinite(observed) and epoch <= observed <= min(epoch+30,completed_epoch) and
         type(clock) in (int,float) and math.isfinite(clock) and 0 < clock <= min(native['duration'],observed-epoch+1),
         'native_playback_start_missing_or_late')
    return {'ready_marker_sha256':digest,'ready_epoch':epoch,'native_start_observed_epoch':observed,
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
            result = legacy.transfer(old_root,digest,deadline)
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
    if record_only: coverage = validate_recording(media,recording,duration)
    else:
        need(abs(actual-duration) < 1, 'source_duration_mismatch')
        need({'audio','video'} <= {s.get('codec_type') for s in media['streams']}, 'missing_media_stream')
        coverage = {'coverage_status':'requires_speech_and_visual_review','source_duration':duration,'media_duration':actual}
    legacy.command([legacy.TOOLS['ffmpeg'],'-nostdin','-v','error','-xerror','-i',str(root/'source.mp4'),'-map','0:v:0','-map','0:a:0','-f','null','-'],root,deadline)
    legacy.command([legacy.TOOLS['ffmpeg'],'-nostdin','-v','error','-n','-i',str(root/'source.mp4'),'-map','0:a:0','-ac','1','-ar','16000','-c:a','flac',str(root/'speech.flac')],root,deadline)
    write_json(root/'acquisition.json',dict(native,method=method,coverage=coverage,captions=captions))
    (root/'run-marker.txt').write_text(secrets.token_hex(32)+'\n')
    digest = payload_manifest(root,{'version':VERSION,'platform':platform,'run_id':run,'video_id':source['video_id'],'duration':actual,
        'source_duration':duration,'method':method,'marker_sha256':sha(root/'run-marker.txt'),'coverage':coverage})
    write_json(root/'source-commit.json',{'run_id':run,'manifest_sha256':digest})
    return legacy.transfer(root,digest,deadline)


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
    coverage = legacy.prepare_visuals(source,root,duration,digest,deadline)
    final = read_json(root/'ending-time.json')
    need(final.get('source_manifest_sha256') == digest,'ending_source_mismatch')
    coverage['ending_observed_second'] = final['observed_video_frame_second']
    write_json(stage,coverage); return coverage


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
    all_segments = []; chunks = []
    for i,(offset,length) in enumerate(legacy.chunk_schedule(audio_duration)):
        audio = root/f'chunk-{i:03d}.flac'; response = root/f'transcript-{i:03d}.json'
        legacy.ensure_audio_chunk(src/'speech.flac',audio,offset,length,root,deadline,response.exists())
        if not response.exists():
            need(time.monotonic()+90 <= deadline,'processing_deadline_before_speech')
            r = legacy.transcribe(audio); legacy.segments(r,offset,allow_empty=True)
            write_json(response,{'source_manifest_sha256':digest,'audio_sha256':sha(audio),'response':r})
        r = read_json(response)
        need(r.get('source_manifest_sha256') == digest and r.get('audio_sha256') == sha(audio),'transcript_source_mismatch')
        current = legacy.segments(r['response'],offset+audio_start,allow_empty=True)
        all_segments.extend(dict(s,chunk=i) for s in current)
        chunks.append({'audio_offset':offset,'offset':offset+audio_start,'duration':length,'response':response.name,'transcript_status':'words_returned' if current else 'empty_audio_result_unverified'})
    gaps = [{'start':c['offset'],'end':c['offset']+c['duration'],'reason':'No words returned; source sound remains unverified',
             'neighbor_word_segments':[s for s in all_segments if s['start'] < c['offset']+c['duration'] and s['end'] > c['offset']]} for c in chunks if c['transcript_status'] != 'words_returned']
    gaps.extend(timing['audio_absent_ranges'])
    basis = 'recording_seconds_including_padding' if manifest['method'] == 'playback_recording' else 'source_media_seconds'
    write_json(root/'timeline.json',{'source_manifest_sha256':digest,'processor_id':processor_id(),'timeline_basis':basis,'observed_timing':timing,'chunks':chunks,'segments':all_segments,'audio_gaps':gaps,
        'overlap_policy':'Five-second overlaps retained with chunk labels; reconcile repeated boundary speech during review'})
    (root/'transcript.txt').write_text('\n'.join(f"[{s['start']:.2f} - {s['end']:.2f}] {s['text']}" for s in all_segments)+'\n')
    coverage = prepare_visuals(src/'source.mp4',root,duration,digest,deadline)
    coverage['unpictured_audio_tail_seconds'] = max(0,audio_start+audio_duration-coverage['visual_duration'])
    write_json(root/'visual-coverage.json',coverage)
    result = {'status':'prepared_for_assistant_review','run_id':run,'source_manifest_sha256':digest,'processor_id':processor_id(),'processing_host':CURRENT['role'],
        'source_method':manifest['method'],'timeline_basis':basis,'source_time_map_status':'requires_actual_boundary_review' if manifest['method'] == 'playback_recording' else 'direct_media',
        'transcript':str(root/'transcript.txt'),'visual_coverage':str(root/'visual-coverage.json'),'sheets':coverage['sheets'],'ending':coverage['ending'],
        'duration':duration,'source_duration':manifest['source_duration'],'media_duration':timing['media_duration'],'video_duration':timing['video_duration'],
        'speech_duration':audio_duration,'audio_start_second':audio_start,'chunks':len(chunks),'segments':len(all_segments),'audio_gaps':gaps,'content_review_status':'pending_assistant_speech_visual_and_boundary_review'}
    write_json(root/'prepared-claims.json',result)
    pd = payload_manifest(root,{'source_manifest_sha256':digest,'run_id':run})
    result = dict(result,prepared_manifest_sha256=pd); write_json(receipt,result)
    persist_prepared(root,run,deadline); return result


def process(run,digest,state):
    platform, identity = run_identity(run); mount_check(NAS,CURRENT['role'])
    manifest = verify(nas_run(run),digest)
    need(manifest.get('platform') == platform and manifest.get('video_id') == identity and manifest.get('run_id') == run, 'source_binding_mismatch')
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
    mount_check(NAS,CURRENT['role']); verify(nas_run(run),digest)
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
    for action in ('process','status','save-digest'):
        a = sub.add_parser(action); a.add_argument('run_id'); a.add_argument('manifest_sha256')
    return p.parse_args(argv)


def main():
    os.umask(0o077)
    try:
        a = arguments(); c = install_config(); role = c['role']
        platform = parse_source(a.url)['platform'] if a.action in ('capture','record') else run_identity(a.run_id)[0] if hasattr(a,'run_id') else 'facebook'
        state = safe_dir(legacy.own_home()/'.local/state/k2b/video-v2'); configure(c,platform,state)
        seconds = LIMITS[platform][2] if a.action in ('capture','record') else processing_budget(LIMITS[platform][0]+120) if a.action == 'process' else 600
        # The v1 owning worker lock is shared, so v1/v2 acquisitions serialize.
        lock_root = safe_dir(legacy.own_home()/'.local/state/k2b/reels-v1') if role == 'mini' else state
        with legacy.operation_timeout(seconds), (contextlib.nullcontext() if a.action in ('version','inventory','status') else legacy.lock(lock_root)):
            if a.action == 'version': result = {'version':VERSION,'role':role,'processor_id':processor_id(),'limits':LIMITS,'byte_budget_scope':BYTE_BUDGET_SCOPE,'recorder_accepted':recorder_accepted(state) if role == 'mini' else False}
            elif a.action == 'inventory': result = inventory(state,role)
            elif a.action in ('capture','record'):
                need(role == 'mini','capture_requires_mini'); result = capture(a.url,state,a.action == 'record')
            elif a.action == 'process':
                need(role in ('home','sjm'),'process_requires_knowledge_host'); result = process(a.run_id,a.manifest_sha256,state)
            elif a.action == 'status': result = read_status(a.run_id,a.manifest_sha256,state)
            else:
                need(role in ('home','sjm'),'save_requires_knowledge_host')
                # Digest storage reuses the writer's provenance and hash checks.
                result = legacy.save_digest(a.run_id,a.manifest_sha256,preparation_state(state,a.manifest_sha256))
        print(json.dumps(result,ensure_ascii=False)); return 0
    except Exception as e:
        print(json.dumps({'status':'blocked','reason':str(e) if isinstance(e,Blocked) else type(e).__name__}),flush=True); return 2
