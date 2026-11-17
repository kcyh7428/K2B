from test_video_extract import load
import pytest


def draft(tmp_path):
    v=load();v.CURRENT={'role':'mini'}
    request='12345678-1234-1234-1234-123456789abc';root=tmp_path/'content-drafts'/request;root.mkdir(parents=True)
    url='https://www.youtube.com/watch?v=bc7QaDmLmoA'
    metadata={'source_url':url,'publisher':'creator','title':'source','source_duration_seconds':60,'control':'native_chrome',
        'caption_language':'English','provenance_label_exact':'English (auto-generated)','caption_provenance':'auto-generated',
        'observed_caption_count':3,'caption_start_seconds_in_sequence':[0,20,58],'first_caption_reached':True,'last_caption_reached':True}
    v.write_json(root/'caption-metadata.json',metadata)
    v.write_json(root/'claims.json',{'source_url':url,'publisher':'creator','title':'source','duration_seconds':60,'claims':[{'range':'0:00-0:59','claim':'paraphrased source claim'}]})
    v.write_json(root/'prior-visual-notes.json',{'source_url':url,'full_visual_viewing':False,'moments':[]})
    v.write_json(root/'visual-observations.json',{'source_url':url,'publisher':'creator','title':'source','source_duration_seconds':60,
        'control':'native_chrome','full_visual_viewing':False,'observations':[{'requested_second':20,'clock_seconds':20,
        'observation_call_started_epoch':900,'observed_epoch':901,'description':'Actual source demonstration','uncertainty':'Sampled paused picture only'}]})
    (root/'analysis.md').write_text(url+'\nTimestamped paraphrase of observed captions, not a transcript.\n')
    context={'version':1,'evidence_type':'caption_visual','request_id':request,'submitted_url':url,'canonical_url':url,
        'host':'Macmini','user':'fastshower','control':'native_chrome','title':'source','publisher':'creator','source_duration':60,
        'request_created_epoch':1000,'deadline_epoch':2000,'caption_observation_window':{'start_epoch':800,'end_epoch':850,'basis':'native_caption_task_turn_interval'},
        'caption_end_times':'not_exposed','speech_completeness':'unverified','raw_caption_retention_status':'not_retained',
        'raw_caption_retention_reason':'Copyright limits; metadata and paraphrase retained','caption_provenance_kind':'auto_generated',
        'caption_sequence_status':'all_exposed_rows_inspected',
        'analysis_kind':'timestamped_paraphrase','complete_media_available':False,'full_visual_review':False,
        'files':{n:v.sha(root/n) for n in v.CONTENT_FILES}}
    v.write_json(root/'evidence.json',context)
    return v,root,context


def test_caption_content_is_typed_and_has_no_media_requirement(tmp_path):
    v,root,c=draft(tmp_path)
    out=v.validate_content_evidence(root,c,1001)
    assert out['evidence_type']=='caption_visual' and out['complete_media_available'] is False
    assert out['caption_count']==3 and out['last_caption_start']==58
    assert out['caption_end_times']=='not_exposed' and out['speech_completeness']=='unverified'
    assert out['analysis_kind']=='timestamped_paraphrase' and out['full_visual_review'] is False
    assert not (root/'source.mp4').exists()


