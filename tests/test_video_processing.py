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
