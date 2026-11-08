from test_video_extract import load
import pytest


def test_source_hash_isolates_preparations_and_chunk_schedule_preserved(tmp_path):
    v = load()
    assert v.preparation_state(tmp_path,'a'*64) != v.preparation_state(tmp_path,'b'*64)
    assert v.legacy.chunk_schedule(480) == [(0,240),(235,240),(470,10)]


def test_wrong_sjm_mount_rejected(monkeypatch,tmp_path):
    v = load()
    monkeypatch.setattr(v.subprocess,'check_output',lambda *a,**k:'//cheung@192.168.50.67/k2b-captures on '+str(tmp_path)+' (smbfs, nodev)')
    with pytest.raises(v.Blocked): v.mount_check(tmp_path,'sjm')


def test_missing_publication_is_not_completed(tmp_path,monkeypatch):
    v = load(); v.CURRENT = {'role':'home'}
    monkeypatch.setattr(v,'mount_check',lambda *a:None)
    monkeypatch.setattr(v,'verify',lambda *a:{})
    monkeypatch.setattr(v,'nas_run',lambda *a:tmp_path/'nas')
    with pytest.raises(v.Blocked): v.read_status('youtube-bc7QaDmLmoA-20261108T000000Z-abcdef01','a'*64,tmp_path)


def test_empty_words_remain_unverified_and_invalid_segments_fail():
    v = load()
    assert v.legacy.segments({'text':None,'segments':[]},0,True) == []
    with pytest.raises(v.Blocked): v.legacy.segments({'text':'words','segments':[]},0,True)


@pytest.mark.parametrize('duration,audio_duration,missing_video_duration',[(2,2,False),(260,260,False),(4,2,False),(2,2,True)])
def test_real_media_preparation_reuses_successful_provider_chunks(tmp_path,monkeypatch,duration,audio_duration,missing_video_duration):
    import subprocess
    v = load(); nas = tmp_path/'nas'; nas.mkdir()
    monkeypatch.setattr(v,'NAS',nas)
    monkeypatch.setattr(v,'mount_check',lambda *a:None)
    state = tmp_path/'state'; state.mkdir(); v.configure({'role':'home'},'youtube',state)
    run = 'youtube-bc7QaDmLmoA-20261108T000000Z-abcdef01'
    src = v.nas_run(run); src.mkdir(parents=True)
    subprocess.run(['/opt/homebrew/bin/ffmpeg','-nostdin','-v','error','-f','lavfi','-i',f'color=c=blue:s=320x240:r=2:d={duration}',
        '-f','lavfi','-i',f'sine=frequency=440:duration={audio_duration}','-c:v','libx264','-c:a','aac',str(src/'source.mp4')],check=True)
    subprocess.run(['/opt/homebrew/bin/ffmpeg','-nostdin','-v','error','-i',str(src/'source.mp4'),'-ac','1','-ar','16000',str(src/'speech.flac')],check=True)
    for name in ('acquisition.json','native-source.json'): (src/name).write_text('{}')
    (src/'run-marker.txt').write_text('random-marker')
    digest = v.payload_manifest(src,{'platform':'youtube','run_id':run,'video_id':'bc7QaDmLmoA','source_duration':duration,'duration':duration,
        'method':'direct_media','marker_sha256':v.sha(src/'run-marker.txt')})
    calls = []
    def provider(path):
        calls.append(v.sha(path)); return {'text':'fixture speech','segments':[{'start':0,'end':1.9,'text':'fixture speech'}]}
    monkeypatch.setattr(v.legacy,'transcribe',provider)
    if missing_video_duration:
        probe = v.legacy.probe
        def without_video_duration(*args,**kwargs):
            result = probe(*args,**kwargs)
            for stream in result.get('streams',[]):
                if stream.get('codec_type') == 'video':stream.pop('duration',None)
            return result
        monkeypatch.setattr(v.legacy,'probe',without_video_duration)
    first = v.process(run,digest,state)
    expected = len(v.legacy.chunk_schedule(audio_duration))
    assert first['processing_host'] == 'home' and len(calls) == expected
    assert first['speech_duration'] == pytest.approx(audio_duration,abs=.25)
    if missing_video_duration:assert first['video_duration'] is None
    if audio_duration < duration:
        assert any(g['reason'] == 'no_audio_stream_in_media_range' for g in first['audio_gaps'])
    again = v.process(run,digest,state)
    assert again['reused'] is True and len(calls) == expected
    assert v.read_status(run,digest,state)['nas_publication_verified'] is True
    root = v.preparation_state(state,digest)/run
    receipt = v.read_json(root/'prepared.json'); receipt['duration'] = duration+100
    v.write_json(root/'prepared.json',receipt)
    destination = v.nas_run(run)/'prepared-home'/receipt['prepared_manifest_sha256']
    v.write_json(destination/'prepared.json',receipt)
    with pytest.raises(v.Blocked): v.read_status(run,digest,state)


