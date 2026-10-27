import importlib.util
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import time
import shutil
import subprocess
import io
import contextlib
import argparse
import types
import http.client
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location('reel_worker', Path(__file__).parents[1] / 'scripts/reel-worker.py')
w = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = w
SPEC.loader.exec_module(w)
INSTALL = importlib.util.spec_from_file_location('reel_install', Path(__file__).parents[1] / 'scripts/install-reel-worker.py')
installer = importlib.util.module_from_spec(INSTALL); INSTALL.loader.exec_module(installer)


class ReelTests(unittest.TestCase):
    def test_only_explicit_facebook_video_sources(self):
        for url in ['https://www.facebook.com/share/v/19nviGfKhY/', 'https://www.facebook.com/reel/901272619418603/']:
            self.assertEqual(w.source_url(url), url)
        for url in ['http://www.facebook.com/reel/123/', 'https://facebook.com.evil.test/reel/123/', 'https://u:p@www.facebook.com/reel/123/', 'https://www.facebook.com:444/reel/123/', 'file:///tmp/a', 'https://www.facebook.com/settings', 'https://127.0.0.1/reel/123/', 'https://www.facebook.com/reel/../123/']:
            with self.subTest(url=url), self.assertRaises(w.Blocked):
                w.source_url(url)
        for suffix in ['?mibextid=fixture&rdid=fixture','#fragment','?next=https://evil.test#fragment']:
            self.assertEqual(w.source_url('https://www.facebook.com/reel/123/'+suffix),'https://www.facebook.com/reel/123/')

    def test_media_url_and_dns_are_public(self):
        with patch.object(w.socket, 'getaddrinfo', return_value=[(2, 1, 6, '', ('127.0.0.1', 443))]):
            with self.assertRaises(w.Blocked): w.check_remote('https://video.xx.fbcdn.net/v.mp4', media=True)
        for url in ['https://evil.test/v.mp4', 'https://video.fbcdn.net:444/a', 'https://user@video.fbcdn.net/a']:
            with self.assertRaises(w.Blocked): w.check_remote(url, media=True)

    def test_actual_media_peer_is_public_before_body_read(self):
        for address,accepted in [('127.0.0.1',False),('192.168.50.67',False),('8.8.8.8',True)]:
            connection=types.SimpleNamespace(getpeername=lambda:(address,443))
            response=types.SimpleNamespace(fp=types.SimpleNamespace(raw=types.SimpleNamespace(_sock=connection)))
            if accepted:w.check_response_peer(response)
            else:
                with self.assertRaises(w.Blocked):w.check_response_peer(response)
        with self.assertRaises(w.Blocked):w.check_response_peer(types.SimpleNamespace())

    def test_only_mini_role_allows_the_verified_fake_ip_transport(self):
        with tempfile.TemporaryDirectory(dir='/private/tmp') as d:
            for role,allowed in [('mini',True),('home',False)]:
                with patch.object(w,'config',return_value={'role':role}),patch.object(w,'arguments',return_value=argparse.Namespace(action='version')),patch.object(w,'own_home',return_value=Path(d)),contextlib.redirect_stdout(io.StringIO()):self.assertEqual(w.main(),0)
                response=types.SimpleNamespace(fp=types.SimpleNamespace(raw=types.SimpleNamespace(_sock=types.SimpleNamespace(getpeername=lambda:('198.18.1.2',443)))))
                if allowed:w.check_response_peer(response)
                else:
                    with self.assertRaises(w.Blocked):w.check_response_peer(response)
                for address in ['127.0.0.1','192.168.50.67','10.0.0.1','169.254.1.1']:
                    self.assertFalse(w.media_address_allowed(address))
                with self.assertRaises(w.Blocked):w.source_url('https://198.18.1.2/reel/123/')

    def test_landscape_and_progressive_hd_formats_are_supported(self):
        video={'vcodec':'h264','acodec':'none','width':1280,'height':720,'tbr':100}
        audio={'vcodec':'none','acodec':'aac','abr':50}
        self.assertEqual(w.choose_formats([video,audio]),(video,audio,False))
        hd={'vcodec':'h264','acodec':'aac','width':1920,'height':1080,'tbr':100}
        self.assertEqual(w.choose_formats([hd]),(hd,None,True))

    def test_manifest_binding_corruption_escape_and_symlink(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); (root/'source.mp4').write_bytes(b'video')
            m = {'files': [{'name': 'source.mp4', 'bytes': 5, 'sha256': w.sha(root/'source.mp4')}]}
            w.write_json(root/'manifest.json', m)
            expected = w.sha(root/'manifest.json')
            self.assertEqual(w.verify(root, expected), m)
            with self.assertRaises(w.Blocked): w.verify(root, '0'*64)
            (root/'source.mp4').write_bytes(b'wrong')
            with self.assertRaises(w.Blocked): w.verify(root, expected)
            for name in ['../outside', '/etc/passwd', 'a/../../x']:
                with self.assertRaises(w.Blocked): w.child(root, name)
            (root/'link').symlink_to('/etc/passwd')
            with self.assertRaises(w.Blocked): w.child(root, 'link')

    def test_run_ids_cannot_choose_arbitrary_paths(self):
        self.assertEqual(w.run_video_id('reel-123-20261027T000000Z-aabbccdd'), '123')
        for x in ['../x', 'reel-123-a', '/tmp/x', 'reel-123-20261027T000000Z-aabbccdd/../../']:
            with self.assertRaises(w.Blocked): w.run_video_id(x)

    def test_budget_includes_partials_and_reserve(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d); (p/'old.part').write_bytes(b'x'*90)
            with self.assertRaises(w.Blocked): w.check_budget(p, maximum=100, reserve=20)

    def test_chunk_schedule_has_no_unprocessed_audio(self):
        chunks = w.chunk_schedule(600)
        self.assertEqual(chunks[0][0], 0)
        self.assertEqual(chunks[-1][0]+chunks[-1][1], 600)
        self.assertTrue(all(a[0]+a[1] >= b[0] for a,b in zip(chunks,chunks[1:])))

    def test_timestamp_offset_and_invalid_output(self):
        r = {'text': 'hello', 'segments':[{'start':0, 'end':2, 'text':'hello'}]}
        self.assertEqual(w.segments(r, 235)[0]['start'], 235)
        for bad in [{}, {'text':'', 'segments':[]}, {'text':'x','segments':[{'start':3,'end':2,'text':'x'}]}]:
            with self.assertRaises(w.Blocked): w.segments(bad, 0)
        self.assertEqual(w.segments({'text':'','segments':[]},0,allow_empty=True),[])
        for response in [{'text':None},{'text':'  ','segments':None},{'text':'','segments':[]}]:
            self.assertEqual(w.segments(response,0,allow_empty=True),[])
        for response in [{},{'text':'actual words','segments':[]},{'text':3,'segments':None}]:
            with self.assertRaises(w.Blocked):w.segments(response,0,allow_empty=True)

    def test_transcription_request_has_correct_auth_scheme_without_network(self):
        requests=[]
        class Response:
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def read(self,n):return b'{"text":"hello","segments":[{"start":0,"end":1,"text":"hello"}]}'
        class Opener:
            def open(self,request,timeout):requests.append(request);return Response()
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'audio.flac';p.write_bytes(b'test')
            with patch.object(w,'groq_key',return_value='fixture_key'),patch.object(w.urllib.request,'build_opener',return_value=Opener()):w.transcribe(p)
        header=requests[0].get_header('Authorization').encode()
        self.assertEqual(header.split()[0],bytes((66,101,97,114,101,114)))
        self.assertEqual(header.split()[1],b'fixture_key')
        self.assertEqual(requests[0].get_header('User-agent'),'K2B-Reel/1')

    def test_transcription_http_failure_reports_only_numeric_status(self):
        with tempfile.TemporaryDirectory(dir='/private/tmp') as d:
            audio=Path(d)/'audio.flac';audio.write_bytes(b'fixture')
            error=w.urllib.error.HTTPError('https://api.groq.com/openai/v1/audio/transcriptions',403,'secret request detail',{},None)
            opener=types.SimpleNamespace(open=lambda *a,**kw:(_ for _ in ()).throw(error))
            with patch.object(w,'groq_key',return_value='fixture_key'),patch.object(w.urllib.request,'build_opener',return_value=opener):
                with self.assertRaisesRegex(w.Blocked,'^transcription_http_403$'):w.transcribe(audio)

    def test_role_guards_reject_cross_host_actions(self):
        for role,action,reason in [('home','capture','capture_requires_mini'),('mini','process','process_requires_home'),('mini','save-digest','save_requires_home')]:
            with tempfile.TemporaryDirectory(dir='/private/tmp') as d:
                args=argparse.Namespace(action=action,url='https://www.facebook.com/reel/123/',run_id='reel-123-20261027T000000Z-aabbccdd',manifest_sha256='0'*64)
                output=io.StringIO()
                with patch.object(w,'arguments',return_value=args),patch.object(w,'config',return_value={'role':role}),patch.object(w,'own_home',return_value=Path(d)),contextlib.redirect_stdout(output):
                    self.assertEqual(w.main(),2)
                self.assertEqual(json.loads(output.getvalue())['reason'],reason)

    def test_http_failure_returns_json_without_raw_partial_response(self):
        with tempfile.TemporaryDirectory(dir='/private/tmp') as d:
            args=argparse.Namespace(action='capture',url='https://www.facebook.com/reel/123/')
            output=io.StringIO()
            with patch.object(w,'arguments',return_value=args),patch.object(w,'config',return_value={'role':'mini'}),patch.object(w,'own_home',return_value=Path(d)),patch.object(w,'capture',side_effect=http.client.IncompleteRead(b'secret response')),contextlib.redirect_stdout(output):
                self.assertEqual(w.main(),2)
            self.assertEqual(json.loads(output.getvalue()),{'status':'blocked','reason':'IncompleteRead'})
            self.assertNotIn('secret',output.getvalue())

    def test_exact_nas_mount_required(self):
        with patch.object(w.subprocess, 'check_output', return_value=''):
            with self.assertRaises(w.Blocked): w.mount_check(Path('/Volumes/k2b-captures'), 'mini')
        with patch.object(w.subprocess, 'check_output', return_value='//k2b-capture-mini@192.168.50.67/k2b-captures on /Volumes/k2b-captures (smbfs, nodev)'):
            w.mount_check(Path('/Volumes/k2b-captures'), 'mini')
        with patch.object(w.subprocess, 'check_output', return_value='//cheung@evil192.168.50.67/k2b-captures on /Volumes/k2b-captures (smbfs, nodev)'):
            with self.assertRaises(w.Blocked):w.mount_check(Path('/Volumes/k2b-captures'),'home')

    def test_installer_security_gates_are_explicit(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'worker.py';p.write_text('hello')
            digest=w.sha(p)
            self.assertEqual(installer.validate(p,'home',digest,'keithmbpm2'),digest)
            with self.assertRaises(SystemExit):installer.validate(p,'home','0'*64,'keithmbpm2')
            with self.assertRaises(SystemExit):installer.validate(p,'mini',digest,'keithmbpm2')

    def test_installer_atomic_write_rejects_symlink_and_rolls_back_rule_failure(self):
        with tempfile.TemporaryDirectory(dir='/private/tmp') as d:
            home=Path(d);victim=home/'victim';victim.write_bytes(b'unchanged');link=home/'link';link.symlink_to(victim)
            with self.assertRaises(SystemExit):installer.atomic_write(link,b'wrong',0o600)
            self.assertEqual(victim.read_bytes(),b'unchanged')
            launcher=home/'.local/libexec/k2b-reel-v1';launcher.parent.mkdir(parents=True);launcher.write_bytes(b'old launcher')
            rule=home/'.codex/rules/k2b-reel-v1.rules';rule.parent.mkdir(parents=True);rule.write_bytes(b'old rules')
            digest=w.sha(Path(installer.__file__).with_name('reel-worker.py'));actual=installer.atomic_write
            def write(path,data,mode):
                if path==rule and data!=b'old rules':raise OSError('simulated rule write failure')
                return actual(path,data,mode)
            account=types.SimpleNamespace(pw_dir=str(home),pw_name='keithmbpm2')
            with patch.object(installer.pwd,'getpwuid',return_value=account),patch.object(Path,'chmod'),patch.object(installer.subprocess,'check_output',return_value=json.dumps({'worker_sha256':digest})) as version,patch.object(installer,'atomic_write',side_effect=write):
                with self.assertRaises(OSError):installer.install('home',digest)
                self.assertEqual(version.call_args.kwargs['timeout'],10)
            self.assertEqual(launcher.read_bytes(),b'old launcher');self.assertEqual(rule.read_bytes(),b'old rules')

    def test_credential_is_not_shell_executed(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'env'; p.write_text('GROQ_API_KEY="secret"\nOTHER=$(touch /tmp/should-never-run)\n'); p.chmod(0o600)
            self.assertEqual(w.groq_key(p), 'secret')
            p.chmod(0o644)
            with self.assertRaises(w.Blocked): w.groq_key(p)

    def test_cli_rejects_unknown_modes_and_extra_arguments(self):
        with self.assertRaises(SystemExit): w.arguments(['capture','https://www.facebook.com/reel/123/','--output','/tmp/anything'])
        with self.assertRaises(SystemExit): w.arguments(['exec','rm'])

    def test_save_digest_stops_before_creating_wrong_volume_paths(self):
        with tempfile.TemporaryDirectory() as d:
            nas=Path(d)/'missing-nas'
            with patch.object(w,'NAS',nas),patch.object(w,'mount_check',side_effect=w.Blocked('nas_not_mounted')):
                with self.assertRaises(w.Blocked):w.save_digest('reel-123-20261027T000000Z-aabbccdd','0'*64,Path(d))
            self.assertFalse(nas.exists())

    def test_metadata_warnings_do_not_corrupt_json(self):
        with tempfile.TemporaryDirectory() as d:
            result=w.command([sys.executable,'-I','-c','import sys;print("warning",file=sys.stderr);print("{\\"id\\":123}")'],Path(d),time.monotonic()+5)
            self.assertEqual(json.loads(result),{'id':123})

    def test_failure_has_diagnostics_without_sensitive_tokens(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            with self.assertRaises(w.Blocked):
                w.command([sys.executable,'-I','-c','import sys;print("HTTP 403 https://video.fbcdn.net/file?token=secret",file=sys.stderr);sys.exit(1)'],root,time.monotonic()+5)
            failure=json.loads(next(root.glob('failure-*.json')).read_text())
            self.assertIn('403',failure['stderr_redacted'])
            self.assertNotIn('secret',failure['stderr_redacted'])
            self.assertNotIn('Bearer',w.redact('Authorization: Bearer secret'))

    def test_child_output_limit_and_diagnostic_tail(self):
        with tempfile.TemporaryDirectory(dir='/private/tmp') as d:
            root=Path(d);started=time.monotonic()
            with self.assertRaisesRegex(w.Blocked,'command_output_limit'):
                w.command([sys.executable,'-I','-c','import sys,time;sys.stderr.write("x"*131072);sys.stderr.flush();time.sleep(3)'],root,time.monotonic()+5,limit=65536)
            self.assertLess(time.monotonic()-started,2)
            with self.assertRaises(w.Blocked):
                w.command([sys.executable,'-I','-c','import sys;sys.stderr.write("front marker\\n"+"x"*9000+"\\ntail diagnostic marker\\n");sys.exit(1)'],root,time.monotonic()+5)
            failure=json.loads(next(root.glob('failure-*.json')).read_text())
            self.assertIn('tail diagnostic marker',failure['stderr_redacted']);self.assertNotIn('front marker',failure['stderr_redacted'])

    def test_operation_timeout_interrupts_a_blocked_operation(self):
        with self.assertRaisesRegex(w.Blocked,'run_deadline'):
            with w.operation_timeout(.03):time.sleep(.5)

    def test_nas_parent_symlink_is_rejected(self):
        with tempfile.TemporaryDirectory(dir='/private/tmp') as d:
            root=Path(d);nas=root/'nas';nas.mkdir();outside=root/'outside';outside.mkdir();(nas/'facebook').symlink_to(outside)
            with patch.object(w,'NAS',nas),self.assertRaisesRegex(w.Blocked,'symlink_nas_parent'):w.nas_run('reel-123-20261027T000000Z-aabbccdd')

    def test_inventory_keeps_other_runs_visible_when_one_has_symlink(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);one=root/'reel-123-20261027T000000Z-aabbccdd';two=root/'reel-456-20261027T000000Z-aabbccdd'
            one.mkdir();two.mkdir();(one/'link').symlink_to('/etc/passwd');(two/'source').write_text('safe')
            entries=w.inventory(root)['local_runs'];self.assertEqual(len(entries),2)
            self.assertTrue(any(x.get('warning')=='symlink_in_run' for x in entries))
            link=root/'reel-789-20261027T000000Z-aabbccdd';link.symlink_to('/missing')
            self.assertTrue(any(x.get('warning')=='symlink_run_dir' for x in w.inventory(root)['local_runs']))

    def test_partial_transfer_resumes_without_overwrite(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d).resolve()/'local'/'reel-123-20261027T000000Z-aabbccdd';root.mkdir(parents=True)
            (root/'source.mp4').write_bytes(b'video');(root/'run-marker.txt').write_text('marker')
            digest=w.payload_manifest(root,{'run_id':root.name})
            nas=Path(d).resolve()/'nas';dest=nas/'facebook'/'123'/root.name;dest.mkdir(parents=True)
            (dest/'source.mp4').write_bytes(b'video')
            with patch.object(w,'NAS',nas),patch.object(w,'mount_check'),patch.object(w,'space_check'):
                result=w.transfer(root,digest)
                self.assertEqual(result['status'],'captured')
                self.assertTrue((dest/'manifest.json').is_file())
                (dest/'source.mp4').write_bytes(b'other')
                with self.assertRaises(w.Blocked):w.transfer(root,digest)

    def test_missing_replay_run_invalidates_cache_but_corruption_stops(self):
        with tempfile.TemporaryDirectory(dir='/private/tmp') as d:
            state=Path(d);run='reel-123-20261027T000000Z-aabbccdd';cache=state/'source-123.json'
            r={'fingerprint':'test','captured_at':time.time(),'run_id':run,'manifest_sha256':'0'*64}
            w.write_json(cache,r);self.assertIsNone(w.reusable_capture(cache,'test',state));self.assertFalse(cache.exists())
            (state/run).mkdir();w.write_json(cache,r)
            with self.assertRaises((w.Blocked,OSError)):w.reusable_capture(cache,'test',state)

    def test_interrupted_nas_copy_preserves_partial_and_retries_atomically(self):
        with tempfile.TemporaryDirectory(dir='/private/tmp') as d:
            root=Path(d);source=root/'original';source.write_bytes(b'complete original')
            run='reel-123-20261027T000000Z-aabbccdd';state=root/'state';(state/run).mkdir(parents=True)
            nas=root/'nas';dest=nas/'facebook'/'123'/run;dest.mkdir(parents=True);target=dest/'source.mp4'
            real=Path.open
            class InterruptedReader:
                def __enter__(self):return self
                def __exit__(self,*args):pass
                def read(self,n):
                    if hasattr(self,'used'):raise OSError('simulated disconnect')
                    self.used=True;return b'complete'
            def opened(path,*args,**kwargs):return InterruptedReader() if path==source and args==('rb',) else real(path,*args,**kwargs)
            digest=w.sha(source)
            with patch.object(Path,'open',opened):
                with self.assertRaises(w.Blocked):w.publish_file(source,target,digest,dest)
            self.assertFalse(target.exists())
            partial=next((dest/'partials').iterdir());self.assertEqual(partial.read_bytes(),b'complete')
            w.publish_file(source,target,digest,dest)
            self.assertEqual(target.read_bytes(),source.read_bytes());self.assertTrue(partial.exists())
            with patch.object(w,'NAS',nas),patch.object(w,'mount_check'):
                self.assertEqual(w.inventory(state,'mini')['nas_partials'][0]['path'],str(partial))
            target.write_bytes(b'corrupt committed data')
            with self.assertRaises(w.Blocked):w.publish_file(source,target,digest,dest)

    @unittest.skipUnless(Path('/opt/homebrew/bin/ffmpeg').exists(),'requires existing FFmpeg')
    def test_real_prepare_interrupt_replay_recovery_and_save(self):
        with tempfile.TemporaryDirectory(dir='/private/tmp') as d:
            r=Path(d);nas=r/'nas';state=r/'state';state.mkdir()
            run='reel-123-20261027T000000Z-aabbccdd';src=nas/'facebook'/'123'/run;src.mkdir(parents=True)
            subprocess.run(['/opt/homebrew/bin/ffmpeg','-v','error','-f','lavfi','-i','color=c=blue:s=160x240:r=10:d=2','-f','lavfi','-i','sine=frequency=440:duration=2','-c:v','libx264','-c:a','aac','-shortest',str(src/'source.mp4')],check=True)
            subprocess.run(['/opt/homebrew/bin/ffmpeg','-v','error','-i',str(src/'source.mp4'),'-vn','-ar','16000','-ac','1',str(src/'speech.flac')],check=True)
            (src/'run-marker.txt').write_text('marker')
            digest=w.payload_manifest(src,{'run_id':run,'video_id':'123','duration':2.,'marker_sha256':w.sha(src/'run-marker.txt')})
            local=state/run;local.mkdir();(local/'chunk-000.flac').write_bytes(b'fLaCbad')
            with patch.object(w,'NAS',nas),patch.object(w,'mount_check'),patch.object(w,'space_check'),patch.object(w,'transcribe',return_value={'text':'hello','segments':[{'start':0.,'end':1.8,'text':'hello'}]}) as t:
                with patch.object(w,'payload_manifest',side_effect=w.Blocked('simulated_stop')):
                    with self.assertRaises(w.Blocked):w.process(run,digest,state)
                before=sorted(local.glob('sheet-*.jpg'))
                with patch.object(w,'persist_home',side_effect=w.Blocked('simulated publication interruption')):
                    with self.assertRaises(w.Blocked):w.process(run,digest,state)
                with self.assertRaisesRegex(w.Blocked,'home_publication_pending'):w.read_status(run,state,'home')
                first=w.process(run,digest,state)
                self.assertTrue(w.read_status(run,state,'home')['nas_publication_verified'])
                self.assertEqual(before,sorted(local.glob('sheet-*.jpg')))
                self.assertTrue(w.process(run,digest,state)['reused']);self.assertEqual(t.call_count,1)
                self.assertTrue(any((local/'partials').rglob('chunk-000.flac')))
                # A committed audio chunk cannot be silently regenerated.
                chunk=local/'chunk-000.flac';original=chunk.read_bytes();chunk.write_bytes(b'fLaCbad')
                with self.assertRaises(w.Blocked):w.ensure_audio_chunk(src/'speech.flac',chunk,0,2,local,time.monotonic()+5,True)
                chunk.write_bytes(original)
                fakehome=r/'home';draft=fakehome/'Projects/K2B/output/reel-digests'/f'{run}.md';draft.parent.mkdir(parents=True)
                draft.write_text(f'Source run: {run}\nSource manifest: {digest}\nDigest of the test source.\n')
                with patch.object(w,'own_home',return_value=fakehome):
                    self.assertEqual(w.save_digest(run,digest,state)['status'],'digest_saved')
                preserved=w.recover_local(run,digest,state,'home');self.assertTrue(Path(preserved['preserved_path']).is_dir())
                w.process(run,digest,state);self.assertEqual(t.call_count,2)

    @unittest.skipUnless(Path('/opt/homebrew/bin/ffmpeg').exists(),'requires existing FFmpeg')
    def test_capture_cache_commit_and_failed_transfer_resume(self):
        with tempfile.TemporaryDirectory(dir='/private/tmp') as d:
            r=Path(d);state=r/'state';state.mkdir();nas=r/'nas';nas.mkdir()
            video=r/'fixture.mp4';audio=r/'fixture.m4a'
            subprocess.run(['/opt/homebrew/bin/ffmpeg','-v','error','-f','lavfi','-i','color=c=blue:s=160x240:r=10:d=2','-f','lavfi','-i','sine=frequency=440:duration=2','-c:v','libx264','-c:a','aac','-shortest',str(video)],check=True)
            subprocess.run(['/opt/homebrew/bin/ffmpeg','-v','error','-i',str(video),'-vn','-c:a','copy',str(audio)],check=True)
            metadata={'id':'123','duration':2,'title':'fixture','uploader':'test','formats':[{'format_id':'v','vcodec':'h264','acodec':'none','width':160,'height':240,'url':'https://video.xx.fbcdn.net/v'},{'format_id':'a','vcodec':'none','acodec':'aac','url':'https://video.xx.fbcdn.net/a'}]}
            actual=w.command
            def cmd(args,*a,**k):return json.dumps(metadata).encode() if args[0]==w.TOOLS['yt-dlp'] else actual(args,*a,**k)
            def dl(url,dest,*a):shutil.copyfile(video if url.endswith('/v') else audio,dest)
            with patch.object(w,'NAS',nas),patch.object(w,'mount_check'),patch.object(w,'space_check'),patch.object(w,'command',side_effect=cmd),patch.object(w,'download',side_effect=dl) as downloads:
                with patch.object(w,'transfer',side_effect=w.Blocked('simulated_nas_offline')):
                    with self.assertRaises(w.Blocked):w.capture('https://www.facebook.com/reel/123/',state)
                cache=json.loads((state/'source-123.json').read_text());w.verify(state/cache['run_id'],cache['manifest_sha256'])
                result=w.capture('https://www.facebook.com/reel/123/',state)
                self.assertTrue(result['reused']);self.assertEqual(downloads.call_count,2)
                held=state/'quarantine';held.mkdir(exist_ok=True);(state/result['run_id']).rename(held/result['run_id'])
                fresh=w.capture('https://www.facebook.com/reel/123/',state)
                self.assertNotEqual(fresh['run_id'],result['run_id']);self.assertEqual(downloads.call_count,4)
                failed_state=r/'failed-acquisition';failed_state.mkdir()
                def interrupted(url,dest,*args):
                    if url.endswith('/a'):raise OSError('simulated audio disconnect')
                    return dl(url,dest,*args)
                with patch.object(w,'download',side_effect=interrupted):
                    with self.assertRaises(OSError):w.capture('https://www.facebook.com/reel/123/',failed_state)
                incomplete=next(failed_state.glob('reel-*'));self.assertTrue((incomplete/'video.mp4').is_file())
                reacquired=w.capture('https://www.facebook.com/reel/123/',failed_state)
                self.assertNotEqual(reacquired['run_id'],incomplete.name);self.assertTrue((incomplete/'video.mp4').is_file())

    @unittest.skipUnless(Path('/opt/homebrew/bin/ffmpeg').exists(),'requires existing FFmpeg')
    def test_visual_checkpoint_resumes_completed_sheets(self):
        with tempfile.TemporaryDirectory(dir='/private/tmp') as d:
            root=Path(d);source=root/'source.mp4'
            colors='drawbox=x=0:y=0:w=iw:h=ih:color=red:t=fill:enable=gte(t,16),drawbox=x=0:y=0:w=iw:h=ih:color=green:t=fill:enable=gte(t,17)'
            colors=colors.replace('gte(t,16)','gte(t\\,16)').replace('gte(t,17)','gte(t\\,17)')
            subprocess.run(['/opt/homebrew/bin/ffmpeg','-v','error','-f','lavfi','-i','color=c=blue:s=160x240:r=10:d=18','-vf',colors,'-c:v','libx264',str(source)],check=True)
            actual=w.command;calls=[]
            def interrupted(args,*a,**kw):
                calls.append(args[-1])
                if args[-1]==str(root/'sheet-002.jpg'):raise w.Blocked('simulated visual interruption')
                return actual(args,*a,**kw)
            with patch.object(w,'command',side_effect=interrupted):
                with self.assertRaises(w.Blocked):w.prepare_visuals(source,root,18.,'0'*64,time.monotonic()+20)
            first_hash=w.sha(root/'sheet-001.jpg');calls.clear()
            def resumed(args,*a,**kw):calls.append(args[-1]);return actual(args,*a,**kw)
            with patch.object(w,'command',side_effect=resumed):coverage=w.prepare_visuals(source,root,18.,'0'*64,time.monotonic()+20)
            self.assertNotIn(str(root/'sheet-001.jpg'),calls);self.assertEqual(w.sha(root/'sheet-001.jpg'),first_hash)
            self.assertEqual(len(coverage['sheets']),2)
            last=coverage['sheets'][-1]
            self.assertEqual((last['frame_count'],last['padding_cells'],last['sample_seconds']),(2,14,[16,17]))
            def pixel(x):
                return subprocess.check_output(['/opt/homebrew/bin/ffmpeg','-v','error','-i',str(root/'sheet-002.jpg'),'-vf',f'crop=320:480:{x}:2,scale=1:1:flags=area','-frames:v','1','-pix_fmt','rgb24','-f','rawvideo','-'])
            red,green=pixel(2),pixel(324)
            self.assertGreater(red[0],200);self.assertLess(red[1],50)
            self.assertGreater(green[1],80);self.assertLess(green[0],50)


if __name__ == '__main__': unittest.main()
