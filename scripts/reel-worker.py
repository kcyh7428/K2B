#!/opt/homebrew/bin/python3 -I
"""Installed, bounded Facebook capture and Home audio preparation. No LLM calls."""
import argparse
import contextlib
import datetime as dt
import fcntl
import hashlib
import ipaddress
import json
import math
import os
import pwd
from pathlib import Path
import re
import secrets
import shlex
import shutil
import signal
import socket
import stat
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

VERSION = '1'
MAX_BYTES = 256 * 1024**2
MAX_SECONDS = 600
NAS = Path('/Volumes/k2b-captures')
TOOLS = {name: '/opt/homebrew/bin/' + name for name in ('ffmpeg', 'ffprobe', 'yt-dlp')}
RUN_RE = r'reel-([0-9]{1,24})-[0-9]{8}T[0-9]{6}Z-[a-f0-9]{8}'
MINI_FAKE_IP_ROUTE = False
MINI_FAKE_IP_RANGE = ipaddress.ip_network('198.18.0.0/15')


class Blocked(Exception):
    pass


def own_home():
    return Path(pwd.getpwuid(os.getuid()).pw_dir)


def need(condition, reason):
    if not condition:
        raise Blocked(reason)


def source_url(value):
    p = urllib.parse.urlsplit(value)
    need(p.scheme == 'https' and p.netloc in ('www.facebook.com', 'facebook.com', 'm.facebook.com'), 'unsupported_source_host')
    need(bool(re.fullmatch(r'/(?:reel/[0-9]{1,24}|share/v/[A-Za-z0-9]{1,64})/?', p.path)), 'unsupported_source_path')
    # The path identifies the source. Never forward tracking or redirect parameters.
    return urllib.parse.urlunsplit((p.scheme,p.netloc,p.path,'',''))


def media_address_allowed(value):
    address=ipaddress.ip_address(value)
    # The named Mini's existing Meta HTTPS route was verified in this fake-IP range.
    # This is not an allowance for LAN/loopback URLs or caller-selected proxies.
    return address.is_global or (MINI_FAKE_IP_ROUTE and address in MINI_FAKE_IP_RANGE)


def check_remote(value, media=False):
    p = urllib.parse.urlsplit(value)
    host = p.hostname or ''
    need(p.scheme == 'https' and p.netloc == host, 'invalid_remote_url')
    domains = ('fbcdn.net', 'fbsbx.com') if media else ('facebook.com',)
    need(any(host == d or host.endswith('.'+d) for d in domains), 'unexpected_remote_host')
    addresses = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    need(bool(addresses) and all(media_address_allowed(a[4][0]) for a in addresses), 'unsupported_media_address')


class Redirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        check_response_peer(fp)
        check_remote(newurl, media=True)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def check_response_peer(response):
    raw=getattr(getattr(response,'fp',None),'raw',None);connection=getattr(raw,'_sock',None)
    need(connection is not None,'media_peer_unavailable')
    need(media_address_allowed(connection.getpeername()[0]),'unsupported_media_peer')


def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for b in iter(lambda: f.read(1024*1024), b''):
            h.update(b)
    return h.hexdigest()


def safe_dir(path):
    path = Path(path)
    for p in (path, *path.parents):
        need(not p.is_symlink(), 'symlink_path')
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    return path


def child(root, name):
    p = Path(name)
    need(not p.is_absolute() and bool(p.parts) and all(x not in ('..', '.') for x in p.parts), 'escaping_manifest_path')
    target = root / p
    need(target.resolve().is_relative_to(root.resolve()), 'escaping_manifest_path')
    for a in (target, *target.parents):
        need(not a.is_symlink(), 'symlink_payload')
        if a == root:
            break
    return target


def write_json(path, data):
    need(not path.is_symlink(), 'symlink_json')
    tmp = path.with_name(path.name+'.'+secrets.token_hex(4)+'.tmp')
    with tmp.open('x') as f:
        json.dump(data, f, ensure_ascii=False, indent=2, allow_nan=False)
        f.write('\n'); f.flush(); os.fsync(f.fileno())
    os.replace(tmp, path)


def read_json(path):
    need(path.is_file() and not path.is_symlink() and path.stat().st_size < 8*1024**2, 'invalid_json_file')
    return json.loads(path.read_text())


def run_video_id(value):
    match = re.fullmatch(RUN_RE, value)
    need(match is not None, 'invalid_run_id')
    return match[1]


def size(root):
    total = 0
    for p in root.rglob('*'):
        need(not p.is_symlink(), 'symlink_in_run')
        if p.is_file():
            total += p.stat().st_size
    return total


def check_budget(root, maximum=MAX_BYTES, reserve=4*1024**2):
    need(size(root) <= maximum-reserve, 'run_byte_budget')