def test_processing_budget_scales_by_audio_and_visual_work():
    v = load()
    assert v.processing_budget(3600) > 15*90 + 225*10
    assert v.processing_budget(3600) > v.processing_budget(1200) > v.processing_budget(45)


def test_preparation_digest_rejected_before_directory_creation(tmp_path):
    v = load()
    with pytest.raises(v.Blocked): v.preparation_state(tmp_path,'../escape')
    assert list(tmp_path.iterdir()) == []


def test_inventory_includes_nested_preparation_and_failed_partials(tmp_path):
    v = load(); run = 'youtube-bc7QaDmLmoA-20261108T000000Z-abcdef01'
    p = v.preparation_state(tmp_path,'a'*64)/run/'partials'; p.mkdir(parents=True)
    (p/'failed').write_bytes(b'failed')
    report = v.inventory(tmp_path,'home')
    assert report['local_runs'][0]['run_id'] == run
    assert report['local_runs'][0]['bytes'] == 6
    assert report['byte_budget_scope'] == 'active_operation_local_plus_nas'
    assert report['retained_archive_bytes']['local_runs_and_preflights'] == 6
    assert report['retained_archive_bytes']['other_hosts_private_copies'] == 'not_measured'


def test_source_timing_cannot_certify_a_prefix():
    v = load(); source = {'platform':'youtube','source_duration':60,'duration':60,'method':'direct_media'}
    assert v.validate_source_timing(source,60,60,audio_stream_duration=60)
    with pytest.raises(v.Blocked): v.validate_source_timing(dict(source,duration=30),60,60,audio_stream_duration=60)
    with pytest.raises(v.Blocked): v.validate_source_timing(source,60,30,audio_stream_duration=60)
    with pytest.raises(v.Blocked): v.validate_source_timing(dict(source,method='playback_recording',duration=181),181,181,audio_stream_duration=181)


def test_source_timing_distinguishes_a_naturally_shorter_audio_track():
    v = load(); source = {'platform':'youtube','source_duration':60,'duration':60,'method':'direct_media'}
    result = v.validate_source_timing(source,60,30,audio_stream_duration=30)
    assert result['speech_duration'] == 30 and result['media_duration'] == 60
    assert result['audio_absent_ranges'] == [{'start':30,'end':60,'reason':'no_audio_stream_in_media_range'}]
    with pytest.raises(v.Blocked):v.validate_source_timing(source,60,30,audio_stream_duration=60)


def test_inventory_finds_nas_only_partial_and_failure(tmp_path,monkeypatch):
    v = load(); state = tmp_path/'state'; state.mkdir(); nas = tmp_path/'nas'; nas.mkdir()
    monkeypatch.setattr(v,'NAS',nas); monkeypatch.setattr(v,'mount_check',lambda *a:None)
    run = 'youtube-bc7QaDmLmoA-20261108T000000Z-abcdef01'
    p = v.nas_run(run)/'partials'; p.mkdir(parents=True)
    (p/'media.partial-abcd').write_bytes(b'partial'); (p.parent/'failure-abcdef01.json').write_text('{}')
    report = v.inventory(state,'home')
    assert len(report['nas_partials']) == 1 and len(report['failure_receipts']) == 1
    assert report['retained_archive_bytes']['nas_v2_runs'] == 9


def test_publication_counts_local_and_nas_copies_before_copy(tmp_path,monkeypatch):
    v = load(); state = tmp_path/'state'; state.mkdir(); nas = tmp_path/'nas'; nas.mkdir()
    monkeypatch.setattr(v,'NAS',nas); monkeypatch.setattr(v,'mount_check',lambda *a:None)
    v.configure({'role':'home'},'youtube',state)
    run = 'youtube-bc7QaDmLmoA-20261108T000000Z-abcdef01'; source = v.nas_run(run); source.mkdir(parents=True)
    (source/'original.bin').write_bytes(b'x'*(3*1024**2))
    root = state/run; root.mkdir(); (root/'local.bin').write_bytes(b'y'*(3*1024**2))
    digest = v.payload_manifest(root,{'platform':'youtube','run_id':run,'source_manifest_sha256':'a'*64})
    v.write_json(root/'prepared.json',{'prepared_manifest_sha256':digest})
    v.legacy.MAX_BYTES = 10*1024**2
    with pytest.raises(v.Blocked):v.persist_prepared(root,run)
    assert not (source/'prepared-home'/digest/'local.bin').exists()