def export_draft(tmp_path):
    v,root,c=draft(tmp_path)
    m=v.read_json(root/'caption-metadata.json')
    m['control']='mini_in_app_browser'
    m['first_exported_row_inspected']=m['last_exported_row_inspected']=True
    m.pop('first_caption_reached');m.pop('last_caption_reached')
    m['native_export']={'api':'tab.content.exportYouTubeTranscript',
        'source_url':c['canonical_url'],'sha256':'a'*64,'bytes':1200,
        'row_count':3,'first_start_seconds':0,'last_start_seconds':58}
    v.write_json(root/'caption-metadata.json',m)
    c['files']['caption-metadata.json']=v.sha(root/'caption-metadata.json')
    c['caption_acquisition_method']='native_browser_export'
    c['caption_observation_window'].update(start_epoch=1000,end_epoch=1000)
    notes=v.read_json(root/'visual-observations.json')
    notes['observations'][0].update(observation_call_started_epoch=1000,observed_epoch=1000)
    v.write_json(root/'visual-observations.json',notes)
    c['files']['visual-observations.json']=v.sha(root/'visual-observations.json')
    c['caption_sequence_status']='all_exported_rows_inspected'
    c['raw_caption_retention_status']='private_export'
    c['raw_caption_retention_reason']='Raw export retained privately on Mini; not in NAS bundle'
    v.write_json(root/'evidence.json',c)
    return v,root,c


def test_browser_export_preserves_actual_route_and_private_retention(tmp_path):
    v,root,c=export_draft(tmp_path)
    out=v.validate_content_evidence(root,c,1001)
    assert out['caption_acquisition_method']=='native_browser_export'
    assert out['caption_sequence_status']=='all_exported_rows_inspected'
    assert out['raw_caption_retention_status']=='private_export'
    assert out['native_export']['sha256']=='a'*64
    assert out['speech_completeness']=='unverified'


def test_browser_export_observation_uses_original_request_deadline(tmp_path):
    v,root,c=export_draft(tmp_path)
    c['caption_observation_window'].update(start_epoch=1002,end_epoch=1003)
    notes=v.read_json(root/'visual-observations.json')
    notes['observations'][0].update(observation_call_started_epoch=1003,observed_epoch=1004)
    v.write_json(root/'visual-observations.json',notes)
    c['files']['visual-observations.json']=v.sha(root/'visual-observations.json')
    out=v.validate_content_evidence(root,c,1005)
    assert out['caption_observation_window']['end_epoch']==1003
    with pytest.raises(v.Blocked):v.validate_content_evidence(root,c,1002)


@pytest.mark.parametrize('kind',['caption','visual'])
def test_new_export_cannot_rebind_observations_from_before_original_request(tmp_path,kind):
    v,root,c=export_draft(tmp_path)
    if kind=='caption':c['caption_observation_window'].update(start_epoch=999,end_epoch=1000)
    else:
        notes=v.read_json(root/'visual-observations.json')
        notes['observations'][0].update(observation_call_started_epoch=999,observed_epoch=1000)
        v.write_json(root/'visual-observations.json',notes);c['files']['visual-observations.json']=v.sha(root/'visual-observations.json')
    with pytest.raises(v.Blocked):v.validate_content_evidence(root,c,1001)


def test_export_reader_rejects_future_observation_but_allows_later_reuse(tmp_path,monkeypatch):
    v,root,c=export_draft(tmp_path)
    c['caption_observation_window'].update(start_epoch=1000,end_epoch=1003)
    monkeypatch.setattr(v.time,'time',lambda:1001)
    with pytest.raises(v.Blocked):v.validate_content_evidence(root,c)
    monkeypatch.setattr(v.time,'time',lambda:2500)
    assert v.validate_content_evidence(root,c)['caption_count']==3


@pytest.mark.parametrize('change',['wrong_source','missing_last','bad_digest','wrong_count','panel_claim','unknown_method','wrong_api','wrong_last_time'])
def test_browser_export_rejects_unbound_or_mislabelled_evidence(tmp_path,change):
    v,root,c=export_draft(tmp_path);m=v.read_json(root/'caption-metadata.json')
    if change=='wrong_source':m['native_export']['source_url']='https://www.youtube.com/watch?v=AAAAAAAAAAA'
    elif change=='missing_last':m['last_exported_row_inspected']=False
    elif change=='bad_digest':m['native_export']['sha256']='invalid'
    elif change=='wrong_count':m['native_export']['row_count']=2
    elif change=='panel_claim':c['caption_sequence_status']='all_exposed_rows_inspected'
    elif change=='unknown_method':c['caption_acquisition_method']='third_party_service'
    elif change=='wrong_api':m['native_export']['api']='unapproved_export'
    else:m['native_export']['last_start_seconds']=59
    v.write_json(root/'caption-metadata.json',m);c['files']['caption-metadata.json']=v.sha(root/'caption-metadata.json')
    reason = ('unsupported_caption_acquisition_method' if change=='unknown_method' else
              'native_export_sequence_incomplete' if change in ('missing_last','panel_claim') else
              'native_export_binding_invalid')
    with pytest.raises(v.Blocked,match=reason):v.validate_content_evidence(root,c,1001)