def mount_check(path, role):
    lines = subprocess.check_output(['/sbin/mount', '-t', 'smbfs'], text=True)
    expression=r'//([^/@]+)@192\.168\.50\.67/k2b-captures on '+re.escape(str(path))+r' \(smbfs,[^\n]*\)'
    matches = [line for line in lines.splitlines() if re.fullmatch(expression,line)]
    need(bool(matches), 'nas_not_mounted')
    if role == 'mini':
        need(any(line.startswith('//k2b-capture-mini@') for line in matches), 'wrong_mini_nas_account')
    else:
        need(any(line.startswith('//cheung@') for line in matches),'wrong_home_nas_account')


def space_check(root, payload=MAX_BYTES):
    for p in (root, NAS):
        s = os.statvfs(p)
        need(s.f_bavail*s.f_frsize >= 2*payload, 'insufficient_free_space')


def verify(root, digest):
    need(bool(re.fullmatch('[a-f0-9]{64}', digest)), 'invalid_manifest_digest')
    need(not root.is_symlink(), 'symlink_run')
    manifest = child(root, 'manifest.json')
    need(sha(manifest) == digest, 'manifest_digest_mismatch')
    m = read_json(manifest)
    need(isinstance(m.get('files'), list) and 0 < len(m['files']) <= 2000, 'invalid_manifest')
    seen = set(); total = 0
    for item in m['files']:
        name = item['name']; need(name not in seen, 'duplicate_manifest_path'); seen.add(name)
        p = child(root, name)
        need(p.is_file() and stat.S_ISREG(p.stat().st_mode), 'missing_payload')
        total += p.stat().st_size
        need(total <= MAX_BYTES, 'payload_byte_budget')
        need(p.stat().st_size == item['bytes'] and sha(p) == item['sha256'], 'payload_hash_mismatch')
    return m


def clean_env():
    return {'PATH':'/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin', 'HOME':str(own_home()), 'LANG':'en_US.UTF-8'}


def command(args, root, deadline, limit=8*1024**2, combined=False):
    # Child output can contain signed CDN URLs. Keep it private in memory, never in receipts.
    with tempfile_file(root) as output, tempfile_file(root) as errors:
        process = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=output, stderr=output if combined else errors,
                                   env=clean_env(), start_new_session=True)
        try:
            while process.poll() is None:
                check_budget(root, reserve=2*limit+1024*1024)
                need(time.monotonic() < deadline, 'run_deadline')
                need(os.fstat(output.fileno()).st_size < limit and os.fstat(errors.fileno()).st_size < limit, 'command_output_limit')
                time.sleep(.1)
            need(os.fstat(output.fileno()).st_size <= limit and os.fstat(errors.fileno()).st_size <= limit,'command_output_limit')
            if process.returncode:
                diagnostic=output if combined else errors
                diagnostic.seek(max(0,os.fstat(diagnostic.fileno()).st_size-4096))
                detail=redact(diagnostic.read(4096).decode(errors='replace'))
                path=root/('failure-'+secrets.token_hex(4)+'.json')
                write_json(path,{'tool':Path(args[0]).name,'exit_code':process.returncode,'stderr_redacted':detail})
                raise Blocked('command_failed_'+Path(args[0]).name+' diagnostics='+str(path))
            output.seek(0); result = output.read(limit+1)
            need(len(result) <= limit, 'command_output_limit')
            return result
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL); process.wait()


@contextlib.contextmanager
def tempfile_file(root):
    import tempfile
    # Anonymous temporary file, so expiring media URLs never enter retained logs.
    with tempfile.TemporaryFile(dir=root) as f:
        yield f


def redact(text):
    lines=[line for line in text.splitlines() if not re.search(r'authorization|bearer|cookie|api[_-]?key',line,re.I)]
    result='\n'.join(lines)
    result=re.sub(r'https?://\S+','[URL]',result)
    result=re.sub(r'\b(?:\d{1,3}\.){3}\d{1,3}\b','[IP]',result)
    result=re.sub(r'\b[A-Za-z0-9_./+=-]{48,}\b','[LONG_TOKEN]',result)
    return result[-4096:]


def probe(path, root, deadline):
    return json.loads(command([TOOLS['ffprobe'], '-v','error','-show_format','-show_streams','-of','json',str(path)], root, deadline))


def download(url, path, root, deadline):
    check_remote(url, media=True)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), Redirects())
    req = urllib.request.Request(url, headers={'User-Agent':'Mozilla/5.0'})
    with opener.open(req, timeout=20) as response, path.open('xb') as out:
        check_response_peer(response)
        check_remote(response.geturl(), media=True)
        while True:
            need(time.monotonic() < deadline, 'run_deadline'); check_budget(root)
            b = response.read(256*1024)
            if not b: break
            out.write(b)
    check_budget(root)


def chunk_schedule(duration):
    need(math.isfinite(duration) and 0 < duration <= MAX_SECONDS+1, 'invalid_audio_duration')
    result = []; start = 0
    while start < duration:
        length = min(240, duration-start); result.append((start,length))
        if start+length >= duration: break
        start += 235
    return result


