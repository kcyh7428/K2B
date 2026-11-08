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


def test_recording_requires_actual_unmuted_source_and_reset():
    v = load()
    with pytest.raises(v.Blocked): v.recording_ready({'audio_state':'muted'})
    assert v.recording_ready({'audio_state':'unmuted_source_sound_observed','source_reset':'paused_at_zero','ad_state':'source_without_ad',
        'window':window(),'title':'verified source','opening_picture_verified':True,'window_corroborated':True})


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
    n = {'audio_state':'unmuted_source_sound_observed','source_reset':'paused_at_zero','ad_state':'source_without_ad',
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
