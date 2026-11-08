from test_video_extract import load
import pytest


def window():
    return {'id':42,'process_id':100,'app':'com.google.Chrome','title':'verified source - YouTube','x':0,'y':0,'width':1000,'height':700}


def test_audio_stream_alone_never_verifies_source_sound():
    v = load()
    result = v.validate_recording({'format':{'duration':'72'},'streams':[{'codec_type':'video'},{'codec_type':'audio'}]},
        {'platform':'facebook','microphone':False,'system_audio':True}, 71)
    assert result['audio_verified'] is False
    assert result['coverage_status'] == 'requires_boundary_and_speech_review'


@pytest.mark.parametrize('actual,streams', [('70',['video','audio']),('193',['video','audio']),('72',['video'])])
def test_incomplete_recordings_fail(actual,streams):
    v = load()
    with pytest.raises(v.Blocked):
        v.validate_recording({'format':{'duration':actual},'streams':[{'codec_type':s} for s in streams]},
            {'platform':'facebook','microphone':False,'system_audio':True},71)


def test_capture_attempts_persist_and_limit_repeated_dispatch(tmp_path):
    v = load(); native = {'request_id':'12345678-1234-1234-1234-123456789abc','canonical_url':'https://www.youtube.com/watch?v=bc7QaDmLmoA','deadline_epoch':12345}
    v.reserve_attempt(tmp_path,native,'direct')
    with pytest.raises(v.Blocked): v.reserve_attempt(tmp_path,native,'direct')
    v.reserve_attempt(tmp_path,native,'record')
    with pytest.raises(v.Blocked): v.reserve_attempt(tmp_path,native,'record')


def test_recording_requires_observed_unmuted_control_and_reset():
    v = load()
    with pytest.raises(v.Blocked): v.recording_ready({'audio_state':'muted'})
    assert v.recording_ready({'audio_state':'unmuted_control_observed','source_reset':'paused_at_zero','ad_state':'source_without_ad',
        'window':window(),'title':'verified source','opening_picture_verified':True,'window_corroborated':True})


def test_native_control_observation_never_claims_hearing():
    v = load(); n = {'audio_state':'unmuted_control_observed','source_reset':'paused_at_zero','ad_state':'source_without_ad',
        'window':window(),'title':'verified source','opening_picture_verified':True,'window_corroborated':True}
    assert v.recording_ready(n)
    for unsupported in ('unmuted_source_sound_observed','audio_playing_indicator','muted'):
        with pytest.raises(v.Blocked):v.recording_ready(dict(n,audio_state=unsupported))


def test_only_committed_acquisition_can_resume_transfer(tmp_path):
    v = load(); run = 'youtube-bc7QaDmLmoA-20261108T000000Z-abcdef01'
    root = tmp_path/run; root.mkdir()
    with pytest.raises(v.Blocked): v.committed_source(root,run)
    (root/'run-marker.txt').write_text('marker')
    d = v.payload_manifest(root,{'platform':'youtube','run_id':run})
    v.write_json(root/'source-commit.json',{'run_id':run,'manifest_sha256':d})
    assert v.committed_source(root,run) == d
    (root/'run-marker.txt').write_text('changed')
    with pytest.raises(v.Blocked): v.committed_source(root,run)


def test_request_deadline_and_source_cannot_be_reset(tmp_path):
    v = load(); n = {'request_id':'12345678-1234-1234-1234-123456789abc','canonical_url':'https://www.youtube.com/watch?v=bc7QaDmLmoA','deadline_epoch':12345}
    v.reserve_attempt(tmp_path,n,'direct')
    with pytest.raises(v.Blocked): v.reserve_attempt(tmp_path,dict(n,deadline_epoch=12346),'record')
    with pytest.raises(v.Blocked): v.reserve_attempt(tmp_path,dict(n,canonical_url='https://www.youtube.com/watch?v=AAAAAAAAAAA'),'record')


def test_readonly_recorder_preflight_also_pins_original_request_deadline(tmp_path):
    v = load(); n = {'request_id':'12345678-1234-1234-1234-123456789abc','canonical_url':'https://www.youtube.com/watch?v=bc7QaDmLmoA','deadline_epoch':12345}
    v.request_receipt(tmp_path,n)
    with pytest.raises(v.Blocked):v.request_receipt(tmp_path,dict(n,deadline_epoch=12346))


