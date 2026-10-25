"""Recall regressions using synthetic sources, never copied private history."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "lib"))
import k2b_context as k
from test_k2b_context import _apply, _bundle, _note, _write_published_memory


def test_named_subject_outranks_earlier_index_row(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    fixture = json.loads((Path(__file__).parent / 'fixtures/k2b-context-ranking/named-subject.json').read_text())
    for rel, text in fixture['notes'].items():
        _note(vault, rel, text)
    packet = k.gather(fixture['query'], vault)
    assert packet["sources"][0]["relative_path"] == "wiki/work/work_atlas_hris.md"


def test_memory_key_value_match_outranks_incidental_quote(tmp_path: Path) -> None:
    memory = tmp_path / "state" / "memory.json"
    for index in range(6):
        value = "Router maintenance is pending."
        _apply(memory, _bundle(
            key=f"memory.fact.a_router_{index}.aaaa", value=value,
            quote="Atlas payroll was mentioned while discussing router maintenance.",
            event_id=f"incidental-{index}", cursor=f"turn:{index}",
        ))
    wanted = _apply(memory, _bundle(
        key="memory.fact.z_atlas_payroll.bbbb", value="Atlas payroll is deferred until validation.",
        quote="Atlas payroll is deferred until validation.", event_id="direct", cursor="turn:direct",
    ))
    vault = tmp_path / "vault"
    _write_published_memory(vault, memory)
    packet = k.gather("Atlas payroll", vault, max_evidence=2)
    assert wanted["item_id"] in {s.get("item_id") for s in packet["evidence"]}


def test_two_query_entities_survive_memory_pressure(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    _note(vault, "wiki/people/person_alice.md", "# Alice\n\n" + "\n\n".join(f"Alice owns Atlas workstream {i}." for i in range(12)))
    _note(vault, "wiki/projects/project_boreal.md", "# Boreal\n\nBoreal release is held for review.\n")
    memory = tmp_path / "state" / "memory.json"
    for index in range(12):
        value = (f"Alice discussed Atlas item {index}. " + "context " * 80).rstrip()
        _apply(memory, _bundle(key=f"memory.fact.alice_{index}.aaaa", value=value, quote=value, event_id=f"event-{index}", cursor=f"turn:{index}"))
    _write_published_memory(vault, memory)
    packet = k.gather("Alice Boreal", vault, max_evidence=8, max_bytes=9_000)
    paths = {s["relative_path"] for s in packet["sources"]}
    assert {"wiki/people/person_alice.md", "wiki/projects/project_boreal.md"} <= paths
    assert len(json.dumps(packet, ensure_ascii=False).encode()) <= 9_000
    assert {s["source_id"] for s in packet["sources"]} == {e["source_id"] for e in packet["evidence"]}


def test_current_subject_outranks_incidental_retired_feature(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    _note(vault, "wiki/concepts/feature_recovery.md", "---\nstatus: retired\n---\n# Recovery feature\n\nAtlas was mentioned during recovery testing.\n")
    _note(vault, "wiki/work/work_atlas_payroll.md", "# Atlas payroll\n\nAtlas payroll recovery requires validation.\n")
    _note(vault, "wiki/work/index.md", "[[feature_recovery]] recovery\n[[work_atlas_payroll]] Atlas payroll\n")
    packet = k.gather("Atlas payroll recovery", vault)
    assert packet["sources"][0]["relative_path"] == "wiki/work/work_atlas_payroll.md"


@pytest.mark.parametrize("link", [
    "[[wiki/people/person_mei]]", "[[wiki/people/person_mei.md#Role|Mei]]",
    "[Mei](wiki/people/person_mei.md#Role)", "[[person_mei|Mei]]", "[[person_mei#Role]]",
])
def test_topic_expansion_supports_vault_path_and_markdown_links(tmp_path: Path, link: str) -> None:
    vault = tmp_path / "vault"
    _note(vault, "wiki/projects/project_atlas.md", f"# Atlas\n\nAtlas sponsor: {link}.\n")
    _note(vault, "wiki/people/person_mei.md", "# Mei\n\nMei sponsors Atlas.\n")
    packet = k.gather("Atlas sponsor", vault, topic_note="wiki/projects/project_atlas.md")
    assert "wiki/people/person_mei.md" in {s["relative_path"] for s in packet["sources"]}


def test_short_name_does_not_match_substrings(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    _note(vault, "wiki/work/work_annual.md", "# Annual planning\n\nThe annual planner has a new panel.\n")
    packet = k.gather("Ann", vault)
    assert packet["evidence"] == []


def test_named_note_keeps_a_substantive_passage_before_heading(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    _note(vault, "wiki/work/work_atlas.md", "# Atlas\n\nAtlas owner is Mei; delivery is awaiting review.\n")
    packet = k.gather("Atlas", vault, max_evidence=1)
    assert "owner is Mei" in packet["evidence"][0]["quote"]


def test_link_expansion_refuses_symlinked_parent(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "person_mei.md").write_text("# Mei\n\nMei sponsors Atlas.")
    _note(vault, "wiki/projects/project_atlas.md", "# Atlas\n\n[[wiki/people/person_mei.md]]\n")
    (vault / "wiki/people").symlink_to(outside, target_is_directory=True)
    packet = k.gather("Atlas", vault, topic_note="wiki/projects/project_atlas.md")
    assert all(s["relative_path"] != "wiki/people/person_mei.md" for s in packet["sources"])


def test_topic_expansion_does_not_emit_index_evidence(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    _note(vault, "wiki/projects/project_atlas.md", "# Atlas\n\n[[index]]\n")
    _note(vault, "wiki/work/index.md", "# Atlas catalogue\n\nThis row is not subject evidence.\n")
    packet = k.gather("Atlas", vault, topic_note="wiki/projects/project_atlas.md")
    assert all(not s["relative_path"].endswith('/index.md') for s in packet['sources'])


def test_long_passage_windows_anchor_on_whole_name_not_incidental_substring(tmp_path: Path) -> None:
    vault = tmp_path / 'vault'
    text = 'Annual planning. ' + ('filler ' * 4000) + 'Ann owns hiring.' + (' filler' * 4000) + ' Annual close.'
    _note(vault, 'wiki/work/work_roster.md', text)
    packet = k.gather('Ann', vault)
    assert any('Ann owns hiring.' in span['quote'] for span in packet['evidence'])


def test_ambiguous_short_link_is_not_silently_bound_to_wrong_subject(tmp_path: Path) -> None:
    vault = tmp_path / 'vault'
    _note(vault, 'wiki/projects/project_boreal.md', '# Boreal\n\n[[atlas]]\n')
    _note(vault, 'wiki/projects/atlas.md', '# Atlas\n\nAtlas product delivery.\n')
    _note(vault, 'wiki/work/atlas.md', '# Atlas\n\nAtlas unrelated work.\n')
    packet = k.gather('Atlas', vault, topic_note='wiki/projects/project_boreal.md')
    assert {s['relative_path'] for s in packet['sources']} == {'wiki/projects/project_boreal.md'}
    assert 'ambiguous_note_link' in packet['reasons']
    _note(vault, 'wiki/projects/project_boreal.md', '# Boreal\n\n[[wiki/projects/atlas.md]]\n')
    qualified = k.gather('Atlas', vault, topic_note='wiki/projects/project_boreal.md')
    assert 'wiki/projects/atlas.md' in {s['relative_path'] for s in qualified['sources']}


def test_incomplete_freshness_recheck_is_unavailable_not_claimed_changed(tmp_path: Path, monkeypatch) -> None:
    vault = tmp_path / 'vault'
    _note(vault, 'wiki/projects/project_atlas.md', '# Atlas\n\nAtlas is pending.\n')
    packet = k.gather('Atlas', vault)
    original = k._candidate_note_files
    def truncated(*args, **kwargs):
        result = list(original(*args, **kwargs));result[1] = True;return tuple(result)
    monkeypatch.setattr(k, '_candidate_note_files', truncated)
    with pytest.raises(k.UnavailableState, match='inconclusive'):
        k._check_candidate_inventory_freshness(packet, vault)


@pytest.mark.parametrize('which', ['root', 'outbox'])
def test_symlinked_provisional_scope_fails_closed(tmp_path: Path, which: str) -> None:
    vault = tmp_path / 'vault'
    _note(vault, 'wiki/projects/project_atlas.md', '# Atlas\n\nAtlas is pending.\n')
    state = tmp_path / 'state'
    outside = tmp_path / 'outside';outside.mkdir()
    if which == 'root':
        state.symlink_to(outside, target_is_directory=True)
    else:
        state.mkdir();(state/'outbox').symlink_to(outside, target_is_directory=True)
    with pytest.raises(k.UnavailableState, match='symlink'):
        k.gather('Atlas', vault, provisional_root=state)


def test_current_summary_survives_keyword_rich_historical_details(tmp_path: Path) -> None:
    vault = tmp_path / 'vault'
    _note(vault, 'wiki/projects/project_atlas.md',
          '# Atlas delivery\n\n## Current Status\n\n'
          'Atlas remains pending acceptance; no launch has been approved.\n\n'
          '## Historical trial\n\n' + 'Atlas delivery launch status trial succeeded. ' * 30)
    packet = k.gather('Atlas delivery launch status', vault, max_evidence=1)
    assert any('no launch has been approved' in e['quote'] for e in packet['evidence'])


def test_bilingual_query_retains_corrected_english_summary_with_original_context(tmp_path: Path) -> None:
    vault = tmp_path / 'vault'
    _note(vault, 'wiki/work/work_harbour.md',
          '# Harbour / 海港\n\n## Current Status\n\n'
          'The original target was April 2025, corrected after checking the record.\n\n'
          '## Draft correspondence\n\n' + '\n\n'.join(
              f'海港上线时间是什么，怎样更正：第 {i} 份未发送草稿。' for i in range(14)))
    packet = k.gather('海港原来上线时间，后来怎样更正？', vault, max_evidence=4)
    quotes = '\n'.join(e['quote'] for e in packet['evidence'])
    assert 'April 2025' in quotes
    assert 'Current Status' in quotes


def test_short_answer_passage_outranks_long_incidental_provenance(tmp_path: Path) -> None:
    vault = tmp_path / 'vault'
    _note(vault, 'wiki/projects/project_atlas.md',
          '# Atlas launch\n\n## Current Status\n\n'
          'The first two November totals are 31 and 33.\n\n'
          '## Provenance\n\n' + ('Atlas launch first two November totals revision file path. ' * 80))
    packet = k.gather('Atlas launch first two November totals', vault, max_evidence=1)
    assert '31 and 33' in packet['evidence'][0]['quote']


def test_historical_answer_keeps_earlier_proposal_and_current_correction(tmp_path: Path) -> None:
    vault = tmp_path / 'vault'
    _note(vault, 'wiki/work/work_atlas.md',
          '# Atlas\n\n## Current Status\n\n'
          'The dependency remains necessary; that option was not accepted.\n\n'
          '## Historical proposal\n\nAtlas initially proposed removing the dependency.\n')
    packet = k.gather('Atlas historical removal proposal', vault, max_evidence=3)
    quotes = '\n'.join(e['quote'] for e in packet['evidence'])
    assert 'initially proposed removing' in quotes
    assert 'that option was not accepted' in quotes
    assert 'Historical proposal' in quotes


def test_leading_note_can_keep_answer_scope_and_current_context_together(tmp_path: Path) -> None:
    vault = tmp_path / 'vault'
    _note(vault, 'wiki/projects/project_atlas_release.md',
          '# Atlas release readiness\n\n## Current Status\n\n'
          'Atlas release readiness requires acceptance.\n\n'
          'Atlas release remains private.\n\n'
          'Atlas release has no publication approval.\n\n'
          'Atlas release needs a fresh owner check.\n')
    for i in range(12):
        _note(vault, f'wiki/work/work_log{i}.md', f'# Log {i}\n\nAtlas release was discussed.\n')
    memory = tmp_path / 'state/memory.json'
    for i in range(90):
        value = f'Atlas release discussion item {i} remains open.'
        _apply(memory, _bundle(key=f'memory.fact.atlas_{i}.aaaa', value=value, quote=value,
                             event_id=f'ev-{i}', cursor=f'turn:{i}'))
    _write_published_memory(vault, memory)
    packet = k.gather('Atlas release readiness', vault)
    ids = {s['source_id'] for s in packet['sources'] if s['relative_path']=='wiki/projects/project_atlas_release.md'}
    quotes = '\n'.join(e['quote'] for e in packet['evidence'] if e['source_id'] in ids)
    assert all(marker in quotes for marker in ['requires acceptance', 'remains private', 'no publication approval', 'fresh owner check'])
