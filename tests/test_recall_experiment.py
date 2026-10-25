"""Offline ranking experiments retain real source evidence and safe scope."""
import hashlib
import importlib
import importlib.util
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts/lib"))
import k2b_context as k
from test_k2b_context import _draft, _item, _note


def _experiment():
    assert importlib.util.find_spec("recall_experiment") is not None, "offline comparison is not implemented"
    return importlib.import_module("recall_experiment")


def _description(vault: Path, path: str, summary: str) -> dict:
    return {"schema_version": 1, "sources": [{"path": path,
        "source_sha256": hashlib.sha256((vault/path).read_bytes()).hexdigest(),
        "summary": summary, "questions": ["How can launch clearance be obtained?"]}]}


@pytest.mark.parametrize("method", ["lexical", "bm25"])
def test_summary_discovers_source_but_never_becomes_evidence(tmp_path: Path, method: str) -> None:
    exp = _experiment()
    vault = tmp_path / 'vault'
    rel = 'wiki/projects/project_atlas.md'
    _note(vault, rel, '# Atlas\n\nAwaiting acceptance.\n')
    aids = exp.load_descriptions(_description(vault, rel, 'Release approval and launch clearance.'), vault)
    with exp.ranking_variant(k, vault, method=method, descriptions=aids):
        packet = k.gather('launch clearance', vault)
        assert [s['relative_path'] for s in packet['sources']] == [rel]
        assert all('launch clearance' not in e['quote'] for e in packet['evidence'])
        assert any('Awaiting acceptance' in e['quote'] for e in packet['evidence'])
        k.validate_draft(packet, _draft(packet, state=[_item('Awaiting acceptance.', [packet['evidence'][0]['evidence_id']])]), vault_root=vault)
    assert not k.gather('launch clearance', vault)['evidence'], 'experiment must restore production ranking'


def test_stale_description_is_rejected(tmp_path: Path) -> None:
    exp = _experiment()
    vault = tmp_path / 'vault'
    rel = 'wiki/projects/project_atlas.md'
    p = _note(vault, rel, '# Atlas\n\nAwaiting acceptance.\n')
    aids = exp.load_descriptions(_description(vault, rel, 'Launch clearance.'), vault)
    p.write_text('# Atlas\n\nThe earlier plan has been withdrawn.\n')
    with pytest.raises(ValueError, match='stale'):
        with exp.ranking_variant(k, vault, method='bm25', descriptions=aids):
            k.gather('launch clearance', vault)


def test_bm25_and_summaries_do_not_invent_unknown_evidence(tmp_path: Path) -> None:
    exp = _experiment()
    vault = tmp_path / 'vault'
    rel = 'wiki/projects/project_atlas.md'
    _note(vault, rel, '# Atlas\n\nAwaiting acceptance.\n')
    aids = exp.load_descriptions(_description(vault, rel, 'Launch clearance.'), vault)
    with exp.ranking_variant(k, vault, method='bm25', descriptions=aids):
        assert k.gather('zzqunknown817', vault)['evidence'] == []


def test_bm25_ranks_named_subject_before_repeated_common_word(tmp_path: Path) -> None:
    exp = _experiment()
    vault = tmp_path / 'vault'
    _note(vault, 'wiki/work/work_atlas.md', '# Atlas\n\nAtlas payroll awaits validation.\n')
    _note(vault, 'wiki/work/work_general.md', '# General\n\n' + ('payroll '*200))
    with exp.ranking_variant(k, vault, method='bm25'):
        p = k.gather('Atlas payroll', vault)
        assert p['sources'][0]['relative_path'] == 'wiki/work/work_atlas.md'


@pytest.mark.parametrize('target', ['../outside.md','wiki/context/policy.md','wiki/projects/index.md'])
def test_description_path_is_not_an_escape_or_control_source(tmp_path: Path, target: str) -> None:
    exp = _experiment()
    vault = tmp_path / 'vault'
    vault.mkdir()
    payload = {'schema_version':1,'sources':[{'path':target,'source_sha256':'0'*64,'summary':'launch','questions':[]}]}
    with pytest.raises(ValueError):
        exp.load_descriptions(payload, vault)


def test_comparison_distinguishes_source_hit_from_missing_answer_passage(tmp_path: Path) -> None:
    exp = _experiment()
    vault = tmp_path / 'vault'
    _note(vault, 'wiki/projects/project_atlas.md', '# Atlas\n\nAwaiting acceptance.\n')
    report = exp.compare(k, k, vault, {'heldout': [
        ['known', 'Atlas', 'wiki/projects/project_atlas.md', ['signed contract']],
        ['unknown', 'zzqunknown791', None, []]
    ]}, descriptions={})
    assert len(report['results']) == 10
    for row in report['results']:
        assert row['binding_valid']
        assert row['packet_bytes'] <= 48_000
        if row['case'] == 'known':
            assert row['source_hit']
            assert row['passage_check'] is False
        else:
            assert row['source_hit']
            assert row['evidence_count'] == 0


def test_description_hash_uses_original_crlf_bytes(tmp_path: Path) -> None:
    exp = _experiment()
    vault = tmp_path / 'vault'
    rel = 'wiki/projects/project_atlas.md'
    p = _note(vault, rel, '# Atlas\n\nAwaiting acceptance.\n')
    p.write_bytes(p.read_bytes().replace(b'\n', b'\r\n'))
    aids = exp.load_descriptions(_description(vault, rel, 'Launch clearance.'), vault)
    with exp.ranking_variant(k, vault, method='bm25', descriptions=aids):
        packet = k.gather('launch clearance', vault)
        assert packet['sources'][0]['relative_path'] == rel


@pytest.mark.parametrize('case_id', ['../escape', '/tmp/escape', 'nested/path', 'C:\\escape'])
def test_comparison_rejects_case_ids_that_escape_packet_directory(tmp_path: Path, case_id: str) -> None:
    exp = _experiment()
    vault = tmp_path / 'vault'
    _note(vault, 'wiki/projects/project_atlas.md', '# Atlas\n\nAwaiting acceptance.\n')
    with pytest.raises(ValueError, match='case'):
        exp.compare(k, k, vault, {'heldout':[[case_id, 'Atlas', 'wiki/projects/project_atlas.md', []]]}, descriptions={})