@pytest.mark.parametrize('failure',['playback_not_advancing','native_source_evidence_expired','native_source_identity_mismatch'])
def test_failed_native_prerequisite_cannot_refresh_request_deadline(tmp_path,failure):
    import time
    v = load(); now = time.time(); url = 'https://www.youtube.com/watch?v=bc7QaDmLmoA'
    n = {'request_id':'12345678-1234-1234-1234-123456789abc','submitted_url':url,'canonical_url':url,
        'deadline_epoch':now+1000,'verified_epoch':now,'host':'Macmini','user':'fastshower','control':'native_chrome',
        'playback_start':0,'playback_end':2,'duration':60,'title':'source','publisher':'creator'}
    if failure == 'playback_not_advancing':n['playback_end'] = 0
    elif failure == 'native_source_evidence_expired':n['verified_epoch'] = now-300
    else:n['canonical_url'] = 'https://www.youtube.com/watch?v=AAAAAAAAAAA'
    v.write_json(tmp_path/'native-source.json',n)
    with pytest.raises(v.Blocked,match=failure):v.native_source(url,tmp_path)
    receipt = v.read_json(v.request_path(tmp_path,n))
    assert receipt['deadline_epoch'] == n['deadline_epoch'] and receipt['attempted'] == []
    n.update(deadline_epoch=now+1100,verified_epoch=now,playback_end=2)
    v.write_json(tmp_path/'native-source.json',n)
    with pytest.raises(v.Blocked,match='immutable_request_binding_changed'):v.native_source(url,tmp_path)


def test_recovery_rejects_changed_native_source():
    v = load(); old = {'canonical_url':'https://www.youtube.com/watch?v=bc7QaDmLmoA','duration':60,'title':'one','publisher':'creator'}
    assert v.recovery_binding(old,dict(old))
    with pytest.raises(v.Blocked): v.recovery_binding(old,dict(old,title='new source'))
    with pytest.raises(v.Blocked): v.recovery_binding(old,dict(old,duration=61))


def test_recorder_permission_failure_remains_precise(tmp_path):
    import time
    v = load(); tool = tmp_path/'record'
    tool.write_text('#!/bin/sh\nprintf \'%s\\n\' \'{"status":"blocked","reason":"recorder_screen_audio_permission_required"}\' >&2\nexit 2\n')
    tool.chmod(0o700)
    with pytest.raises(v.Blocked,match='recorder_screen_audio_permission_required'):
        v.run_recorder([str(tool)],tmp_path,time.monotonic()+5)


def test_recorder_recheck_binds_actual_id_process_title_and_geometry():
    v = load(); target = window()
    assert v.window_binding(target,dict(target))
    for changed in (dict(target,id=43),dict(target,process_id=101),dict(target,width=1002),dict(target,title='unrelated source')):
        with pytest.raises(v.Blocked):v.window_binding(target,changed)


def test_native_corroboration_handles_real_window_title_ellipsis():
    v = load(); target = dict(window(),title='(12) I Tested Uncensored AI... I…d Probably Be Illegal… - YouTube')
    n = {'audio_state':'unmuted_control_observed','source_reset':'paused_at_zero','ad_state':'source_without_ad',
        'window':target,'title':'I Tested Uncensored AI... It Should Probably Be Illegal…','opening_picture_verified':True,'window_corroborated':True}
    assert v.recording_ready(n)
    with pytest.raises(v.Blocked):v.recording_ready(dict(n,window_corroborated=False))


def test_successful_recorder_json_is_separate_from_benign_stderr(tmp_path):
    import json,time
    v = load(); tool = tmp_path/'record'
    tool.write_text('#!/bin/sh\nprintf \'framework notice\\n\' >&2\nprintf \'%s\\n\' \'{"status":"window_candidate","window":{}}\'\n')
    tool.chmod(0o700)
    result = v.run_recorder([str(tool)],tmp_path,time.monotonic()+5)
    assert json.loads(result)['status'] == 'window_candidate'


