"""Synthetic fixture invariants independent of the new writing-check implementation."""
from pathlib import Path
import shutil
import tomllib

from latex_review.contract import ComparisonSource
from latex_review.sources import ProjectSource
from latex_review.structure import parse_project
from latex_review.rules import check_rules
from latex_review.project_profile import validate

FIXTURE = Path(__file__).parent / 'fixtures/writing_checks'


def source(tmp_path):
    root = tmp_path / 'paper'
    shutil.copytree(FIXTURE / 'paper', root)
    return ProjectSource('new', root, 'main.tex', ComparisonSource('directory', str(root)))


def test_fixture_preserves_crlf_unicode_and_repeated_include_origins(tmp_path):
    project = source(tmp_path).expand()
    raw = project.source_map.files['shared/crlf.tex']
    assert raw.count('\r\n') == 2
    assert 'café 中文 🧪' in raw
    needle = 'Repeated observation:'
    start = 0
    origins = []
    while (start := project.text.find(needle, start)) >= 0:
        mapped = project.origin_ranges(start, start + len(needle))
        assert len(mapped) == 1
        assert mapped[0].origin.file == 'shared/observation.tex'
        assert mapped[0].origin.confidence == 'exact'
        origins.append(mapped[0].origin)
        start += len(needle)
    assert len(origins) == 2
    assert origins[0].include_instance != origins[1].include_instance
    assert (origins[0].start, origins[0].end) == (origins[1].start, origins[1].end)
    start = project.text.index('café 中文 🧪', project.text.index('CRLF source:'))
    mapped = project.origin_ranges(start, start + len('café 中文 🧪'))
    assert len(mapped) == 1
    origin = mapped[0].origin
    assert raw[origin.start:origin.end] == 'café 中文 🧪'


def test_fixture_baseline_findings_and_verbatim_negative_control(tmp_path):
    parsed = parse_project(source(tmp_path).expand(), include_title=True)
    findings = check_rules(parsed, parsed)
    assert {item.code for item in findings} == {
        'bibliography_key_unresolved', 'unresolved_reference',
        'figure_unreferenced', 'table_unreferenced'}
    assert all(item.rule_status == 'existing' for item in findings)
    assert all('verbatim-only' not in item.message and 'comment-only' not in item.message for item in findings)


def test_fixture_read_only_profile_declares_independent_entries():
    profile = tomllib.loads((FIXTURE / 'read-only.toml').read_text())
    validate(profile)
    assert profile['project'] == {'read_only': True, 'entries': ['main.tex', 'supplement.tex']}
    assert all((FIXTURE / 'paper' / entry).is_file() for entry in profile['project']['entries'])
