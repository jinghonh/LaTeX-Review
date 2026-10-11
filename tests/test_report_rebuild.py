import json
from pathlib import Path
import subprocess
import pytest

from latex_review.cli import main
from latex_review.annotations import AnnotationStore, load_content
from latex_review.report_rebuild import rebuild_report


def make_report(tmp_path, mode='directory'):
    old, new, output = (tmp_path / name for name in ('old', 'new', 'report'))
    old.mkdir(); new.mkdir()
    template = r'\documentclass{article}\begin{document}%s\end{document}'
    (old / 'main.tex').write_text(template % 'Original text.')
    (new / 'main.tex').write_text(template % 'Current text.')
    args = (['--old-dir', str(old), '--new-dir', str(new), '--entry', 'main.tex'] if mode == 'directory'
            else [str(old / 'main.tex'), str(new / 'main.tex')])
    assert main([*args, '--output', str(output)]) == 0
    return old, new, output


@pytest.mark.parametrize('mode', ['directory', 'file'])
def test_rebuild_fixes_baseline_and_preserves_annotations(tmp_path, mode):
    old, new, output = make_report(tmp_path, mode)
    content = load_content(output)
    unit = next(u for u in content['units'] if u['kind'] == 'text')
    store = AnnotationStore.for_content(content)
    created = store.dispatch(content, {'operation': 'create', 'expected_revision': 0,
        'selection': {'unit_id': unit['id'], 'start': 0, 'end': 7}, 'body': 'Keep this.',
        'author': {'name': 'A', 'type': 'user'}, 'category': 'note'})
    old.joinpath('main.tex').write_text('Wrong baseline.')
    new.joinpath('main.tex').write_text(r'\documentclass{article}\begin{document}Updated text.\end{document}')
    result = rebuild_report(output / 'report.html')
    assert result['status'] == 'updated', result
    assert Path(result['previous_report']).joinpath('report.html').exists()
    diff = json.loads(output.joinpath('diff.json').read_text())
    assert 'Original text.' in json.dumps(diff) and 'Wrong baseline.' not in json.dumps(diff)
    assert 'Updated text.' in json.dumps(diff)
    fresh = load_content(output)
    assert fresh['version_id'] != content['version_id']
    assert store.read(fresh)['threads'][0]['id'] == created['result']['thread_id']
    manifest = json.loads(output.joinpath('run.json').read_text())
    assert manifest['browser_acceptance']['status'] == 'not_executed'
    assert all(Path(path).parent == output for path in manifest['artifacts'].values())
    assert rebuild_report(output)['status'] == 'updated'


def test_git_recipe_keeps_original_commit_after_head_changes(tmp_path):
    for args in (['init', '-q'], ['config', 'user.name', 'Tester'], ['config', 'user.email', 'tester@example.invalid']):
        subprocess.run(['git', '-C', str(tmp_path), *args], check=True, capture_output=True)
    entry = tmp_path / 'main.tex'
    entry.write_text(r'\documentclass{article}\begin{document}Original text.\end{document}')
    subprocess.run(['git', '-C', str(tmp_path), 'add', 'main.tex'], check=True)
    subprocess.run(['git', '-C', str(tmp_path), 'commit', '-qm', 'original'], check=True)
    entry.write_text(r'\documentclass{article}\begin{document}Current text.\end{document}')
    output = tmp_path / 'report'
    assert main([str(entry), '--output', str(output)]) == 0
    subprocess.run(['git', '-C', str(tmp_path), 'add', 'main.tex'], check=True)
    subprocess.run(['git', '-C', str(tmp_path), 'commit', '-qm', 'new baseline'], check=True)
    entry.write_text(r'\documentclass{article}\begin{document}Updated text.\end{document}')
    assert rebuild_report(output)['status'] == 'updated'
    diff = json.loads(output.joinpath('diff.json').read_text())
    assert 'Original text.' in json.dumps(diff) and 'Updated text.' in json.dumps(diff)


def test_missing_recipe_and_failed_rebuild_preserve_previous_report(tmp_path):
    old, new, output = make_report(tmp_path)
    original = output.joinpath('report.html').read_bytes()
    new.joinpath('main.tex').unlink()
    assert rebuild_report(output)['status'] == 'pending'
    assert output.joinpath('report.html').read_bytes() == original
    output.joinpath('.rebuild/recipe.json').unlink()
    assert '缺少' in rebuild_report(output)['message']
    assert output.joinpath('report.html').read_bytes() == original