def handshake(tmp_path):
    v = load(); now = 1000.0
    n = {'request_id':'12345678-1234-1234-1234-123456789abc','canonical_url':'https://www.youtube.com/watch?v=bc7QaDmLmoA',
        'deadline_epoch':2000,'duration':60,'title':'verified source','window':window()}
    v.CURRENT.update(package='a'*64,recorder_sha256='b'*64)
    ready = {'version':1,'request_id':n['request_id'],'canonical_url':n['canonical_url'],'deadline_epoch':2000,'source_duration':60,
        'package':'a'*64,'recorder_sha256':'b'*64,'window':window(),'ready_epoch':now,'ready_uptime':200.0}
    v.write_json(tmp_path/'recording-started.json',ready)
    start = {'request_id':n['request_id'],'canonical_url':n['canonical_url'],'ready_marker_sha256':v.sha(tmp_path/'recording-started.json'),
        'control':'native_chrome','window':window(),'title':n['title'],'source_reset':'paused_at_zero','ad_state':'source_without_ad',
        'audio_state':'unmuted_control_observed','opening_picture_verified':True,'window_corroborated':True,
        'observed_epoch':1005.0,'playback_clock':2.0}
    n.update(audio_state='unmuted_control_observed',source_reset='paused_at_zero',ad_state='source_without_ad',opening_picture_verified=True,window_corroborated=True)
    start.pop('opening_picture_verified',None)
    start.update(version=2,source_identity_corroborated=True,picture_observation='source_picture_visible',
        observation_clock_basis='native_call_completion_epoch',observation_call_started_epoch=1004.5,receipt_written_epoch=1006)
    v.write_json(tmp_path/'native-playback-started.json',start)
    recording = {'window':window(),'ready_marker_sha256':v.sha(tmp_path/'recording-started.json'),'ready_epoch':now}
    return v,n,recording,ready,start


def test_actual_started_marker_and_fresh_native_start_are_request_bound(tmp_path):
    v,n,recording,ready,start = handshake(tmp_path)
    result = v.recording_handshake(tmp_path,n,recording,995,1100)
    assert result['native_start_delay_seconds'] == 5
    assert result['source_clock_offset_status'] == 'uncertain_observations_only'
    assert v.recording_seconds(n) == 120


@pytest.mark.parametrize('change',['missing','request','window','recorder','package','future_ready','old_hearing','no_advancing','marker_hash','not_reset'])
def test_missing_or_mismatched_recording_handshake_never_accepts(tmp_path,change):
    v,n,recording,ready,start = handshake(tmp_path)
    if change == 'missing':(tmp_path/'recording-started.json').unlink()
    elif change == 'request':ready['request_id'] = '87654321-1234-1234-1234-123456789abc'
    elif change == 'window':ready['window'] = dict(window(),id=43)
    elif change == 'recorder':ready['recorder_sha256'] = 'c'*64
    elif change == 'package':ready['package'] = 'c'*64
    elif change == 'future_ready':ready['ready_epoch'] = 1200
    elif change == 'old_hearing':start['audio_state'] = 'unmuted_source_sound_observed'
    elif change == 'no_advancing':start['playback_clock'] = 0
    elif change == 'marker_hash':start['ready_marker_sha256'] = 'c'*64
    else:start['source_reset'] = 'playing'
    if change != 'missing':v.write_json(tmp_path/'recording-started.json',ready)
    v.write_json(tmp_path/'native-playback-started.json',start)
    with pytest.raises((v.Blocked,OSError)):
        v.recording_handshake(tmp_path,n,recording,995,1100)


def native_observation_v2(tmp_path):
    v,n,recording,ready,start = handshake(tmp_path)
    n.update(audio_state='unmuted_control_observed',source_reset='paused_at_zero',ad_state='source_without_ad',opening_picture_verified=True,window_corroborated=True)
    start.pop('opening_picture_verified',None)
    start.update(version=2,source_identity_corroborated=True,picture_observation='ambiguous_black_during_capture',
        observation_clock_basis='native_call_completion_epoch',observation_call_started_epoch=1004.5,receipt_written_epoch=1040)
    v.write_json(tmp_path/'native-playback-started.json',start)
    return v,n,recording,start


def test_native_call_timestamp_is_distinct_from_later_receipt_and_black_is_ambiguous(tmp_path):
    v,n,recording,start = native_observation_v2(tmp_path)
    result=v.recording_handshake(tmp_path,n,recording,995,1100)
    assert result['native_start_delay_seconds']==5
    assert result['native_observation_call_uncertainty_seconds']==0.5
    assert result['native_picture_observation']=='ambiguous_black_during_capture'
    assert result['recorded_opening_and_ending_review_required'] is True


