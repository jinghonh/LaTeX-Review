"""Small reusable projections for high-level annotation CLI output."""


def write_summary(value, thread_id=None, message_id=None):
    result = value.get('result') or {}
    return {'success': True, 'thread_id': result.get('thread_id', thread_id),
            'message_id': result.get('message_id', message_id), 'revision': value.get('revision'),
            'version_id': value.get('version_id'), 'request_id': value.get('request_id')}


def add_read_options(parser):
    parser.add_argument('--summary', action='store_true')
    parser.add_argument('--full', action='store_true', help='展开完整正文、锚点和历史')
    parser.add_argument('--history', action='store_true', help='按需展开批注历史')
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
        association = item.get('association') or {}
        anchor = association.get('anchor') or association.get('container') or item.get('original_anchor') or {}
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
        if args.author and not any(m['author']['name'].casefold() == args.author.casefold() for m in item.get('messages', []) if not m.get('deleted_at')):
            continue
        selected.append(item)
    fields = [field.strip() for field in args.fields.split(',')] if args.fields else None
    known = set().union(*(item.keys() for item in items)) if items else ({'id', 'kind', 'source', 'section_path', 'raw_latex', 'text', 'atoms', 'order'} if key == 'units' else {'id', 'resolved', 'messages', 'original_anchor', 'association', 'updated_at', 'created_at'})
    if fields and any(not field or field not in known for field in fields):
        raise ValueError('字段名称无效')
    if args.summary and fields is None:
        fields = ['id', 'kind', 'source', 'section_path'] if key == 'units' else ['id', 'resolved', 'updated_at']
    compact = not args.full and fields is None
    limit = args.limit if args.limit is not None else (50 if compact or args.summary else None)
    paged = selected[args.offset:] if limit is None else selected[args.offset:args.offset + limit]
    if fields:
        paged = [{field: item.get(field) for field in fields} for item in paged]
    elif compact:
        paged = [unit_summary(item) if key == 'units' else thread_summary(item) for item in paged]
    if args.history and key == 'threads':
        paged = [{**item, 'history': original.get('history', [])} for item, original in zip(paged, selected[args.offset:])]
    result = {**value, key: paged}
    result.pop('result', None)
    result.update(total_matched=len(selected), offset=args.offset, limit=limit)
    return result


def unit_summary(unit):
    return {**{key: unit.get(key) for key in ('id', 'kind', 'source', 'section_path', 'subunit')},
            'preview': unit['text'][:160]}


def thread_summary(thread):
    association = thread['association']
    anchor = association.get('anchor') or association.get('container') or thread['original_anchor']
    messages = [m for m in thread['messages'] if not m.get('deleted_at')]
    return {**{key: thread.get(key) for key in ('id', 'resolved', 'updated_at')},
            'location_status': association['status'], 'reason': association.get('reason'),
            'unit_id': anchor.get('unit_id'), 'source': anchor.get('source'),
            'selection_deleted': (association.get('container') or {}).get('selection_deleted', False),
            'quote': thread['original_anchor']['quote'][:160], 'message_count': len(messages),
            'messages': [{'id': m['id'], 'author': m['author'], 'category': m['category'],
                          'preview': m['body'][:160]} for m in messages]}


def page_projection(value):
    """Keep UI messages/current anchors; omit duplicated historical text."""
    threads = []
    for thread in value['threads']:
        threads.append({**{k: v for k, v in thread.items() if k not in
                           ('original_anchor', 'current_anchor', 'associations', 'history', 'messages')},
                        'original_anchor': {k: thread['original_anchor'][k] for k in ('quote', 'kind')},
                        'messages': [{k: v for k, v in m.items() if k != 'edits'} for m in thread['messages']],
                        'history': [{'at': h['at'], 'method': h['method']} for h in thread['history']]})
    return {**value, 'threads': threads}
