"""面向智能体的报告查询、范围核对和验收声明。"""
from __future__ import annotations

import argparse
import fnmatch
import json
from pathlib import Path, PurePosixPath, PureWindowsPath

from .contract import validate_document


def emit(value: dict) -> None:
    print(json.dumps(value, ensure_ascii=False, sort_keys=True))


def report_root(value: str) -> Path:
    path = Path(value).expanduser()
    return path if path.is_dir() else path.parent


def load_report(value: str) -> dict:
    path = Path(value).expanduser()
    target = path / 'diff.json' if path.is_dir() else path
    data = json.loads(target.read_text(encoding='utf-8'))
    if not isinstance(data, dict) or 'entry' not in data:
        raise ValueError('需要有效的 diff.json')
    validate_document(data)
    return data


def pattern(value: str) -> str:
    if not value or PurePosixPath(value).is_absolute() or PureWindowsPath(value).drive or '\\' in value or '..' in value.split('/'):
        raise argparse.ArgumentTypeError('范围必须是项目内的相对 POSIX 路径或 glob')
    return value.removeprefix('./')


def matches(file: str, patterns: list[str]) -> bool:
    # fnmatch 的 * 可以跨 /；**/ 也允许零层目录。
    return any(fnmatch.fnmatchcase(file, p) or
               (p.startswith('**/') and fnmatch.fnmatchcase(file, p[3:])) for p in patterns)


def parser(prog: str) -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(prog=prog)
    result.add_argument('report', help='报告目录或 diff.json')
    return result


def error(exc: Exception) -> int:
    emit({'error': str(exc)})
    return 4


def query_main(argv: list[str] | None = None) -> int:
    p = parser('latex-review-query')
    p.add_argument('--file', action='append', type=pattern, default=[])
    p.add_argument('--section', action='append', default=[])
    p.add_argument('--category', action='append', choices=['text', 'equation', 'figure', 'table', 'citation', 'comment', 'move'], default=[])
    p.add_argument('--change-id', action='append', default=[])
    p.add_argument('--include-raw', action='store_true')
    p.add_argument('--limit', type=int, default=50)
    p.add_argument('--offset', type=int, default=0)
    args = p.parse_args(argv)
    if args.limit < 1 or args.offset < 0:
        p.error('limit 必须大于零，offset 不得小于零')
    try:
        data = load_report(args.report)
        nodes = {side: {n['id']: n for n in data['nodes_' + side]} for side in ('old', 'new')}
        selected = []
        for c in data['changes']:
            paired = {side: nodes[side].get(c[side + '_node_id']) for side in ('old', 'new')}
            categories = sorted(set(c['categories']) | {d['category'] for d in c['details']})
            if args.file and not any(s and s['file'] and matches(s['file'], args.file) for s in (c['source_old'], c['source_new'])):
                continue
            if args.section and not any(n and any(s in n['section_path'] or s == '/'.join(n['section_path']) for s in args.section) for n in paired.values()):
                continue
            if args.category and not set(args.category).intersection(categories):
                continue
            if args.change_id and c['id'] not in args.change_id:
                continue
            item = {k: c[k] for k in ('id', 'kind', 'node_type', 'summary', 'source_old', 'source_new', 'matching_confidence')}
            item['categories'] = categories
            item['section_old'] = paired['old']['section_path'] if paired['old'] else None
            item['section_new'] = paired['new']['section_path'] if paired['new'] else None
            if args.include_raw:
                item['raw_old'] = paired['old']['raw_latex'] if paired['old'] else None
                item['raw_new'] = paired['new']['raw_latex'] if paired['new'] else None
                item['details'] = c['details']
            selected.append(item)
        emit({'schema_version': '1.0', 'summary': data['summary'],
              'diagnostic_counts': {level: sum(d['severity'] == level for d in data['diagnostics'])
                                    for level in ('info', 'warning', 'error')},
              'total_matched': len(selected), 'limit': args.limit, 'offset': args.offset,
              'changes': selected[args.offset:args.offset + args.limit]})
        return 0
    except Exception as exc:
        return error(exc)


