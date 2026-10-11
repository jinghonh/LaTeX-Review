"""Compare annotation snapshots against explicit batch receipts or expectations."""
from collections import Counter


def audit(snapshot_before, snapshot_after, results=None, *, expected_items=None):
    """Return discrepancies without resolving threads or changing the snapshots.

    ``results`` accepts a batch response or its result list. Expected items may
    include id, thread_id, message_id, operation, reply_to and author.
    """
    before = {t['id']: t for t in snapshot_before['threads']}
    after = {t['id']: t for t in snapshot_after['threads']}
    receipt = results or []
    if isinstance(receipt, dict):
        receipt = receipt['results']
    successful = [r for r in receipt if r.get('ok')]
    receipts = {r['id']: r for r in successful}
    expected = expected_items if expected_items is not None else successful
    issues = []
    for item_id, count in Counter(r['id'] for r in receipt).items():
        if count > 1:
            issues.append({'code': 'duplicate_item', 'id': item_id})
    message_ids = [r['message_id'] for r in successful if r.get('message_id')]
    for message_id, count in Counter(message_ids).items():
        if count > 1:
            issues.append({'code': 'duplicate_message', 'message_id': message_id})
    for item in expected:
        result = receipts.get(item['id'], {})
        target = {**item, **result}
        thread_id = target.get('thread_id')
        thread = after.get(thread_id)
        if not thread:
            issues.append({'code': 'missing_thread', 'id': item['id'], 'thread_id': thread_id})
            continue
        if target.get('operation') not in ('create', 'reply'):
            continue
        messages = [m for m in thread['messages'] if not m.get('deleted_at')]
        message_id = target.get('message_id')
        matches = [m for m in messages if m['id'] == message_id] if message_id else [
            m for m in messages if m['id'] not in {v['id'] for v in before.get(thread_id, {}).get('messages', [])}
            and ('author' not in item or m['author'] == item['author'])
            and ('reply_to' not in item or m.get('reply_to') == item['reply_to'])]
        if not matches:
            issues.append({'code': 'missing_message', 'id': item['id'], 'thread_id': thread_id})
        if len(matches) > 1:
            issues.append({'code': 'duplicate_reply', 'id': item['id'], 'thread_id': thread_id})
        for message in matches:
            parent = item.get('reply_to')
            if parent is None and target.get('operation') == 'reply':
                parent = thread['messages'][0]['id']
            if parent is not None and (message.get('reply_to') != parent or not any(
                    m['id'] == parent and not m.get('deleted_at') for m in thread['messages'])):
                issues.append({'code': 'wrong_parent', 'id': item['id'], 'message_id': message['id']})
            if 'author' in item and message['author'] != item['author']:
                issues.append({'code': 'wrong_author', 'id': item['id'], 'message_id': message['id']})
    own_threads = {r['thread_id'] for r in successful if r.get('operation') == 'create' and r.get('thread_id')}
    own_messages = set(message_ids)
    new_threads = set(after) - set(before)
    external_messages = []
    for thread_id, thread in after.items():
        old_ids = {m['id'] for m in before.get(thread_id, {}).get('messages', [])}
        reply_groups = {}
        for message in thread['messages']:
            if message['id'] in old_ids or message.get('deleted_at') or not message.get('reply_to'):
                continue
            identity = (message['reply_to'], message['author']['name'], message['author']['type'], message['body'])
            reply_groups.setdefault(identity, []).append(message['id'])
        for matches in reply_groups.values():
            if len(matches) > 1:
                issues.append({'code': 'duplicate_reply', 'thread_id': thread_id, 'message_ids': matches})
        external_messages.extend({'thread_id': thread_id, 'message_id': m['id']} for m in thread['messages']
                                 if m['id'] not in old_ids and m['id'] not in own_messages)
    version_changed = snapshot_before['version_id'] != snapshot_after['version_id']
    if version_changed:
        issues.append({'code': 'version_changed'})
    return {'ok': not issues, 'version_changed': version_changed,
            'start_revision': snapshot_before['revision'], 'end_revision': snapshot_after['revision'],
            'issues': issues, 'new_thread_ids': sorted(new_threads & own_threads),
            'external_thread_ids': sorted(new_threads - own_threads), 'external_messages': external_messages}
