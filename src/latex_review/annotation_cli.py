"""High-level annotation requests using the shared version-checked store."""
from __future__ import annotations


from .annotations import AnnotationError
from .quote_matching import normalize_quote, normalize_formula, quote_atoms, matches, nearest


class SelectionError(AnnotationError):
    def __init__(self, message, candidates):
        super().__init__(message)
        self.candidates = candidates


def find_selection(content, args):
    quote = normalize_quote(args.quote) if args.quote is not None else None
    if quote == '' or (quote is None and not (args.formula and args.unit_id)):
        raise SelectionError('需要非空 --quote；完整公式也可使用 --formula --unit-id', [])
    candidates = []
    eligible = []
    plain_quote, formulas = quote_atoms(args.quote or "")
    for unit in sorted(content['units'], key=lambda u: u['order']):
        if unit['kind'] != ('formula' if args.formula else 'text'):
            continue
        if args.unit_id and unit['id'] != args.unit_id:
            continue
        eligible.append(unit)
        if args.formula:
            if quote is not None and (not (formulas or (quote.startswith(r'\begin{') and quote.endswith('}'))) or normalize_formula(unit['raw_latex']) != normalize_formula(quote)):
                continue
            selections = [{'unit_id': unit['id'], 'side': 'new'}]
        else:
            selections = list(matches(unit, plain_quote, formulas))
        for selection in selections:
            candidates.append({'occurrence': len(candidates) + 1, 'selection': selection,
                               'source': unit['source'], 'section_path': unit['section_path'],
                               'text': unit['text']})
    if not candidates:
        raise SelectionError('未找到原文或完整公式；请读取 --content 并核对 --unit-id', nearest(eligible, quote or ''))
    if args.occurrence is not None:
        if not 1 <= args.occurrence <= len(candidates):
            raise SelectionError('--occurrence 超出候选范围（从 1 开始）', candidates)
        return candidates[args.occurrence - 1]['selection']
    if len(candidates) != 1:
        raise SelectionError('原文存在多个候选；请用 --unit-id 或 --occurrence 明确位置', candidates)
    return candidates[0]['selection']


def build_request(content, state, args):
    request = {'operation': args.command, 'expected_revision': state['revision']}
    if args.command == 'add':
        request.update(operation='create', selection=find_selection(content, args))
    else:
        request['thread_id'] = args.thread_id
    if args.command in ('add', 'metadata'):
        if args.command == 'metadata':
            request['operation'] = 'update_metadata'
        if args.title is not None:
            request['title'] = args.title
        if args.tag is not None or getattr(args, 'clear_tags', False):
            request['tags'] = args.tag or []
    if args.command in ('preview', 'apply'):
        request.update(operation='suggestion_' + args.command, message_id=args.message_id)
    if args.command in ('add', 'reply'):
        original = getattr(args, 'original_chunk', None)
        replacement = getattr(args, 'replacement_chunk', None)
        if original is not None or replacement is not None:
            if original is None or replacement is None:
                raise AnnotationError('建议需要同时提供 --original-chunk 和 --replacement-chunk')
            request['suggested_replacement'] = {'original_chunk': original, 'replacement_chunk': replacement}
        request.update(body=args.body, author={'name': args.author, 'type': 'agent'},
                       category=args.category)
        if args.command == 'reply' and args.parent_message_id:
            request['reply_to'] = args.parent_message_id
    elif args.command == 'edit':
        thread = next((t for t in state['threads'] if t['id'] == args.thread_id), None)
        message = next((m for m in thread['messages'] if m['id'] == args.message_id), None) if thread else None
        if message is None or message['deleted_at'] or thread['deleted_at']:
            raise AnnotationError('批注内容不存在或已删除')
        request.update(message_id=args.message_id, body=args.body,
                       category=args.category or message['category'])
    elif args.command == 'delete':
        if not args.yes:
            raise AnnotationError('删除需要显式传入 --yes')
        request['operation'] = 'delete_message' if args.message_id else 'delete_thread'
        if args.message_id:
            request['message_id'] = args.message_id
    return {'project_id': content['project_id'], 'version_id': content['version_id'], 'request': request}


def add_commands(parser):
    commands = parser.add_subparsers(dest='command')
    for name, help_text in (('add', '按原文或完整公式添加批注'), ('reply', '回复批注或指定消息'),
                            ('edit', '编辑指定消息，保留原作者'), ('delete', '删除线程或指定消息'),
                            ('resolve', '将批注标记为已解决'), ('metadata', '更新线程标题或标签'), ('preview', '预览替换建议差异'), ('apply', '显式应用替换建议')):
        command = commands.add_parser(name, help=help_text)
        command.add_argument('--full', action='store_true', help='返回完整写入结果')
        if name != 'add':
            command.add_argument('thread_id')
        if name in ('add', 'reply', 'edit'):
            command.add_argument('--body', required=True, help='批注正文，按纯文本保存')
            command.add_argument('--category', choices=['question', 'suggestion', 'note'],
                                 default=None if name == 'edit' else 'note')
        if name in ('preview', 'apply'):
            command.add_argument('message_id')
        if name in ('add', 'reply'):
            command.add_argument('--original-chunk', help='建议替换的原始 LaTeX 块')
            command.add_argument('--replacement-chunk', help='替换 LaTeX 块，可为空字符串')
            command.add_argument('--author', required=True, help='作者名称；身份固定为 agent')
        if name in ('add', 'metadata'):
            command.add_argument('--title', help='线程标题；空字符串恢复自动标题')
            labels = command.add_mutually_exclusive_group()
            labels.add_argument('--tag', action='append', help='替换线程标签，可重复')
        if name == 'metadata':
            labels.add_argument('--clear-tags', action='store_true', help='清空线程标签')
        if name == 'add':
            command.add_argument('--quote', help='原文；公式模式须包含完整 LaTeX 包装')
            command.add_argument('--unit-id', help='只搜索指定正文单元')
            command.add_argument('--occurrence', type=int, help='在当前候选中选择第几个位置，从 1 开始')
            command.add_argument('--formula', action='store_true', help='选择完整公式')
        elif name == 'reply':
            command.add_argument('--parent-message-id', help='回复的父消息；默认初始批注')
        elif name == 'edit':
            command.add_argument('message_id')
        elif name == 'delete':
            command.add_argument('--message-id', help='只删除指定消息；默认删除整条批注及回复')
            command.add_argument('--yes', action='store_true', help='确认删除')
    return commands


if __name__ == '__main__':
    from .agent_tools import annotations_main
    raise SystemExit(annotations_main())