def test_inventory_includes_recorder_preflight_failures(tmp_path):
    v = load(); p = tmp_path/'preflight-12345678-1234-1234-1234-123456789abc'; p.mkdir()
    (p/'failure-abcdefgh.json').write_text('{}')
    report = v.inventory(tmp_path,'home')
    assert len(report['local_preflights']) == 1
    assert len(report['failure_receipts']) == 1


@pytest.mark.parametrize('interrupted',[False,True])
@pytest.mark.parametrize('audio_tail',[False,True])
def test_sparse_video_samples_hold_the_prior_picture_across_sheet_boundaries(tmp_path,monkeypatch,interrupted,audio_tail):
    import subprocess,time
    v=load();root=tmp_path/'prepared';root.mkdir();source=tmp_path/'sparse.mp4'
    v.configure({'role':'home'},'youtube',root)
    audio_duration=30 if audio_tail else 18
    subprocess.run(['/opt/homebrew/bin/ffmpeg','-nostdin','-v','error','-f','lavfi','-i','color=c=blue:s=320x240:r=1:d=18',
        '-f','lavfi','-i',f'sine=frequency=440:duration={audio_duration}',
        '-vf',"drawbox=color=red:t=fill:enable='gte(t,17)',select='eq(n,0)+eq(n,17)'",'-fps_mode','vfr','-c:v','libx264','-c:a','aac',str(source)],check=True)
    previous = []
    if interrupted:
        command = v.legacy.command
        def fail_after_outputs(args,*a,**kw):
            result = command(args,*a,**kw)
            if str(args[-1]).endswith('sheet-%03d.jpg'):raise v.Blocked('fixture_interrupted_before_visual_commit')
            return result
        monkeypatch.setattr(v.legacy,'command',fail_after_outputs)
        with pytest.raises(v.Blocked,match='fixture_interrupted_before_visual_commit'):
            v.prepare_visuals(source,root,audio_duration,'a'*64,time.monotonic()+60)
        previous = [v.sha(p) for p in root.glob('sheet-*.jpg')]
        assert len(previous)==2 and not (root/'visual-coverage.json').exists()
        monkeypatch.setattr(v.legacy,'command',command)
    coverage=v.prepare_visuals(source,root,audio_duration,'a'*64,time.monotonic()+60)
    if interrupted:
        assert sorted(v.sha(p) for p in (root/'partials').rglob('sheet-*.jpg'))==sorted(previous)
    assert [s['frame_count'] for s in coverage['sheets']]==[16,2]
    assert coverage['sheets'][1]['sample_seconds']==[16,17]
    assert coverage['ending_observed_second']==17
    assert coverage['visual_duration']==18
    assert coverage['unpictured_audio_tail_seconds']==audio_duration-18
    assert max(t for sheet in coverage['sheets'] for t in sheet['sample_seconds'])==17
    assert coverage['sampling_policy']=='full_stream_display_timeline_resampling_with_possible_held_images'
    def pixel(sheet,x,y):
        return subprocess.check_output(['/opt/homebrew/bin/ffmpeg','-nostdin','-v','error','-i',str(root/sheet),
            '-vf',f'crop=2:2:{x}:{y},scale=1:1','-frames:v','1','-f','rawvideo','-pix_fmt','rgb24','-'])
    # The held blue frame must remain blue through16s, not be replaced by the
    # first future red frame from an independently trimmed input segment.
    for rgb in (pixel('sheet-001.jpg',1128,848),pixel('sheet-002.jpg',162,122)):
        assert rgb[2]>rgb[0]+100
    red=pixel('sheet-002.jpg',484,122);assert red[0]>red[2]+100