def segments(response, offset, allow_empty=False):
    text=response.get('text');raw=response.get('segments')
    if allow_empty and 'text' in response and (text is None or isinstance(text,str) and not text.strip()) and (raw is None or raw==[]):return []
    need(isinstance(response.get('text'), str) and bool(response['text'].strip()), 'empty_transcription')
    raw = response.get('segments'); need(isinstance(raw,list) and bool(raw), 'missing_transcript_segments')
    result = []
    for s in raw:
        a,b = s['start'],s['end']
        need(isinstance(a,(int,float)) and isinstance(b,(int,float)) and 0 <= a <= b <= 241 and math.isfinite(b), 'invalid_segment_time')
        need(isinstance(s['text'],str), 'invalid_segment_text')
        result.append({'start':a+offset,'end':b+offset,'text':s['text'].strip()})
    return result


def groq_key(path=None):
    p = path or own_home()/'.k2b-env'
    s = p.lstat()
    need(stat.S_ISREG(s.st_mode) and s.st_uid == os.getuid() and stat.S_IMODE(s.st_mode)==0o600, 'unsafe_credential_file')
    for line in p.read_text().splitlines():
        match = re.fullmatch(r'\s*(?:export\s+)?GROQ_API_KEY=(.*)', line)
        if match:
            values = shlex.split(match[1], comments=True)
            need(len(values)==1 and bool(re.fullmatch(r'[A-Za-z0-9_-]+', values[0])), 'invalid_groq_credential')
            return values[0]
    raise Blocked('missing_groq_credential')