@pytest.mark.parametrize('change',['identity','media_claim','verbatim_claim','caption_end','future_observation','caption_order','missing_last','changed_payload','extra_payload','expired','duration','clock'])
def test_content_evidence_rejects_false_or_unbound_claims(tmp_path,change):
    v,root,c=draft(tmp_path)
    if change=='identity':c['canonical_url']='https://www.youtube.com/watch?v=AAAAAAAAAAA'
    elif change=='media_claim':c['complete_media_available']=True
    elif change=='verbatim_claim':c['analysis_kind']='verbatim_transcript'
    elif change=='caption_end':c['caption_end_times']='complete'
    elif change=='future_observation':c['caption_observation_window']['end_epoch']=3000
    elif change in ('caption_order','missing_last'):
        m=v.read_json(root/'caption-metadata.json')
        if change=='caption_order':m['caption_start_seconds_in_sequence']=[0,58,20]
        else:m['last_caption_reached']=False
        v.write_json(root/'caption-metadata.json',m);c['files']['caption-metadata.json']=v.sha(root/'caption-metadata.json')
    elif change=='changed_payload':(root/'analysis.md').write_text('changed')
    elif change=='extra_payload':c['files']['source.mp4']='a'*64
    elif change=='expired':c['deadline_epoch']=1000
    elif change=='duration':c['source_duration']=3601
    else:
        s=v.read_json(root/'visual-observations.json');s['observations'][0]['clock_seconds']=61
        v.write_json(root/'visual-observations.json',s);c['files']['visual-observations.json']=v.sha(root/'visual-observations.json')
    with pytest.raises(v.Blocked):v.validate_content_evidence(root,c,1001)


@pytest.mark.parametrize('caption_route',['panel','export'])
def test_real_content_publication_home_validation_and_reuse_never_probe_or_transcribe(tmp_path,monkeypatch,caption_route):
    v,root,c=(export_draft if caption_route=='export' else draft)(tmp_path);state=tmp_path;nas=tmp_path/'nas';nas.mkdir()
    v.configure({'role':'mini'},'youtube',state);monkeypatch.setattr(v,'NAS',nas);monkeypatch.setattr(v,'mount_check',lambda *a:None)
    monkeypatch.setattr(v.legacy,'mount_check',lambda *a:None);monkeypatch.setattr(v.legacy,'nas_run',v.nas_run)
    monkeypatch.setattr(v.legacy,'space_check',lambda *a:None);monkeypatch.setattr(v.time,'time',lambda:1001)
    def forbidden(*a,**k):raise AssertionError('Caption evidence must not invoke media/provider work')
    monkeypatch.setattr(v.legacy,'probe',forbidden);monkeypatch.setattr(v.legacy,'transcribe',forbidden);monkeypatch.setattr(v.legacy,'command',forbidden)
    digest=v.sha(root/'evidence.json');first=v.publish_content(c['canonical_url'],c['request_id'],digest,state)
    assert first['status']=='content_evidence_published' and first['evidence_type']=='caption_visual'
    assert v.publish_content(c['canonical_url'],c['request_id'],digest,state)['manifest_sha256']==first['manifest_sha256']
    home=tmp_path/'home';home.mkdir();v.configure({'role':'home'},'youtube',home)
    monkeypatch.setattr(v.legacy,'command',forbidden);monkeypatch.setattr(v,'run_recorder',forbidden)
    monkeypatch.setattr(v.legacy,'download',forbidden)
    result=v.process(first['run_id'],first['manifest_sha256'],home)
    assert result['evidence_type']=='caption_visual' and result['complete_media_available'] is False
    assert result['caption_end_times']=='not_exposed' and result['raw_caption_retention_status']==c['raw_caption_retention_status']
    assert result.get('caption_acquisition_method','native_panel')==('native_browser_export' if caption_route=='export' else 'native_panel')
    assert result['status']=='content_evidence_verified_for_assistant_read'
    assert result['reader_version']==v.processor_id() and result['source_bundle']==str(v.nas_run(first['run_id']))
    assert result['canonical_url']==c['canonical_url'] and result['title']==c['title'] and result['publisher']==c['publisher']
    assert 'prepared_manifest_sha256' not in result and list(home.iterdir())==[]
    assert v.process(first['run_id'],first['manifest_sha256'],home)['reused'] is True
    status=v.read_status(first['run_id'],first['manifest_sha256'],home)
    assert status['caption_provenance_label']=='English (auto-generated)' and status['nas_publication_verified'] is True
    assert status['analysis_kind']=='timestamped_paraphrase' and status['full_visual_review'] is False
    (v.nas_run(first['run_id'])/'analysis.md').write_text('corrupt')
    with pytest.raises(v.Blocked):v.process(first['run_id'],first['manifest_sha256'],home)