def test_out_of_chunk_words_are_retained_but_never_clipped_into_source_claims():
    import copy
    v=load();raw={'text':'supported unsupported','segments':[{'start':0,'end':2,'text':'supported'},
        {'start':9,'end':12,'text':'  unsupported  '}]};before=copy.deepcopy(raw)
    words,anomalies=v.classify_transcript(raw,100,10)
    assert raw==before and [s['text'] for s in words]==['supported']
    assert anomalies[0]['raw_segment_index']==1
    assert anomalies[0]['raw_segment']==raw['segments'][1]
    assert anomalies[0]['unverified_audio_range']=={'start':109,'end':110}
    assert v.validate_episode_anomalies(anomalies,100,108,110)['anomaly_intersection_check']=='none_intersect'
    with pytest.raises(v.Blocked,match='source_episode_has_unresolved_provider_anomaly'):
        v.validate_episode_anomalies(anomalies,100,109.5,110)


@pytest.mark.parametrize('segment',[{}, {'start':1,'end':2}, {'start':float('nan'),'end':2,'text':'bad'},
    {'start':1,'end':float('inf'),'text':'bad'}, {'start':2,'end':1,'text':'bad'}, {'start':1,'end':2,'text':None}])
def test_provider_anomaly_classification_never_relaxes_parse_failures(segment):
    v=load()
    with pytest.raises(v.Blocked):v.classify_transcript({'text':'bad','segments':[segment]},0,10)


def test_padding_anomaly_does_not_validate_an_intersecting_source_episode():
    v=load()
    _,padding=v.classify_transcript({'text':'padding','segments':[{'start':20,'end':35,'text':'padding'}]},0,30)
    assert v.validate_episode_anomalies(padding,2,18,30)['anomaly_intersection_check']=='none_intersect'
    _,source=v.classify_transcript({'text':'source','segments':[{'start':7,'end':35,'text':'source'}]},0,30)
    with pytest.raises(v.Blocked,match='source_episode_has_unresolved_provider_anomaly'):
        v.validate_episode_anomalies(source,2,18,30)


def test_preparation_and_status_hash_bind_anomalies_without_provider_retry(tmp_path,monkeypatch):
    import subprocess
    v=load();nas=tmp_path/'nas';nas.mkdir();state=tmp_path/'state';state.mkdir()
    monkeypatch.setattr(v,'NAS',nas);monkeypatch.setattr(v,'mount_check',lambda *a:None);v.configure({'role':'home'},'youtube',state)
    run='youtube-bc7QaDmLmoA-20261108T000000Z-abcdef01';src=v.nas_run(run);src.mkdir(parents=True)
    subprocess.run(['/opt/homebrew/bin/ffmpeg','-nostdin','-v','error','-f','lavfi','-i','color=c=blue:s=320x240:r=2:d=2',
        '-f','lavfi','-i','sine=frequency=440:duration=2','-c:v','libx264','-c:a','aac',str(src/'source.mp4')],check=True)
    subprocess.run(['/opt/homebrew/bin/ffmpeg','-nostdin','-v','error','-i',str(src/'source.mp4'),'-ac','1','-ar','16000',str(src/'speech.flac')],check=True)
    for name in ('acquisition.json','native-source.json'):(src/name).write_text('{}')
    (src/'run-marker.txt').write_text('random-marker')
    digest=v.payload_manifest(src,{'platform':'youtube','run_id':run,'video_id':'bc7QaDmLmoA','source_duration':2,'duration':2,
        'method':'direct_media','marker_sha256':v.sha(src/'run-marker.txt')})
    calls=[]
    def provider(path):
        calls.append(v.sha(path));return {'text':'supported unsupportedword','segments':[{'start':0,'end':1,'text':'supported'},
            {'start':1.5,'end':3,'text':'unsupportedword'}]}
    monkeypatch.setattr(v.legacy,'transcribe',provider)
    first=v.process(run,digest,state);root=v.preparation_state(state,digest)/run
    assert len(calls)==1 and first['transcription_coverage_status']=='unverified_provider_timestamp_anomalies'
    assert 'unsupportedword' not in (root/'transcript.txt').read_text()
    raw=root/'transcript-000.json';before=(v.sha(raw),raw.stat().st_mtime_ns)
    anomaly=first['provider_timestamp_anomalies'][0]
    assert anomaly['response_sha256']==v.sha(raw) and anomaly['raw_segment']['text']=='unsupportedword'
    assert any(g['reason']=='provider_timestamp_outside_submitted_audio_chunk' for g in first['audio_gaps'])
    names={item['name'] for item in v.verify(root,first['prepared_manifest_sha256'])['files']}
    assert {'provider-timestamp-anomalies.json','timeline.json','visual-coverage.json','transcript-000.json'} <= names
    assert v.read_status(run,digest,state)['transcription_coverage_status']=='unverified_provider_timestamp_anomalies'
    assert v.process(run,digest,state)['reused'] is True and len(calls)==1
    assert (v.sha(raw),raw.stat().st_mtime_ns)==before
    with pytest.raises(v.Blocked,match='source_episode_has_unresolved_provider_anomaly'):
        v.validate_episode_anomalies(first['provider_timestamp_anomalies'],0,2,2)
    (root/'provider-timestamp-anomalies.json').write_text('{}')
    with pytest.raises(v.Blocked):v.read_status(run,digest,state)