@pytest.mark.parametrize('change',['unversioned','write_time_basis','missing_identity','unverified_prerecord_opening'])
def test_revised_native_protocol_refuses_old_or_unverified_observations(tmp_path,change):
    v,n,recording,start = native_observation_v2(tmp_path)
    if change=='unversioned':start.pop('version')
    elif change=='write_time_basis':start['observation_clock_basis']='receipt_write_time'
    elif change=='missing_identity':start['source_identity_corroborated']=False
    else:n['opening_picture_verified']=False
    v.write_json(tmp_path/'native-playback-started.json',start)
    with pytest.raises(v.Blocked):v.recording_handshake(tmp_path,n,recording,995,1100)


def test_late_confirmation_preserves_failed_target_as_warning_not_pass(tmp_path):
    v,n,recording,ready,start = handshake(tmp_path)
    start.update(observation_call_started_epoch=1029.52248,observed_epoch=1030.21748,receipt_written_epoch=1047)
    v.write_json(tmp_path/'native-playback-started.json',start)
    result = v.recording_handshake(tmp_path,n,recording,995,1100)
    assert result['native_start_target_seconds'] == 30
    assert result['native_start_target_met'] is False
    assert result['warnings'] == ['late_native_playback_confirmation_requires_full_content_proof']
    assert result['native_start_delay_seconds'] == pytest.approx(30.21748)
    assert result['native_observation_call_uncertainty_seconds'] == pytest.approx(.695)
    assert result['full_episode_content_proof_required'] is True


@pytest.mark.parametrize('field,value', [('observed_epoch',float('nan')),('observation_call_started_epoch',float('inf')),
    ('observed_epoch',999),('playback_clock',float('nan')),('playback_clock',0),('receipt_written_epoch',999)])
def test_warning_never_relaxes_invalid_observation_timing(tmp_path,field,value):
    v,n,recording,ready,start=handshake(tmp_path)
    import json
    start[field]=value;(tmp_path/'native-playback-started.json').write_text(json.dumps(start))
    with pytest.raises(v.Blocked):v.recording_handshake(tmp_path,n,recording,995,1100)


def recovery_fixture(tmp_path):
    v,n,recording,ready,start=handshake(tmp_path)
    n.update(request_id=v.LATE_RECOVERY_REQUEST,verified_epoch=995)
    ready.update(request_id=n['request_id'],package=v.LATE_RECOVERY_PACKAGE)
    v.write_json(tmp_path/'recording-started.json',ready)
    start.update(request_id=n['request_id'],ready_marker_sha256=v.sha(tmp_path/'recording-started.json'),
        observation_call_started_epoch=1029.52248,observed_epoch=1030.21748,receipt_written_epoch=1047)
    v.write_json(tmp_path/'native-playback-started.json',start)
    v.write_json(tmp_path/'native-source.json',n)
    v.write_json(tmp_path/'original-command-result.json',{'exit_code':2,'result':{'status':'blocked','reason':'native_playback_start_missing_or_late'},'observed_epoch':1100})
    (tmp_path/'source.mp4').write_bytes(b'unchanged retained media')
    context={'version':1,'run_id':v.LATE_RECOVERY_RUN,'request_id':n['request_id'],
        'authority':'keith_2026-11-08_late_confirmation','files':{name:v.sha(tmp_path/name) for name in
        ('source.mp4','native-source.json','recording-started.json','native-playback-started.json','original-command-result.json')}}
    v.write_json(tmp_path/'recovery-context.json',context)
    return v,n,context


def test_recovery_requires_exact_existing_attempt_hashes_and_failure(tmp_path):
    v,n,c=recovery_fixture(tmp_path)
    ledger={'request_id':n['request_id'],'canonical_url':n['canonical_url'],'deadline_epoch':n['deadline_epoch'],
        'attempted':['record'],'runs':[v.LATE_RECOVERY_RUN],'by_method':{'record':v.LATE_RECOVERY_RUN}}
    result=v.validate_late_recovery(tmp_path,ledger,v.LATE_RECOVERY_RUN,v.sha(tmp_path/'source.mp4'),v.sha(tmp_path/'recovery-context.json'),1101)
    assert result['original_result']['reason']=='native_playback_start_missing_or_late'
    assert result['status']=='pending_content_verification'
    assert result['unretained_recorder_stdout']=='unavailable_not_reconstructed'
    assert ledger['attempted']==['record']