def test_historical_panel_bundle_coverage_remains_readable(tmp_path,monkeypatch):
    v,root,c=draft(tmp_path);nas=tmp_path/'nas';nas.mkdir()
    v.configure({'role':'mini'},'youtube',tmp_path);monkeypatch.setattr(v,'NAS',nas)
    monkeypatch.setattr(v,'mount_check',lambda *a:None);monkeypatch.setattr(v.legacy,'space_check',lambda *a:None)
    monkeypatch.setattr(v.time,'time',lambda:1001)
    result=v.publish_content(c['canonical_url'],c['request_id'],v.sha(root/'evidence.json'),tmp_path)
    bundle=v.nas_run(result['run_id']);manifest=v.read_json(bundle/'manifest.json')
    for key in ('caption_acquisition_method','native_export'):manifest['coverage'].pop(key,None)
    v.write_json(bundle/'manifest.json',manifest)
    v.configure({'role':'home'},'youtube',tmp_path)
    out=v.process(result['run_id'],v.sha(bundle/'manifest.json'),tmp_path)
    assert out['caption_count']==3 and out['nas_publication_verified'] is True


@pytest.mark.parametrize('action,args,reason',[
    ('capture',['https://www.youtube.com/watch?v=bc7QaDmLmoA'],'youtube_requires_native_caption_visual_evidence'),
    ('record',['https://www.youtube.com/watch?v=bc7QaDmLmoA'],'recording_experimental_not_in_default_workflow'),
    ('recover-recording',['youtube-bc7QaDmLmoA-20261108T090447Z-d57543ed','a'*64,'b'*64],'recording_recovery_experimental_not_activated')])
def test_default_cli_refuses_media_or_recording_fallback(tmp_path,monkeypatch,capsys,action,args,reason):
    import json
    v=load();monkeypatch.setattr(v,'install_config',lambda:{'role':'mini'})
    monkeypatch.setattr(v.legacy,'own_home',lambda:tmp_path)
    monkeypatch.setattr(v.sys if hasattr(v,'sys') else __import__('sys'),'argv',['video-extract.py',action,*args])
    def forbidden(*a,**k):raise AssertionError('Forbidden fallback invoked')
    monkeypatch.setattr(v,'capture',forbidden);monkeypatch.setattr(v,'recover_recording',forbidden)
    assert v.main()==2 and json.loads(capsys.readouterr().out)['reason']==reason