def test_interruption_between_preserved_sheet_moves_remains_retryable(tmp_path,monkeypatch):
    from pathlib import Path
    import subprocess,time
    v=load();root=tmp_path/'prepared';root.mkdir();source=tmp_path/'sparse.mp4';v.configure({'role':'home'},'youtube',root)
    subprocess.run(['/opt/homebrew/bin/ffmpeg','-nostdin','-v','error','-f','lavfi','-i','color=c=blue:s=320x240:r=1:d=18',
        '-vf',"select='eq(n,0)+eq(n,17)'",'-fps_mode','vfr','-c:v','libx264',str(source)],check=True)
    v.prepare_visuals(source,root,18,'a'*64,time.monotonic()+60)
    old=sorted(v.sha(p) for p in root.glob('sheet-*.jpg'))
    # Model interruption after only one member's valid progress was committed.
    (root/'visual-coverage.json').unlink();progress=v.read_json(root/'visual-progress.json')
    progress['completed'].pop('sheet-002.jpg');progress['sample_seconds'].pop('sheet-002.jpg');v.write_json(root/'visual-progress.json',progress)
    rename=Path.rename;moves=[]
    def interrupt(path,destination):
        if path.parent==root and path.name.startswith('sheet-'):
            moves.append(path.name)
            if len(moves)==2:raise OSError('fixture interruption between preserved moves')
        return rename(path,destination)
    monkeypatch.setattr(Path,'rename',interrupt)
    with pytest.raises(OSError):v.prepare_visuals(source,root,18,'a'*64,time.monotonic()+60)
    monkeypatch.setattr(Path,'rename',rename)
    result=v.prepare_visuals(source,root,18,'a'*64,time.monotonic()+60)
    assert len(result['sheets'])==2
    assert sorted(v.sha(p) for p in (root/'partials').rglob('sheet-*.jpg'))==old


@pytest.mark.parametrize('state',['matching_partial','missing','changed_partial','corrupt_current'])
def test_preexisting_stale_sheet_progress_requires_exact_retained_bytes(tmp_path,state):
    import subprocess,time,shutil
    v=load();root=tmp_path/'prepared';root.mkdir();source=tmp_path/'sparse.mp4';v.configure({'role':'home'},'youtube',root)
    subprocess.run(['/opt/homebrew/bin/ffmpeg','-nostdin','-v','error','-f','lavfi','-i','color=c=blue:s=320x240:r=1:d=18',
        '-vf',"select='eq(n,0)+eq(n,17)'",'-fps_mode','vfr','-c:v','libx264',str(source)],check=True)
    v.prepare_visuals(source,root,18,'a'*64,time.monotonic()+60);old=sorted(v.sha(p) for p in root.glob('sheet-*.jpg'))
    (root/'visual-coverage.json').unlink();progress=v.read_json(root/'visual-progress.json')
    progress['completed'].pop('sheet-002.jpg');progress['sample_seconds'].pop('sheet-002.jpg');v.write_json(root/'visual-progress.json',progress)
    sheet=root/'sheet-001.jpg';partial=v.safe_dir(root/'partials'/'1234abcd')/'sheet-001.jpg'
    if state=='missing':sheet.unlink()
    elif state=='corrupt_current':shutil.copyfile(sheet,partial);sheet.write_bytes(b'corrupt current')
    else:
        sheet.rename(partial)
        if state=='changed_partial':partial.write_bytes(b'changed retained bytes')
    before=v.sha(root/'sheet-002.jpg')
    if state=='matching_partial':
        result=v.prepare_visuals(source,root,18,'a'*64,time.monotonic()+60)
        assert len(result['sheets'])==2
        assert sorted(v.sha(p) for p in (root/'partials').rglob('sheet-*.jpg'))==old
    else:
        with pytest.raises(v.Blocked,match='visual_progress_hash_mismatch'):
            v.prepare_visuals(source,root,18,'a'*64,time.monotonic()+60)
        assert v.sha(root/'sheet-002.jpg')==before and not (root/'visual-coverage.json').exists()