def scope_main(argv: list[str] | None = None) -> int:
    p = parser('latex-review-check-scope')
    p.add_argument('--allow', action='append', type=pattern, required=True, help='允许的文件路径或 glob，可重复')
    p.add_argument('--strict', action='store_true')
    args = p.parse_args(argv)
    try:
        data = load_report(args.report)
        groups = {'inside': [], 'outside': [], 'unknown': []}
        for c in data['changes']:
            sides = ('new',) if c['kind'] == 'added' else ('old',) if c['kind'] == 'removed' else ('old', 'new')
            outside, unknown = [], []
            for side in sides:
                loc = c['source_' + side]
                if not loc or not loc['file'] or loc['confidence'] < 1 or loc['uncertainty_reason']:
                    unknown.append(side)
                if loc and loc['file'] and not matches(loc['file'], args.allow):
                    outside.append(side)
            status = 'outside' if outside else 'unknown' if unknown else 'inside'
            groups[status].append({'id': c['id'], 'kind': c['kind'], 'source_old': c['source_old'],
                                   'source_new': c['source_new'], 'outside_sides': outside, 'unknown_sides': unknown})
        emit({'schema_version': '1.0', 'allowed': args.allow, 'counts': {k: len(v) for k, v in groups.items()}, **groups})
        return 2 if args.strict and (groups['outside'] or groups['unknown']) else 0
    except Exception as exc:
        return error(exc)


def acceptance_main(argv: list[str] | None = None) -> int:
    p = parser('latex-review-record-acceptance')
    p.add_argument('--status', required=True, choices=['passed', 'failed', 'incomplete'])
    p.add_argument('--evidence', action='append', required=True, help='已存在的证据文件，可重复')
    p.add_argument('--note', default='')
    args = p.parse_args(argv)
    try:
        from .run_manifest import write_manifest, artifact_hash
        root = report_root(args.report)
        load_report(str(root))
        if not (root / 'report.html').is_file():
            raise ValueError('报告入口不存在，无法记录浏览器验收')
        target = root / 'run.json'
        data = json.loads(target.read_text(encoding='utf-8'))
        if data.get('schema_version') != '1.0' or data.get('report_fingerprint') != artifact_hash(root):
            raise ValueError('运行清单版本无效或报告已变化，请重新生成报告')
        evidence = []
        for value in args.evidence:
            path = Path(value).expanduser().resolve(strict=True)
            if not path.is_file():
                raise ValueError('证据必须是已有文件')
            evidence.append(str(path))
        data['browser_acceptance'] = {'status': args.status, 'evidence': evidence, 'note': args.note,
                                      'kind': 'agent_declaration'}
        write_manifest(root, data)
        emit(data['browser_acceptance'])
        return 0
    except Exception as exc:
        return error(exc)


def annotations_main(argv: list[str] | None = None) -> int:
    """Read content/threads or execute one version-checked JSON request."""
    from .annotations import AnnotationStore, execute_request, load_content
    import sys
    p = parser('latex-review-annotations')
    p.add_argument('--project-root', type=Path, help='论文项目根目录，须与报告身份一致')
    mode = p.add_mutually_exclusive_group()
    mode.add_argument('--content', action='store_true', help='读取全部当前正文及公式定位坐标')
    mode.add_argument('--export', action='store_true', help='导出含删除记录的批注备份')
    mode.add_argument('--request', help='JSON 请求文件；- 表示标准输入')
    args = p.parse_args(argv)
    try:
        content = load_content(args.report)
        store = AnnotationStore.for_content(content, args.project_root)
        if args.content:
            value = content
        elif args.export:
            value = store.export()
        elif args.request:
            raw = sys.stdin.read() if args.request == '-' else Path(args.request).read_text(encoding='utf-8')
            if len(raw.encode('utf-8')) > 8 * 1024 * 1024:
                raise ValueError('批注请求超过 8 MiB')
            value = execute_request(store, content, json.loads(raw))
        else:
            value = store.read(content)
        emit(value)
        return 0
    except Exception as exc:
        return error(exc)