def test_caption_clock_uncertainty_is_preserved_without_substitution(tmp_path):
    v,root,c=draft(tmp_path)
    notes=v.read_json(root/'visual-observations.json');notes['observations'][0]['clock_seconds']=None
    notes['observations'][0]['uncertainty']='Actual player clock unavailable; requested20is not an observation'
    v.write_json(root/'visual-observations.json',notes);c['files']['visual-observations.json']=v.sha(root/'visual-observations.json')
    out=v.validate_content_evidence(root,c,1001)
    assert out['visual_observations'][0]['clock_seconds'] is None
    assert out['visual_observations'][0]['requested_second']==20


def test_source_wide_interpretation_is_not_full_visual_coverage(tmp_path):
    v,root,c=draft(tmp_path);claims=v.read_json(root/'claims.json')
    claims['claims'].append({'range':'whole source','claim':'Source-wide interpretation','limits':'Limited native observations'})
    v.write_json(root/'claims.json',claims);c['files']['claims.json']=v.sha(root/'claims.json')
    out=v.validate_content_evidence(root,c,1001)
    assert out['full_visual_review'] is False and out['source_uncertainties'][-1]=='Limited native observations'


def test_content_byte_failure_preserves_attempt_and_never_publishes_success(tmp_path,monkeypatch):
    v,root,c=draft(tmp_path);nas=tmp_path/'nas';nas.mkdir()
    v.configure({'role':'mini'},'youtube',tmp_path);v.legacy.MAX_BYTES=4*1024**2+1024
    monkeypatch.setattr(v,'NAS',nas);monkeypatch.setattr(v,'mount_check',lambda *a:None)
    monkeypatch.setattr(v.legacy,'space_check',lambda *a:None);monkeypatch.setattr(v.time,'time',lambda:1001)
    with pytest.raises(v.Blocked,match='run_byte_budget'):
        v.publish_content(c['canonical_url'],c['request_id'],v.sha(root/'evidence.json'),tmp_path)
    ledger=v.read_json(v.request_path(tmp_path,c));assert ledger['attempted']==['content']
    staged=tmp_path/ledger['by_method']['content']
    assert staged.is_dir() and not (staged/'completion.json').exists() and not (staged/'manifest.json').exists()
    assert (root/'analysis.md').is_file() and list(nas.iterdir())==[]


def publication_fixture(tmp_path,monkeypatch):
    v,root,c=draft(tmp_path);nas=tmp_path/'nas';nas.mkdir()
    v.configure({'role':'mini'},'youtube',tmp_path);monkeypatch.setattr(v,'NAS',nas)
    monkeypatch.setattr(v,'mount_check',lambda *a:None);monkeypatch.setattr(v.legacy,'mount_check',lambda *a:None)
    monkeypatch.setattr(v.legacy,'nas_run',v.nas_run);monkeypatch.setattr(v.legacy,'space_check',lambda *a:None)
    monkeypatch.setattr(v.time,'time',lambda:1001)
    return v,root,c


def test_first_expired_publication_pins_deadline_before_validation(tmp_path,monkeypatch):
    v,root,c=publication_fixture(tmp_path,monkeypatch);monkeypatch.setattr(v.time,'time',lambda:2001)
    with pytest.raises(v.Blocked,match='expired_content_publication_request'):
        v.publish_content(c['canonical_url'],c['request_id'],v.sha(root/'evidence.json'),tmp_path)
    assert v.request_path(tmp_path,c).is_file()
    c['deadline_epoch']=3000;v.write_json(root/'evidence.json',c)
    with pytest.raises(v.Blocked,match='immutable_request_binding_changed'):
        v.publish_content(c['canonical_url'],c['request_id'],v.sha(root/'evidence.json'),tmp_path)