@pytest.mark.parametrize('change',['media','native','context_hash','wrong_run','ledger','deadline','different_failure'])
def test_recovery_never_renews_retries_or_rebinds_retained_evidence(tmp_path,change):
    v,n,c=recovery_fixture(tmp_path)
    ledger={'request_id':n['request_id'],'canonical_url':n['canonical_url'],'deadline_epoch':n['deadline_epoch'],
        'attempted':['record'],'runs':[v.LATE_RECOVERY_RUN],'by_method':{'record':v.LATE_RECOVERY_RUN}}
    raw=v.sha(tmp_path/'source.mp4'); digest=v.sha(tmp_path/'recovery-context.json');run=v.LATE_RECOVERY_RUN;now=1101
    if change=='media':(tmp_path/'source.mp4').write_bytes(b'changed')
    elif change=='native':v.write_json(tmp_path/'native-source.json',dict(n,title='different source'))
    elif change=='context_hash':digest='0'*64
    elif change=='wrong_run':run=run[:-1]+'a'
    elif change=='ledger':ledger['by_method']['record']='another run'
    elif change=='deadline':now=2000
    else:
        v.write_json(tmp_path/'original-command-result.json',{'exit_code':2,'result':{'status':'blocked','reason':'recording_audio_route_unverified'},'observed_epoch':1100})
        c['files']['original-command-result.json']=v.sha(tmp_path/'original-command-result.json');v.write_json(tmp_path/'recovery-context.json',c);digest=v.sha(tmp_path/'recovery-context.json')
    with pytest.raises(v.Blocked):v.validate_late_recovery(tmp_path,ledger,run,raw,digest,now)


def test_recovery_publishes_pending_bundle_and_reuses_without_recording(tmp_path,monkeypatch):
    state=tmp_path/'state';state.mkdir()
    run='youtube-bc7QaDmLmoA-20261108T090447Z-d57543ed';root=state/run;root.mkdir()
    v,n,c=recovery_fixture(root)
    v.CURRENT.update(role='mini',package='c'*64,recorder_sha256='d'*64)
    ledger={'request_id':n['request_id'],'canonical_url':n['canonical_url'],'deadline_epoch':n['deadline_epoch'],
        'attempted':['record'],'runs':[run],'by_method':{'record':run}}
    v.write_json(v.request_path(state,n),ledger)
    before={p.name:v.sha(p) for p in root.iterdir()}
    v.NAS=tmp_path/'nas';v.NAS.mkdir();monkeypatch.setattr(v,'mount_check',lambda *a:None)
    monkeypatch.setattr(v.time,'time',lambda:1101)
    monkeypatch.setattr(v,'install_config',lambda *a:{'role':'mini','package':v.LATE_RECOVERY_PACKAGE,
        'files':{'lib/video_extract.py':v.LATE_RECOVERY_CODE},'recorder_sha256':'b'*64})
    monkeypatch.setattr(v.legacy,'space_check',lambda *a:None)
    monkeypatch.setattr(v.legacy,'mount_check',lambda *a:None)
    monkeypatch.setattr(v.legacy,'nas_run',v.nas_run)
    monkeypatch.setattr(v.legacy,'probe',lambda *a:{'format':{'duration':'120'},'streams':[{'codec_type':'audio'},{'codec_type':'video'}]})
    calls=[]
    def command(args,*unused):
        calls.append(args)
        if args[-1].endswith('speech.flac'):root.joinpath('speech.flac').write_bytes(b'new export')
        return b''
    monkeypatch.setattr(v.legacy,'command',command)
    raw=v.sha(root/'source.mp4');digest=v.sha(root/'recovery-context.json')
    result=v.recover_recording(run,raw,digest,state)
    assert result['status']=='pending_content_verification'
    assert result['recovery']['handshake']['native_start_target_met'] is False
    assert result['recovery']['original_result']['reason']=='native_playback_start_missing_or_late'
    assert len(calls)==2 and all('record' not in args[0] for args in calls)
    assert not (root/'recording.json').exists() and not (state/'acceptance.json').exists()
    assert {name:v.sha(root/name) for name in before}==before
    assert v.read_json(v.request_path(state,n))==ledger
    assert v.verify(v.nas_run(run),result['manifest_sha256'])['recovery']['status']=='pending_content_verification'
    assert v.recover_recording(run,raw,digest,state)['manifest_sha256']==result['manifest_sha256']
    assert len(calls)==2
