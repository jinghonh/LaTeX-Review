"""Portable editorial review records with version-specific source locations."""
from pathlib import Path

from .annotations import AnnotationError, make_anchor


def write_record(path, content, request, result, state, originals):
    path = Path(path).expanduser().absolute()
    path.parent.mkdir(parents=True, exist_ok=True)
    threads = {thread['id']: thread for thread in state['threads']}
    receipts = {item['id']: item for item in result['results']}
    submitted = {item['id']: item for item in originals}
    lines = ['# 全文审查记录', '', f"论文项目：{content['project_id']}",
             f"论文入口：{content['entry']}", f"显示版本：{content['version_id']}",
             f"批次：{request['request_id']}", f"提交状态：{result['committed']}", '']
    for item in request['items']:
        receipt = receipts[item['id']]
        original = submitted[item['id']]
        thread = threads.get(receipt.get('thread_id'), {})
        association = thread.get('association') or {}
        anchor = association.get('anchor') or {}
        if item.get('selection'):
            try:
                anchor = make_anchor(content, item['selection'])
            except AnnotationError:
                anchor = {}
        source = anchor.get('source') or {}
        quote = anchor.get('quote') or original.get('quote') or (thread.get('original_anchor') or {}).get('quote')
        location = f"{source.get('file', original.get('file', '未知文件'))}:{source.get('start_line', original.get('line', '?'))}-{source.get('end_line', '?')}"
        lines += [f"## {item['id']}", '', f"操作：{item['operation']}", f"源码位置：{location}",
                  f"结果：{'成功' if receipt['ok'] else '失败'}", f"批注编号：{receipt.get('thread_id') or '未提交'}", '']
        if original.get('line') is not None:
            lines += [f"指定定位行号：{original['line']}", '']
        if isinstance(quote, str) and quote:
            lines += ['原文：', '', *['> ' + line for line in quote.splitlines()], '']
        if isinstance(original.get('body'), str) and original['body']:
            lines += ['审查意见：', '', *['> ' + line for line in original['body'].splitlines()], '']
        if receipt.get('error'):
            lines += [f"错误：{receipt['error']['message']}", '']
    path.write_text('\n'.join(lines), encoding='utf-8')
    return str(path)