def transcribe(path):
    data = path.read_bytes(); need(len(data)<24*1024**2, 'audio_request_too_large')
    boundary='k2b'+secrets.token_hex(16)
    body = bytearray()
    for k,v in [('model','whisper-large-v3'),('response_format','verbose_json'),('timestamp_granularities[]','segment'),('temperature','0')]:
        body.extend(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
    body.extend(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="audio.flac"\r\nContent-Type: audio/flac\r\n\r\n'.encode())
    body.extend(data); body.extend(f'\r\n--{boundary}--\r\n'.encode())
    request = urllib.request.Request('https://api.groq.com/openai/v1/audio/transcriptions', data=body,
              headers={'Content-Type':'multipart/form-data; boundary='+boundary,'Authorization':'Bearer '+groq_key(),'User-Agent':'K2B-Reel/'+VERSION})
    # Do not follow redirects with an Authorization header.
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self,*args,**kwargs): raise Blocked('transcription_redirect')
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
    try:
        with opener.open(request, timeout=90) as r:
            data=r.read(4*1024**2+1); need(len(data)<=4*1024**2,'transcript_response_limit')
            return json.loads(data)
    except urllib.error.HTTPError as error:
        raise Blocked('transcription_http_'+str(error.code)) from None


def config():
    p=Path(__file__).parent/'config.json'; c=read_json(p)
    need(c.get('role') in ('home','mini'), 'invalid_install_role')
    need(c.get('worker_sha256')==sha(Path(__file__)), 'installed_code_changed')
    expected_user='keithmbpm2' if c['role']=='home' else 'fastshower'
    need(pwd.getpwuid(os.getuid()).pw_name==expected_user, 'wrong_host_user')
    return c


@contextlib.contextmanager
def lock(state):
    p=child(state,'worker.lock')
    with p.open('a') as f:
        try: fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError: raise Blocked('worker_busy')
        yield


def payload_manifest(root, extra):
    files=[]
    for p in sorted(root.rglob('*')):
        if p.is_file() and p.name not in ('manifest.json','completion.json'):
            if p.relative_to(root).parts[0]=='partials': continue
            need(not p.is_symlink(),'symlink_payload')
            files.append({'name':str(p.relative_to(root)),'bytes':p.stat().st_size,'sha256':sha(p)})
    write_json(root/'manifest.json',dict(extra,files=files))
    check_budget(root)
    return sha(root/'manifest.json')


def nas_run(run):
    base=NAS/'facebook'/run_video_id(run)
    for parent in (base,*base.parents):need(not parent.is_symlink(),'symlink_nas_parent')
    return child(base,run)


def publish_file(source,target,digest,budget_root,deadline=None):
    deadline=deadline or time.monotonic()+600
    safe_dir(target.parent)
    if target.exists():
        need(sha(target)==digest,'nas_committed_file_conflict');return
    partials=safe_dir(budget_root/'partials')
    staged=child(partials,target.name+'.partial-'+secrets.token_hex(8))
    # Only this host owns this destination. A failed copy never receives a final name.
    try:
        with source.open('rb') as inp,staged.open('xb') as out:
            while True:
                need(time.monotonic()<deadline,'run_deadline')
                check_budget(budget_root)
                block=inp.read(256*1024)
                if not block:break
                out.write(block)
            out.flush();os.fsync(out.fileno())
        need(sha(staged)==digest,'nas_staged_hash_mismatch')
        need(not target.exists(),'nas_concurrent_publication')
        staged.rename(target)
        need(sha(target)==digest,'nas_readback_mismatch')
    except OSError as error:
        raise Blocked('nas_copy_incomplete preserved='+str(staged)) from error


def transfer(root, digest,deadline=None):
    deadline=deadline or time.monotonic()+600
    mount_check(NAS,'mini'); space_check(root,max(MAX_BYTES,size(root)))
    verify(root,digest); dest=nas_run(root.name)
    safe_dir(dest)
    m=read_json(root/'manifest.json')
    for item in [*m['files'],{'name':'manifest.json','sha256':digest}]:
        src=child(root,item['name']); target=child(dest,item['name'])
        publish_file(src,target,item['sha256'],dest,deadline)
    verify(dest,digest)
    result={'status':'captured','run_id':root.name,'manifest_sha256':digest,'nas_path':str(dest),'local_path':str(root),'bytes':size(root)}
    write_json(root/'completion.json',result)
    return result


def choose_formats(formats):
    video=[f for f in formats if f.get('vcodec') not in (None,'none') and (f.get('width') or 0)>0 and (f.get('height') or 0)>0]
    audio=[f for f in formats if f.get('vcodec')=='none' and f.get('acodec') not in (None,'none')]
    if not audio: video=[f for f in video if f.get('acodec') not in (None,'none')]
    need(bool(video),'media_formats_unavailable')
    low=[f for f in video if max(f['width'],f['height'])<=1280 and min(f['width'],f['height'])<=720]
    if low: v=max(low,key=lambda f:((f.get('width') or 0)*(f.get('height') or 0),f.get('tbr') or 0))
    else: v=min(video,key=lambda f:(f.get('filesize') or f.get('filesize_approx') or (f.get('tbr') or 1e9)*1000,f['width']*f['height']))
    a=max(audio,key=lambda f:f.get('abr') or f.get('tbr') or 0) if audio else None
    return v,a,not bool(low)


def reusable_capture(cache,fingerprint,state):
    if not cache.exists(): return None
    r=read_json(cache)
    if r['fingerprint']!=fingerprint or not 0<=time.time()-r.get('captured_at',0)<86400:return None
    run_video_id(r['run_id']);old=child(state,r['run_id'])
    if not old.exists():
        q=safe_dir(state/'quarantine')/('missing-run-cache-'+secrets.token_hex(4)+'.json')
        cache.rename(q);return None
    # Existing corrupt evidence is an integrity hold, never a silent recapture.
    verify(old,r['manifest_sha256']);return r


def capture(url,state):
    url=source_url(url); mount_check(NAS,'mini'); space_check(state)
    deadline=time.monotonic()+600
    preflight=safe_dir(state/('preflight-'+secrets.token_hex(8)))
    try:
        metadata=json.loads(command([TOOLS['yt-dlp'],'--ignore-config','--no-plugin-dirs','--no-cache-dir','--no-playlist','--skip-download','--dump-single-json','--socket-timeout','15','--retries','0',url],preflight,min(deadline,time.monotonic()+45)))
    finally:
        if not any(preflight.iterdir()): preflight.rmdir()
    video_id=str(metadata.get('id','')); need(bool(re.fullmatch('[0-9]{1,24}',video_id)),'invalid_video_identity')
    duration=float(metadata.get('duration') or 0); need(0<duration<=MAX_SECONDS,'source_duration_limit')
    v,a,downscale=choose_formats(metadata.get('formats',[]))
    fingerprint=hashlib.sha256(json.dumps({'id':video_id,'duration':duration,'title':metadata.get('title'),'formats':[(f.get('format_id'),f.get('filesize'),f.get('filesize_approx')) for f in (v,a or v)]},sort_keys=True).encode()).hexdigest()
    cache=state/('source-'+video_id+'.json')
    r=reusable_capture(cache,fingerprint,state)
    if r:
        result=transfer(child(state,r['run_id']),r['manifest_sha256'],deadline); result['reused']=True; return result
    run=f'reel-{video_id}-{dt.datetime.now(dt.timezone.utc):%Y%m%dT%H%M%SZ}-{secrets.token_hex(4)}'
    root=safe_dir(state/run)
    write_json(root/'acquisition.json',{'submitted_url':url,'canonical_url':'https://www.facebook.com/reel/'+video_id+'/','fingerprint':fingerprint,'duration':duration,'title':metadata.get('title'),'creator':metadata.get('uploader'),'utc':dt.datetime.now(dt.timezone.utc).isoformat()})
    for f,name in ((v,'video.mp4'), *(([(a,'audio.m4a')] if a else []))):
        download(f['url'],root/name,root,deadline)
    audio_input=root/('audio.m4a' if a else 'video.mp4')
    encoding=['-c:v','copy']
    if downscale or not str(v.get('vcodec','')).startswith(('h264','avc1')):
        scale="scale=w='if(gte(iw,ih),1280,720)':h='if(gte(iw,ih),720,1280)':force_original_aspect_ratio=decrease:force_divisible_by=2"
        encoding=['-vf',scale,'-c:v','libx264','-preset','veryfast','-crf','23','-c:a','copy']
    audio_codec=str((a or v).get('acodec',''))
    encoding+=['-c:a','copy'] if audio_codec.startswith(('aac','mp4a','mp3','alac')) else ['-c:a','aac','-b:a','128k']
    command([TOOLS['ffmpeg'],'-nostdin','-v','error','-n','-i',str(root/'video.mp4'),'-i',str(audio_input),'-map','0:v:0','-map','1:a:0',*encoding,str(root/'source.mp4')],root,deadline)
    p=probe(root/'source.mp4',root,deadline)
    actual=float(p['format']['duration']); need(abs(actual-duration)<1,'source_duration_mismatch')
    need({'audio','video'} <= {s['codec_type'] for s in p['streams']},'missing_media_stream')
    command([TOOLS['ffmpeg'],'-nostdin','-v','error','-xerror','-i',str(root/'source.mp4'),'-map','0:v:0','-map','0:a:0','-f','null','-'],root,deadline)
    command([TOOLS['ffmpeg'],'-nostdin','-v','error','-n','-i',str(root/'source.mp4'),'-map','0:a:0','-ac','1','-ar','16000','-c:a','flac',str(root/'speech.flac')],root,deadline)
    write_json(root/'validation.json',{'duration':actual,'decode':'entire_audio_video_passed','streams':[{k:s[k] for k in ('codec_type','codec_name','width','height','sample_rate') if k in s} for s in p['streams']]})
    (root/'run-marker.txt').write_text(secrets.token_hex(32)+'\n')
    digest=payload_manifest(root,{'version':VERSION,'run_id':run,'video_id':video_id,'duration':actual,'method':'public_media_no_cookies','marker_sha256':sha(root/'run-marker.txt')})
    # Commit acquisition before transfer so a failed NAS write resumes without downloading.
    write_json(cache,{'fingerprint':fingerprint,'run_id':run,'manifest_sha256':digest,'captured_at':time.time()})
    return transfer(root,digest,deadline)


def prepare_visuals(source,root,duration,digest,deadline):
    stage=root/'visual-coverage.json';checkpoint=root/'visual-progress.json'
    media=probe(source,root,deadline);video=next(s for s in media['streams'] if s['codec_type']=='video')
    visual_duration=float(video.get('duration') or media['format']['duration'])
    need(math.isfinite(visual_duration) and visual_duration>0,'invalid_visual_duration')
    visual_duration=min(duration,visual_duration)
    count=max(1,math.ceil(visual_duration));names=[f'sheet-{i+1:03d}.jpg' for i in range(math.ceil(count/16))]
    expected_sheets=[{'path':str(root/name),'first_second':i*16,'last_second':min(i*16+15,count-1),'frame_count':min(16,count-i*16),'padding_cells':16-min(16,count-i*16),'sample_seconds':list(range(i*16,min(i*16+16,count)))} for i,name in enumerate(names)]
    if stage.exists():
        coverage=read_json(stage);need(coverage['source_manifest_sha256']==digest,'visual_source_mismatch')
        need(coverage['sheets']==expected_sheets and coverage['fps']==1 and coverage['ending']==str(root/'ending.jpg'),'visual_coverage_gap')
        need(len(coverage['sample_files'])==len(names)+1 and {f['name'] for f in coverage['sample_files']}==set([*names,'ending.jpg']),'visual_coverage_gap')
        for f in coverage['sample_files']:need(sha(child(root,f['name']))==f['sha256'],'visual_stage_hash_mismatch')
        return coverage
    if checkpoint.exists():
        progress=read_json(checkpoint)
        need(progress['source_manifest_sha256']==digest and progress['duration']==duration,'visual_progress_source_mismatch')
    else:
        detection=command([TOOLS['ffmpeg'],'-nostdin','-hide_banner','-i',str(source),'-vf','cropdetect=24:2','-t','5','-f','null','-'],root,deadline,combined=True)
        candidates=re.findall(rb'crop=(\d+:\d+:\d+:\d+)',detection)
        progress={'source_manifest_sha256':digest,'duration':duration,'crop':candidates[-1].decode() if candidates else None,'completed':{},'sample_seconds':{}}
        write_json(checkpoint,progress)
    crop=progress['crop'];sheets=[]
    need(set(progress['completed'])<=set([*names,'ending.jpg']),'unexpected_visual_progress_file')
    def ready(name):
        path=child(root,name)
        if name in progress['completed']:
            need(path.is_file() and sha(path)==progress['completed'][name],'visual_progress_hash_mismatch')
            if name in names:need(progress['sample_seconds'][name]==expected_sheets[names.index(name)]['sample_seconds'],'visual_sample_time_mismatch')
            return True
        if path.exists():
            partials=safe_dir(root/'partials'/secrets.token_hex(4));path.rename(partials/path.name)
        return False
    def commit(name,times=None):
        path=child(root,name);need(path.is_file() and path.stat().st_size>0,'missing_visual_output')
        progress['completed'][name]=sha(path)
        if times is not None:progress['sample_seconds'][name]=times
        write_json(checkpoint,progress)
    base_filter=('crop='+crop+',' if crop else '')+'scale=320:-2,'
    for i,name in enumerate(names):
        if not ready(name):
            observed=command([TOOLS['ffmpeg'],'-nostdin','-hide_banner','-v','info','-n','-accurate_seek','-ss',str(i*16),'-t',str(min(16,visual_duration-i*16)),'-i',str(source),'-vf','fps=1:start_time=0:round=up,showinfo,'+base_filter+'tile=4x4:nb_frames=16:padding=2:margin=2','-frames:v','1','-q:v','3',str(root/name)],root,deadline,combined=True)
            times=[float(t)+i*16 for t in re.findall(rb'n:\s*\d+\s+pts:\s*-?\d+\s+pts_time:([0-9.+-]+)',observed)]
            need(times==expected_sheets[i]['sample_seconds'],'visual_sample_count_or_time_mismatch')
            commit(name,times)
        sheets.append(expected_sheets[i])
    if not ready('ending.jpg'):
        command([TOOLS['ffmpeg'],'-nostdin','-v','error','-n','-accurate_seek','-ss',str(max(0,visual_duration-.1)),'-i',str(source),'-frames:v','1',str(root/'ending.jpg')],root,deadline)
        commit('ending.jpg')
    coverage={'source_manifest_sha256':digest,'fps':1,'visual_duration':visual_duration,'unpictured_audio_tail_seconds':max(0,duration-visual_duration),'crop':crop,'crop_sampling_window_seconds':[0,min(5,duration)],'crop_is_provisional':bool(crop),'sheets':sheets,'sample_files':[{'name':name,'sha256':progress['completed'][name]} for name in [*names,'ending.jpg']],'reading_order':'left to right, top to bottom; 4 columns by 4 rows; only frame_count cells are samples, remaining cells are padding','ending':str(root/'ending.jpg'),'limitation':'sampled visuals, not every frame; crop derived from first five seconds only'}
    write_json(stage,coverage);return coverage


def process(run,digest,state):
    run_video_id(run); mount_check(NAS,'home'); space_check(state)
    src=nas_run(run); manifest=verify(src,digest)
    need(manifest.get('run_id')==run and manifest.get('video_id')==run_video_id(run),'source_binding_mismatch')
    need(sha(src/'run-marker.txt')==manifest.get('marker_sha256'),'marker_mismatch')
    root=safe_dir(state/run); receipt=root/'prepared.json';deadline=time.monotonic()+600
    if receipt.exists():
        r=read_json(receipt); need(r['source_manifest_sha256']==digest,'prepared_source_mismatch')
        verify(root,r['prepared_manifest_sha256'])
        need({k:v for k,v in r.items() if k!='prepared_manifest_sha256'}==read_json(root/'prepared-claims.json'),'prepared_receipt_mismatch')
        persist_home(root,run,deadline); return dict(r,reused=True)
    duration=float(manifest['duration']); all_segments=[]; chunks=[]
    for i,(offset,length) in enumerate(chunk_schedule(duration)):
        audio=root/f'chunk-{i:03d}.flac'; response=root/f'transcript-{i:03d}.json'
        ensure_audio_chunk(src/'speech.flac',audio,offset,length,root,deadline,response.exists())
        if not response.exists():
            need(time.monotonic()<deadline,'run_deadline')
            r=transcribe(audio); segments(r,offset,allow_empty=True)
            write_json(response,{'source_manifest_sha256':digest,'audio_sha256':sha(audio),'response':r})
        r=read_json(response)
        need(r['source_manifest_sha256']==digest and r['audio_sha256']==sha(audio),'transcript_source_mismatch')
        current=segments(r['response'],offset,allow_empty=True)
        # Retain overlap text explicitly. Dropping a crossing sentence can lose speech.
        all_segments.extend(dict(s,chunk=i) for s in current)
        chunks.append({'offset':offset,'duration':length,'response':response.name,'transcript_status':'words_returned' if current else 'empty_audio_result_unverified'})
    gaps=[{'start':c['offset'],'end':c['offset']+c['duration'],'reason':'This chunk returned no words; silence or music not independently established','neighbor_word_segments':[s for s in all_segments if s['start']<c['offset']+c['duration'] and s['end']>c['offset']]} for c in chunks if c['transcript_status']!='words_returned']
    write_json(root/'timeline.json',{'source_manifest_sha256':digest,'chunks':chunks,'segments':all_segments,'audio_gaps':gaps,'overlap_policy':'5 second overlap retained and labeled by chunk; assistant reconciles repeated speech without dropping boundary sentences'})
    (root/'transcript.txt').write_text('\n'.join(f"[{s['start']:.2f} - {s['end']:.2f}] {s['text']}" for s in all_segments)+'\n')
    coverage=prepare_visuals(src/'source.mp4',root,duration,digest,deadline)
    sheets=coverage['sheets'];ending=root/'ending.jpg';visual_stage=root/'visual-coverage.json'
    check_budget(root)
    result={'status':'prepared_for_assistant_review','run_id':run,'source_manifest_sha256':digest,'transcript':str(root/'transcript.txt'),'visual_coverage':str(root/'visual-coverage.json'),'sheets':sheets,'ending':str(ending),'duration':duration,'chunks':len(chunks),'segments':len(all_segments),'audio_gaps':gaps}
    write_json(root/'prepared-claims.json',result)
    prepared_digest=payload_manifest(root,{'source_manifest_sha256':digest,'run_id':run})
    result=dict(result,prepared_manifest_sha256=prepared_digest)
    write_json(receipt,result)
    persist_home(root,run,deadline)
    return result


def persist_home(root,run,deadline=None):
    mount_check(NAS,'home'); space_check(root,max(MAX_BYTES,size(root)))
    digest=read_json(root/'prepared.json')['prepared_manifest_sha256']
    need(bool(re.fullmatch('[a-f0-9]{64}',digest)),'invalid_prepared_digest')
    dest=safe_dir(nas_run(run)/'home-v1'/digest)
    manifest=verify(root,digest)
    for name in [*[f['name'] for f in manifest['files']],'manifest.json','prepared.json']:
        p=child(root,name)
        if p.is_file():
            target=child(dest,name)
            publish_file(p,target,sha(p),dest,deadline)
    verify(dest,digest)
    write_json(root/'publication.json',{'prepared_manifest_sha256':digest,'nas_path':str(dest),'status':'published'})


def ensure_audio_chunk(source,audio,offset,length,root,deadline,committed):
    def valid(path):
        p=probe(path,root,deadline)
        need(abs(float(p['format']['duration'])-length)<.15,'chunk_duration_mismatch')
        command([TOOLS['ffmpeg'],'-nostdin','-v','error','-xerror','-i',str(path),'-map','0:a:0','-f','null','-'],root,deadline)
    if audio.exists():
        try:
            valid(audio);return
        except Blocked:
            if committed: raise
    # Only incomplete, uncommitted intermediates are moved aside automatically.
    stale=[*root.glob(audio.stem+'-partial-*.flac')]
    if audio.exists():stale.append(audio)
    if stale:
        partials=safe_dir(root/'partials'/secrets.token_hex(4))
        for p in stale:need(not p.is_symlink(),'symlink_audio_partial');p.rename(partials/p.name)
    staged=root/(audio.stem+'-partial-'+secrets.token_hex(4)+'.flac')
    command([TOOLS['ffmpeg'],'-nostdin','-v','error','-n','-ss',str(offset),'-i',str(source),'-t',str(length),'-ac','1','-ar','16000','-c:a','flac',str(staged)],root,deadline)
    valid(staged);staged.rename(audio)


def save_digest(run,digest,state):
    deadline=time.monotonic()+600
    run_video_id(run); mount_check(NAS,'home'); verify(nas_run(run),digest); space_check(state)
    root=child(state,run)
    prepared=read_json(root/'prepared.json'); need(prepared['source_manifest_sha256']==digest,'digest_source_mismatch')
    verify(root,prepared['prepared_manifest_sha256'])
    draft_root=own_home()/'Projects/K2B/output/reel-digests'
    need(draft_root.is_dir(),'digest_draft_directory_missing')
    draft=child(draft_root,run+'.md')
    need(draft.is_file() and draft.stat().st_size<256*1024,'invalid_digest_draft')
    header=draft.read_text()[:4096]
    need('Source run: '+run+'\n' in header and 'Source manifest: '+digest+'\n' in header,'digest_provenance_missing')
    dest=safe_dir(nas_run(run)/'digests')/('digest-'+sha(draft)+'.md')
    publish_file(draft,dest,sha(draft),dest.parent,deadline)
    return {'status':'digest_saved','run_id':run,'path':str(dest),'sha256':sha(dest)}


def recover_local(run,digest,state,role):
    # Deliberately absent from saved allow rules. Operator must authorize exact run.
    run_video_id(run); mount_check(NAS,role); verify(nas_run(run),digest)
    root=child(state,run)
    quarantine=safe_dir(state/'quarantine')/(run+'-'+secrets.token_hex(4))
    cache=state/('source-'+run_video_id(run)+'.json')
    if role=='mini' and cache.exists():
        r=read_json(cache); need(r['run_id']==run and r['manifest_sha256']==digest,'cache_recovery_mismatch')
    need(root.is_dir() or (role=='mini' and cache.exists()),'missing_local_run')
    if root.is_dir():root.rename(quarantine)
    else:safe_dir(quarantine)
    if role=='mini' and cache.exists(): cache.rename(quarantine/'source-cache.json')
    return {'status':'local_evidence_quarantined','run_id':run,'preserved_path':str(quarantine),'next_step':'Re-run the normal capture or process command; NAS originals were verified and untouched'}


def inventory(state,role=None):
    result=[]
    candidates=list(state.iterdir())
    if (state/'quarantine').is_dir() and not (state/'quarantine').is_symlink():candidates+=list((state/'quarantine').iterdir())
    for p in sorted(candidates):
        if re.fullmatch(RUN_RE,p.name) and p.is_symlink():
            result.append({'run_id':p.name,'path':str(p),'warning':'symlink_run_dir'})
        elif (re.fullmatch(RUN_RE,p.name) or p.name.startswith('preflight-') or p.parent.name=='quarantine') and p.is_dir():
            item={'run_id':p.name,'path':str(p),'modified_utc':dt.datetime.fromtimestamp(p.stat().st_mtime,dt.timezone.utc).isoformat()}
            try:item['bytes']=size(p)
            except (Blocked,OSError) as e:item['warning']=str(e) if isinstance(e,Blocked) else type(e).__name__
            result.append(item)
        elif p.parent.name=='quarantine' and p.is_file() and not p.is_symlink():
            result.append({'path':str(p),'bytes':p.stat().st_size,'kind':'quarantined_receipt'})
    report={'status':'inventory','retention':'No automatic deletion; see skill retention procedure','local_runs':result}
    if role:
        report['nas_partials']=[]
        try:
            mount_check(NAS,role)
            for p in sorted(state.iterdir()):
                if not re.fullmatch(RUN_RE,p.name) or not p.is_dir() or p.is_symlink():continue
                destination=nas_run(p.name)
                if not destination.exists():continue
                for partial in destination.rglob('*.partial-*'):
                    child(destination,str(partial.relative_to(destination)))
                    if partial.is_file():report['nas_partials'].append({'path':str(partial),'bytes':partial.stat().st_size})
                    need(len(report['nas_partials'])<=2000,'nas_partial_inventory_limit')
            report['nas_status']='mounted_checked'
        except (Blocked,OSError) as error:
            report['nas_status']=str(error) if isinstance(error,Blocked) else type(error).__name__
    return report


def arguments(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='action',required=True)
    sub.add_parser('version')
    sub.add_parser('inventory')
    sub.add_parser('capture').add_argument('url')
    for name in ('process','save-digest','recover-local'):
        p=sub.add_parser(name); p.add_argument('run_id');p.add_argument('manifest_sha256')
    sub.add_parser('status').add_argument('run_id')
    return parser.parse_args(argv)


@contextlib.contextmanager
def operation_timeout(seconds):
    def expired(*unused):raise Blocked('run_deadline')
    previous=signal.signal(signal.SIGALRM,expired);started=time.monotonic()
    timer=signal.setitimer(signal.ITIMER_REAL,seconds)
    try:yield
    finally:
        signal.setitimer(signal.ITIMER_REAL,0);signal.signal(signal.SIGALRM,previous)
        if timer[0]>0:signal.setitimer(signal.ITIMER_REAL,max(.001,timer[0]-(time.monotonic()-started)),timer[1])


def read_status(run,state,role):
    run_video_id(run);root=child(state,run)
    result=read_json(root/('completion.json' if role=='mini' else 'prepared.json'))
    if role=='home':
        need((root/'publication.json').is_file(),'home_publication_pending')
        published=read_json(root/'publication.json');digest=result['prepared_manifest_sha256']
        destination=nas_run(run)/'home-v1'/digest
        need(published=={'prepared_manifest_sha256':digest,'nas_path':str(destination),'status':'published'},'home_publication_receipt_mismatch')
        mount_check(NAS,role);verify(nas_run(run),result['source_manifest_sha256'])
        verify(root,digest);verify(destination,digest)
        need({k:v for k,v in result.items() if k!='prepared_manifest_sha256'}==read_json(root/'prepared-claims.json'),'prepared_receipt_mismatch')
        need(sha(destination/'prepared.json')==sha(root/'prepared.json'),'home_publication_receipt_mismatch')
    else:
        mount_check(NAS,role);verify(root,result['manifest_sha256']);verify(nas_run(run),result['manifest_sha256'])
    return dict(result,nas_publication_verified=True)


def main():
    global MINI_FAKE_IP_ROUTE
    os.umask(0o077)
    try:
        args=arguments(); c=config(); role=c['role']
        MINI_FAKE_IP_ROUTE=role=='mini'
        state=safe_dir(own_home()/'.local/state/k2b/reels-v1')
        with operation_timeout(600),(contextlib.nullcontext() if args.action in ('version','status','inventory') else lock(state)):
            if args.action=='version': result={'version':VERSION,'role':role,'worker_sha256':sha(Path(__file__)),'media_transport':'verified_mini_fake_ip_or_public' if MINI_FAKE_IP_ROUTE else 'public_only'}
            elif args.action=='inventory':
                result=inventory(state,role)
            elif args.action=='recover-local': result=recover_local(args.run_id,args.manifest_sha256,state,role)
            elif args.action=='capture':
                need(role=='mini','capture_requires_mini');result=capture(args.url,state)
            elif args.action=='process':
                need(role=='home','process_requires_home');result=process(args.run_id,args.manifest_sha256,state)
            elif args.action=='save-digest':
                need(role=='home','save_requires_home');result=save_digest(args.run_id,args.manifest_sha256,state)
            else:
                result=read_status(args.run_id,state,role)
        print(json.dumps(result,ensure_ascii=False)); return 0
    except Exception as e:
        # Never print raw HTTP exceptions, signed media URLs or credential values.
        reason=str(e) if isinstance(e,Blocked) else type(e).__name__
        print(json.dumps({'status':'blocked','reason':reason}),flush=True);return 2


if __name__=='__main__': raise SystemExit(main())
