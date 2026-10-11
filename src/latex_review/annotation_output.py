"""Small reusable projections for high-level annotation CLI output."""


def write_summary(value, thread_id=None, message_id=None):
    result = value.get('result') or {}
    return {'success': True, 'thread_id': result.get('thread_id', thread_id),
            'message_id': result.get('message_id', message_id), 'revision': value.get('revision'),
            'version_id': value.get('version_id'), 'request_id': value.get('request_id')}


def add_read_options(parser):
    parser.add_argument('--summary', action='store_true')
    parser.add_argument('--fields', help='逗号分隔的线程或正文单元字段')
    parser.add_argument('--limit', type=int)
    parser.add_argument('--offset', type=int, default=0)
    parser.add_argument('--thread-id')
    parser.add_argument('--unit-id')
    parser.add_argument('--status', choices=['resolved', 'unresolved', 'exact', 'auto_migrated', 'manual', 'needs_relocation'])
    parser.add_argument('--author', help='包含此作者消息的线程')
    parser.add_argument('--kind', choices=['text', 'formula'])


def read_projection(value, args):
    key = 'units' if 'units' in value else 'threads'
    items = value[key]
    selected = []
    for item in items:
        anchor = (item.get('association') or {}).get('anchor') or item.get('original_anchor') or {}
        if args.thread_id and (key != 'threads' or item['id'] != args.thread_id):
            continue
        if args.unit_id and (item['id'] if key == 'units' else anchor.get('unit_id')) != args.unit_id:
            continue
        if args.kind and (item.get('kind') if key == 'units' else anchor.get('kind')) != args.kind:
            continue
        if args.status:
            status = ('resolved' if item.get('resolved') else 'unresolved') if args.status in ('resolved', 'unresolved') else (item.get('association') or {}).get('status')
            if key == 'units' or status != args.status:
                continue
        if args.author and not any(m['author']['name'] == args.author for m in item.get('messages', []) if not m.get('deleted_at')):
            continue
        selected.append(item)
    fields = [field.strip() for field in args.fields.split(',')] if args.fields else None
    known = set().union(*(item.keys() for item in items)) if items else ({'id', 'kind', 'source', 'section_path', 'raw_latex', 'text', 'atoms', 'order'} if key == 'units' else {'id', 'resolved', 'messages', 'original_anchor', 'association', 'updated_at', 'created_at'})
    if fields and any(not field or field not in known for field in fields):
        raise ValueError('字段名称无效')
    if args.summary and fields is None:
        fields = ['id', 'kind', 'source', 'section_path'] if key == 'units' else ['id', 'resolved', 'updated_at']
    paged = selected[args.offset:] if args.limit is None else selected[args.offset:args.offset + args.limit]
    if fields:
        paged = [{field: item.get(field) for field in fields} for item in paged]
    result = {**value, key: paged}
    if any((args.summary, args.fields, args.limit is not None, args.offset, args.thread_id, args.unit_id, args.status, args.author, args.kind)):
        result.update(total_matched=len(selected), offset=args.offset, limit=args.limit)
    return result
