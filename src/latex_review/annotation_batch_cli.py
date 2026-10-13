"""Batch CLI input, saved review snapshots, and post-write verification."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

from .annotation_audit import audit
from .annotation_cli import SelectionError, find_selection
from .annotations import AnnotationError, _atomic_write, _keys


def add_batch_commands(commands):
    batch = commands.add_parser('batch', help='批量添加、回复、解决、重新打开或更新元数据；默认提交成功项')
    batch.add_argument('file', help='JSON 文件；- 表示标准输入')
    batch.add_argument('--request-id', help='唯一请求 ID；也可在 JSON 对象中提供')
    batch.add_argument('--expected-revision', type=int)
    batch.add_argument('--author', help='未指定作者的项使用此智能体名称')
    batch.add_argument('--atomic', action='store_true', help='整批校验通过才提交')
    batch.add_argument('--dry-run', action='store_true', help='保存预检缓存，不提交批注或消耗请求 ID')
    batch.add_argument('--preflight-token', help='指定预检返回的令牌；默认复用相同请求 ID 的预检')
    batch.add_argument('--quiet-progress', action='store_true', help='关闭 stderr 阶段进度')
    batch.add_argument('--snapshot', type=Path, help='复核快照文件；默认保存到项目共享存储旁')
    batch.add_argument('--full', action='store_true', help='返回完整批量结果及开始清单')
    batch.add_argument('--review-record', type=Path, help='提交后导出独立 Markdown 审查记录，路径须以 .md 结尾')
    check = commands.add_parser('audit', help='复核保存的批量操作并报告运行期间新增批注')
    check.add_argument('snapshot', type=Path)


def _read_json(path):
    import sys
    raw = sys.stdin.read() if path == '-' else Path(path).read_text(encoding='utf-8')
    if len(raw.encode('utf-8')) > 8 * 1024 * 1024:
        raise AnnotationError('批量文件超过 8 MiB')
    return json.loads(raw)


def prepare_batch(content, state, value, args):
    if isinstance(value, list):
        value = {'items': value}
    _keys(value, ('items', 'request_id', 'expected_revision', 'atomic'), ('items',))
    if not isinstance(value['items'], list):
        raise AnnotationError('items 须为列表')
    items = []
    for original in value['items']:
        if not isinstance(original, dict):
            raise AnnotationError('批量项须为对象')
        item = deepcopy(original)
        operation = item.get('operation')
        if operation == 'add':
            operation = item['operation'] = 'create'
        if operation in ('create', 'reply'):
            item.setdefault('category', 'note')
            if isinstance(item.get('author'), str):
                item['author'] = {'name': item['author'], 'type': 'agent'}
            if 'author' not in item and args.author:
                item['author'] = {'name': args.author, 'type': 'agent'}
        if operation == 'create' and 'selection' not in item:
            locator = SimpleNamespace(quote=item.pop('quote', None), formula=item.pop('formula', False),
                                      unit_id=item.pop('unit_id', None), occurrence=item.pop('occurrence', None),
                                      file=item.pop('file', None), line=item.pop('line', None))
            try:
                item['selection'] = find_selection(content, locator)
            except SelectionError as exc:
                item = {'id': item.get('id'), 'operation': 'create',
                        'error': {'code': 'location', 'message': str(exc), 'candidates': exc.candidates}}
        if operation == 'reply' and 'parent_message_id' in item:
            item['reply_to'] = item.pop('parent_message_id')
        items.append(item)
    revision = args.expected_revision if args.expected_revision is not None else value.get('expected_revision', state['revision'])
    request = {'operation': 'batch', 'request_id': args.request_id or value.get('request_id'),
               'expected_revision': revision, 'atomic': args.atomic or value.get('atomic', False),
               'dry_run': args.dry_run, 'items': items}
    if args.preflight_token:
        request['preflight_token'] = args.preflight_token
    return request


def run_batch(store, content, args):
    import sys
    labels = {'read': '读取批注', 'lock': '等待存储锁', 'validate': '检查版本',
              'preflight': '预检', 'apply': '应用', 'reuse': '复用预检', 'write': '写入',
              'audit': '审计', 'complete': '完成'}
    def progress(phase, completed, total):
        if not args.quiet_progress:
            print(f"{labels.get(phase, phase)} {completed}/{total}", file=sys.stderr, flush=True)
    progress('read', 0, 0)
    before = store.inventory(content)
    submitted = _read_json(args.file)
    request = prepare_batch(content, before, submitted, args)
    originals = submitted if isinstance(submitted, list) else submitted['items']
    if args.review_record and (args.dry_run or args.review_record.suffix.lower() != '.md' or args.review_record.is_symlink()):
        raise AnnotationError('--review-record 只用于提交，须为非符号链接的 .md 文件')
    request_id = request['request_id']
    if not isinstance(request_id, str) or not request_id.strip():
        raise AnnotationError('批量写入需要 --request-id 或 JSON request_id')
    filename = hashlib.sha256(request_id.encode('utf-8')).hexdigest() + '.json'
    snapshot_path = args.snapshot or store.directory.parent / 'annotation-batches' / filename
    snapshot_path = snapshot_path.expanduser()
    if snapshot_path.is_symlink():
        raise AnnotationError('快照文件不可为符号链接')
    snapshot_path = snapshot_path.parent.resolve() / snapshot_path.name
    if args.review_record and args.review_record.expanduser().resolve() == snapshot_path:
        raise AnnotationError('审查记录不能覆盖批量快照')
    snapshot = {'project_id': content['project_id'], 'version_id': content['version_id'],
                'request_id': request_id, 'before': before, 'expected_items': request['items'],
                'submitted_items': originals}
    if not args.dry_run:
        if snapshot_path.exists():
            saved = json.loads(snapshot_path.read_text(encoding='utf-8'))
            if saved.get('project_id') != content['project_id'] or saved.get('request_id') != request_id:
                raise AnnotationError('快照文件属于其他项目或请求')
            if saved.get('expected_items') != request['items']:
                raise AnnotationError('请求 ID 已用于不同批量内容；请保留原快照并使用新 ID')
            # Keep the original review inventory across idempotent retries.
            snapshot['before'] = saved['before']
        snapshot_path.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write(snapshot_path, snapshot)
    result = store.batch(content, request, progress=progress)
    if args.dry_run:
        preflight_path = snapshot_path.with_suffix('.preflight.json')
        if preflight_path.is_symlink():
            raise AnnotationError('预检文件不可为符号链接')
        snapshot.update(result=result)
        preflight_path.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write(preflight_path, snapshot)
        result = {**result, 'preflight_file': str(preflight_path)}
    if not args.dry_run:
        progress('audit', 0, len(request['items']))
        successful = {item['id'] for item in result['results'] if item['ok']}
        expected = [item for item in request['items'] if item['id'] in successful]
        verification = audit(snapshot['before'], store.inventory(content), result, expected_items=expected)
        snapshot.update(result=result, audit=verification)
        _atomic_write(snapshot_path, snapshot)
        result = {**result, 'audit': verification, 'snapshot_file': str(snapshot_path.resolve())}
        if args.review_record:
            from .annotation_review_record import write_record
            try:
                result['review_record'] = write_record(args.review_record, content, request, result, store.read(content), originals)
            except OSError as exc:
                result['review_record_error'] = str(exc)
        progress('complete', len(request['items']), len(request['items']))
    if not args.full:
        result = {**result, 'start_thread_count': len(result.get('start_thread_ids', []))}
        result.pop('start_thread_ids', None)
    return result


def run_audit(store, content, path):
    snapshot = _read_json(str(path))
    if snapshot.get('project_id') != content['project_id']:
        raise AnnotationError('复核快照属于其他论文项目')
    result = snapshot.get('result')
    if result is None:
        raise AnnotationError('快照没有成功返回的批量收据；请用相同请求 ID 核对原请求')
    successful = {item['id'] for item in result['results'] if item['ok']}
    expected = [item for item in snapshot['expected_items'] if item['id'] in successful]
    return audit(snapshot['before'], store.inventory(content), result, expected_items=expected)