def test_content_request_creation_time_is_also_immutable(tmp_path,monkeypatch):
    v,root,c=publication_fixture(tmp_path,monkeypatch)
    v.publish_content(c['canonical_url'],c['request_id'],v.sha(root/'evidence.json'),tmp_path)
    c['request_created_epoch']=900;v.write_json(root/'evidence.json',c)
    with pytest.raises(v.Blocked,match='immutable_request_binding_changed'):
        v.publish_content(c['canonical_url'],c['request_id'],v.sha(root/'evidence.json'),tmp_path)


def test_context_changed_during_staging_cannot_publish(tmp_path,monkeypatch):
    v,root,c=publication_fixture(tmp_path,monkeypatch);reserve=v.reserve_attempt
    def change(*a,**k):
        receipt=reserve(*a,**k);changed=dict(c,raw_caption_retention_reason='Changed during stage');v.write_json(root/'evidence.json',changed);return receipt
    monkeypatch.setattr(v,'reserve_attempt',change)
    with pytest.raises(v.Blocked,match='content_evidence_digest_mismatch'):
        v.publish_content(c['canonical_url'],c['request_id'],v.sha(root/'evidence.json'),tmp_path)
    ledger=v.read_json(v.request_path(tmp_path,c));staged=tmp_path/ledger['by_method']['content']
    assert not (staged/'manifest.json').exists() and not (staged/'completion.json').exists()


def test_home_rejects_hash_valid_bundle_for_different_source_run(tmp_path,monkeypatch):
    import shutil
    v,root,c=publication_fixture(tmp_path,monkeypatch);first=v.publish_content(c['canonical_url'],c['request_id'],v.sha(root/'evidence.json'),tmp_path)
    wrong='youtube-AAAAAAAAAAA-20261108T000000Z-abcdef01';target=v.nas_run(wrong);target.parent.mkdir(parents=True,exist_ok=True)
    shutil.copytree(v.nas_run(first['run_id']),target);m=v.read_json(target/'manifest.json');m.update(run_id=wrong,video_id='AAAAAAAAAAA');v.write_json(target/'manifest.json',m)
    home=tmp_path/'home';home.mkdir();v.configure({'role':'home'},'youtube',home)
    for operation in (v.process,v.read_status):
        with pytest.raises(v.Blocked,match='content_run_source_identity_mismatch'):operation(wrong,v.sha(target/'manifest.json'),home)


def test_home_caption_reader_has_no_copy_or_preparation_state(tmp_path,monkeypatch):
    v,root,c=publication_fixture(tmp_path,monkeypatch);first=v.publish_content(c['canonical_url'],c['request_id'],v.sha(root/'evidence.json'),tmp_path)
    source=v.nas_run(first['run_id']);home=tmp_path/'home';home.mkdir();v.configure({'role':'home'},'youtube',home);prepare=v.preparation_state
    def change(*a,**k):
        out=prepare(*a,**k);(source/'analysis.md').write_text(c['canonical_url']+'\nChanged after source validation\n');return out
    monkeypatch.setattr(v,'preparation_state',change)
    out=v.process(first['run_id'],first['manifest_sha256'],home)
    assert out['status']=='content_evidence_verified_for_assistant_read'
    assert v.sha(source/'analysis.md')==c['files']['analysis.md'] and list(home.iterdir())==[]


def test_caption_reader_never_publishes_derived_results(tmp_path,monkeypatch):
    v,root,c=publication_fixture(tmp_path,monkeypatch);first=v.publish_content(c['canonical_url'],c['request_id'],v.sha(root/'evidence.json'),tmp_path)
    home=tmp_path/'home';home.mkdir();v.configure({'role':'home'},'youtube',home)
    def forbidden(*a,**k):raise AssertionError('Caption reader must not create another publication transaction')
    monkeypatch.setattr(v,'persist_prepared',forbidden);monkeypatch.setattr(v.legacy,'publish_file',forbidden)
    analysis=v.nas_run(first['run_id'])/'analysis.md';before=(v.sha(analysis),analysis.stat().st_mtime_ns)
    v.process(first['run_id'],first['manifest_sha256'],home)
    result=v.process(first['run_id'],first['manifest_sha256'],home)
    assert result['reused'] is True and (v.sha(analysis),analysis.stat().st_mtime_ns)==before
    assert v.read_status(first['run_id'],first['manifest_sha256'],home)['nas_publication_verified'] is True
    assert list(home.iterdir())==[]


