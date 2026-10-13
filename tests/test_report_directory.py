"""Report updates preserve recoverable generations across filesystem failures."""
import os
from pathlib import Path

import pytest

from latex_review.local_reports import copy_report
from latex_review.report_directory import report_directory


def report(path, text):
    path.mkdir(exist_ok=True)
    (path / 'report.html').write_text(text)


@pytest.mark.parametrize('keep_previous', [False, True])
def test_commit_retains_only_requested_previous_generation(tmp_path, keep_previous):
    output = tmp_path / 'report'
    report(output, 'old')
    with report_directory(output, keep_previous=keep_previous) as candidate:
        report(candidate.directory, 'new')
        assert (output / 'report.html').read_text() == 'old'
    assert (output / 'report.html').read_text() == 'new'
    assert not candidate.directory.exists()
    backups = list(tmp_path.glob('.report.before-*'))
    if keep_previous:
        assert backups == [candidate.previous_report]
        assert (candidate.previous_report / 'report.html').read_text() == 'old'
    else:
        assert backups == [] and candidate.previous_report is None


@pytest.mark.parametrize('existing', [False, True])
def test_first_generation_or_copy_into_prepared_candidate(tmp_path, existing):
    source, output = tmp_path / 'source', tmp_path / 'report'
    report(source, 'new')
    if existing:
        report(output, 'old')
    with report_directory(output) as candidate:
        copy_report(source, candidate.directory, allow_empty=True)
    assert (output / 'report.html').read_text() == 'new'
    assert not list(tmp_path.glob('.report.*'))


@pytest.mark.parametrize('error', [ValueError, KeyboardInterrupt])
def test_generation_or_validation_failure_keeps_old_and_cleans_candidate(tmp_path, error):
    output = tmp_path / 'report'
    report(output, 'old')
    with pytest.raises(error):
        with report_directory(output) as candidate:
            report(candidate.directory, 'incomplete')
            raise error('generation failed')
    assert (output / 'report.html').read_text() == 'old'
    assert not list(tmp_path.glob('.report.*'))


@pytest.mark.parametrize('failure', ['backup', 'install', 'rollback'])
def test_rename_failures_restore_or_preserve_recoverable_old_report(tmp_path, monkeypatch, failure):
    output = tmp_path / 'report'
    report(output, 'old')
    replace = os.replace

    def fail_replace(source, destination):
        source = Path(source)
        if (failure == 'backup' and source == output
                or failure in ('install', 'rollback') and '.stage-' in source.name
                or failure == 'rollback' and '.before-' in source.name):
            raise OSError('injected rename failure')
        return replace(source, destination)

    monkeypatch.setattr('latex_review.report_directory.os.replace', fail_replace)
    with pytest.raises(OSError) as raised:
        with report_directory(output) as candidate:
            report(candidate.directory, 'new')
    assert not candidate.directory.exists()
    backups = list(tmp_path.glob('.report.before-*'))
    if failure == 'rollback':
        assert not output.exists() and len(backups) == 1
        assert (backups[0] / 'report.html').read_text() == 'old'
        assert str(backups[0]) in str(raised.value)
    else:
        assert (output / 'report.html').read_text() == 'old'
        assert backups == []


def test_copy_rejects_existing_content_even_when_empty_destination_is_allowed(tmp_path):
    source, destination = tmp_path / 'source', tmp_path / 'destination'
    report(source, 'new')
    report(destination, 'keep')
    with pytest.raises(ValueError, match='候选报告目录已存在'):
        copy_report(source, destination, allow_empty=True)
    assert (destination / 'report.html').read_text() == 'keep'
