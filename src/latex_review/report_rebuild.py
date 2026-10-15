"""Saved report recipes and staged rebuilding after explicit source edits."""
from copy import deepcopy
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from threading import local

_held = local()

RECIPE = '.rebuild/recipe.json'


@contextmanager
def rebuild_lock(report):
    report = Path(report).resolve()
    lock = report.parent / ('.' + report.name + '.rebuild.lock')
    held = getattr(_held, 'paths', set())
    if str(lock) in held:
        yield
        return
    lock.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(lock, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        _held.paths = held | {str(lock)}
        yield
    finally:
        _held.paths = held
        os.close(fd)


def save_recipe(output, old, new, configuration):
    from .cache import dependency_fingerprints
    output = Path(output)
    recipe = {'schema_version': '1.0', 'new_kind': new.source.identity.kind,
              'new_root': str(new.source.worktree_origin or new.source.root), 'entry': new.source.entry,
              'configuration': deepcopy(configuration)}
    recipe['configuration'].pop('translation', None)
    if new.source.identity.kind == 'git':
        recipe.update(available=False, reason='Git 历史比较版本只读')
    elif old.source.identity.kind == 'git':
        recipe.update(available=True, mode='git', old_revision=old.source.identity.identifier)
    else:
        snapshot = output / '.rebuild' / 'old'
        for relative in dependency_fingerprints(old):
            original = old.source.root / relative
            target = snapshot / relative
            if not original.resolve().is_relative_to(old.source.root.resolve()):
                raise ValueError('重建基线依赖越过来源目录')
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(original.read_bytes())
        # Local class/style files may be read only by an explicitly enabled TeX engine.
        for original in old.source.root.iterdir():
            if original.suffix in ('.sty', '.cls', '.bst') and original.is_file() and not original.is_symlink():
                snapshot.mkdir(parents=True, exist_ok=True)
                shutil.copy2(original, snapshot / original.name)
        recipe.update(available=True, mode='file' if old.source.identity.kind == 'file' else 'directory',
                      old_entry=old.source.entry, old_snapshot='.rebuild/old')
    target = output / RECIPE
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(recipe, ensure_ascii=False, indent=2), encoding='utf-8')
    return recipe


def save_live_recipe(output, source):
    target = Path(output) / RECIPE
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps({'schema_version': '1.0', 'available': True, 'mode': 'live',
                                 'new_root': str(source.root), 'entry': source.entry}), encoding='utf-8')


def replay(recipe_path, output, original_report):
    """Run inside a fresh Python process, without changing the server's cwd."""
    recipe_path, output = Path(recipe_path), Path(output)
    recipe = json.loads(recipe_path.read_text(encoding='utf-8'))
    if recipe.get('schema_version') != '1.0' or not recipe.get('available'):
        raise ValueError(recipe.get('reason') or '报告缺少有效重建配方')
    root = Path(recipe['new_root']).resolve()
    entry = root / recipe['entry']
    if not entry.resolve().is_relative_to(root):
        raise ValueError('重建入口越过论文目录')
    if recipe['mode'] == 'live':
        from .live import build, _write
        from .contract import ComparisonSource
        from .sources import ProjectSource
        try:
            revision = json.loads((Path(original_report) / 'live-status.json').read_text())['revision'] + 1
        except (OSError, ValueError, KeyError):
            revision = 1
        output.mkdir(parents=True, exist_ok=True)
        build(ProjectSource('new', root, recipe['entry'], ComparisonSource('directory', str(root))), output, revision)
        _write(output / 'live-status.json', json.dumps({'revision': revision, 'message': '已应用源码建议并更新预览。'}))
        return 0
    from .cli import main
    if recipe['mode'] == 'git':
        argv = [str(entry), '--old', recipe['old_revision'], '--new', 'worktree']
    else:
        old_root = (recipe_path.parent.parent / recipe['old_snapshot']).resolve()
        if not old_root.is_relative_to(recipe_path.parent.parent.resolve()):
            raise ValueError('重建基线快照路径无效')
        if recipe['mode'] == 'file':
            argv = [str(old_root / recipe['old_entry']), str(entry)]
        elif recipe['mode'] == 'directory':
            argv = ['--old-dir', str(old_root), '--new-dir', str(root), '--entry', recipe['entry']]
        else:
            raise ValueError('未知重建来源模式')
    argv += ['--output', str(output), '--no-cache']
    return main(argv, configuration=recipe['configuration'], source_exclusions=(Path(original_report),))


def execute_on_report(session, envelope, report, *, progress=None, incremental=False):
    """Execute an annotation request against a report; rebuild the report when the paper source changed.

    The rebuild replaces translation-units.json, which translation sessions notice on their own.
    """
    value = session.execute(envelope, progress=progress, incremental=incremental)
    if (value.get('result') or {}).get('source_changed'):
        if progress:
            progress('rebuild', 0, 0)
        value['result']['rebuild'] = rebuild_report(report)
    return value


def rebuild_report(report):
    report = Path(report).expanduser()
    report = (report if report.is_dir() else report.parent).resolve()
    try:
        from .output_leases import output_lease
        with output_lease(report), rebuild_lock(report):
            return _rebuild_report(report)
    except OSError as exc:
        return {'status': 'pending', 'message': '源码已修改，报告待更新：' + str(exc)}


def _rebuild_report(report):
    report = Path(report).resolve()
    recipe_path = report / RECIPE
    if not recipe_path.is_file():
        return {'status': 'pending', 'message': '源码已修改，报告待更新：旧报告缺少重建配方。'}
    try:
        from .report_directory import report_directory
        with report_directory(report, keep_previous=True) as candidate:
            stage = candidate.directory
            code = ('from latex_review.report_rebuild import replay; import sys; '
                    'sys.exit(replay(sys.argv[1], sys.argv[2], sys.argv[3]))')
            from .local_tasks import OWNER_FD
            inherited = ()
            if OWNER_FD is not None:
                code = ('from latex_review.local_tasks import inherit_owner; '
                        f'inherit_owner({OWNER_FD}); ' + code)
                inherited = (OWNER_FD,)
            result = subprocess.run([sys.executable, '-c', code, str(recipe_path), str(stage), str(report)],
                                    capture_output=True, text=True, pass_fds=inherited)
            from .annotations import load_content
            if result.returncode not in (0, 2) or not (stage / 'report.html').is_file():
                raise ValueError((result.stderr or result.stdout or '报告重建失败')[-800:])
            load_content(stage)  # A source fallback cannot provide the new annotation identity.
            manifest = stage / 'run.json'
            if manifest.exists():
                data = json.loads(manifest.read_text(encoding='utf-8'))
                data['artifacts'] = {name: str((report / name).resolve()) for name in data.get('artifacts', {})}
                from .run_manifest import write_manifest
                write_manifest(stage, data)
        return {'status': 'updated', 'message': '源码已修改，报告已更新。',
                'report_directory': str(report), 'previous_report': str(candidate.previous_report)}
    except (OSError, ValueError, TypeError, KeyError) as exc:
        return {'status': 'pending', 'message': '源码已修改，报告待更新：' + str(exc)}