def test_home_reenforces_context_size_limit(tmp_path,monkeypatch):
    v,root,c=publication_fixture(tmp_path,monkeypatch);first=v.publish_content(c['canonical_url'],c['request_id'],v.sha(root/'evidence.json'),tmp_path)
    source=v.nas_run(first['run_id']);e=v.read_json(source/'evidence.json');e['ignored_padding']='x'*1024**2;v.write_json(source/'evidence.json',e)
    m=v.read_json(source/'manifest.json');m.pop('files');m['evidence_sha256']=v.sha(source/'evidence.json');digest=v.payload_manifest(source,m)
    home=tmp_path/'home';home.mkdir();v.configure({'role':'home'},'youtube',home)
    with pytest.raises(v.Blocked,match='content_evidence_size_limit'):v.process(first['run_id'],digest,home)


def test_completed_publication_reuse_rechecks_local_plus_nas_bytes(tmp_path,monkeypatch):
    v,root,c=publication_fixture(tmp_path,monkeypatch);digest=v.sha(root/'evidence.json')
    first=v.publish_content(c['canonical_url'],c['request_id'],digest,tmp_path)
    completion=tmp_path/first['run_id']/'completion.json';before=(completion.read_bytes(),completion.stat().st_mtime_ns)
    # Existing source copies are complete, so no copy-loop monitor will run.
    # Retained derived files on this same NAS run still count in the operation.
    (v.nas_run(first['run_id'])/'retained-derived.bin').write_bytes(b'x'*32768)
    v.legacy.MAX_BYTES=4*1024**2+16384
    with pytest.raises(v.Blocked,match='run_byte_budget'):
        v.publish_content(c['canonical_url'],c['request_id'],digest,tmp_path)
    assert (completion.read_bytes(),completion.stat().st_mtime_ns)==before
    assert v.read_json(v.request_path(tmp_path,c))['attempted']==['content']


@pytest.mark.parametrize('platform',['facebook','youtube'])
def test_completed_copy_reuse_checks_deadline_before_success_receipt(tmp_path,monkeypatch,platform):
    v=load();v.configure({'role':'mini'},platform,tmp_path);nas=tmp_path/'nas';nas.mkdir()
    monkeypatch.setattr(v,'NAS',nas);monkeypatch.setattr(v,'mount_check',lambda *a:None)
    monkeypatch.setattr(v.legacy,'mount_check',lambda *a:None);monkeypatch.setattr(v.legacy,'nas_run',v.nas_run)
    monkeypatch.setattr(v.legacy,'space_check',lambda *a:None)
    run=('facebook-2372450313602638' if platform=='facebook' else 'youtube-bc7QaDmLmoA')+'-20261108T000000Z-abcdef01'
    root=tmp_path/run;root.mkdir();(root/'source-proof.bin').write_bytes(b'synthetic publication payload')
    kind='media' if platform=='facebook' else 'caption_visual'
    digest=v.payload_manifest(root,{'platform':platform,'run_id':run,'evidence_type':kind})
    first=v.publish_source(root,digest,v.time.monotonic()+60)
    completion=root/'completion.json';before=(completion.read_bytes(),completion.stat().st_mtime_ns)
    with pytest.raises(v.Blocked,match='run_deadline'):v.publish_source(root,digest,v.time.monotonic()-1)
    assert (completion.read_bytes(),completion.stat().st_mtime_ns)==before
    assert v.read_json(completion)['evidence_type']==kind
    assert first['status']==('captured' if platform=='facebook' else 'content_evidence_published')
