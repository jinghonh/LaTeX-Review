"""Shared complete units, without inter-version dependency chains."""
from copy import deepcopy
import hashlib
import json

ENCODING = 'shared-units-v1'


def pack_versions(bundle):
    result = deepcopy(bundle)
    pool, indices = [], {}
    expanded_bytes = 0
    for content in result['versions'].values():
        refs = []
        for unit in content['units']:
            key = json.dumps(unit, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
            expanded_bytes += len(key.encode())
            if expanded_bytes > 200 * 1024 * 1024:
                raise ValueError('恢复正文超过 200 MiB 上限')
            if key not in indices:
                indices[key] = len(pool)
                pool.append(unit)
            refs.append(indices[key])
        content['units'] = refs
    expanded_bytes += len(json.dumps(result, ensure_ascii=False, separators=(',', ':')).encode())
    if expanded_bytes > 200 * 1024 * 1024:
        raise ValueError('恢复正文超过 200 MiB 上限')
    return {'encoding': ENCODING, 'units': pool, 'bundle': result}


def unpack_versions(value):
    if not isinstance(value, dict) or 'encoding' not in value:
        return value
    if set(value) != {'encoding', 'units', 'bundle'} or value['encoding'] != ENCODING:
        raise ValueError('正文共享表示无效')
    pool, result = value['units'], deepcopy(value['bundle'])
    if not isinstance(pool, list) or any(not isinstance(u, dict) for u in pool):
        raise ValueError('正文单元池无效')
    if not isinstance(result, dict) or not isinstance(result.get('versions'), dict):
        raise ValueError('正文版本集合无效')
    expanded_bytes = len(json.dumps(result, ensure_ascii=False, separators=(',', ':')).encode())
    sizes = [len(json.dumps(u, ensure_ascii=False, separators=(',', ':')).encode()) for u in pool]
    for content in result['versions'].values():
        refs = content.get('units') if isinstance(content, dict) else None
        if not isinstance(refs, list) or any(type(i) is not int or not 0 <= i < len(pool) for i in refs):
            raise ValueError('正文单元引用无效')
        expanded_bytes += sum(sizes[i] for i in refs)
        if expanded_bytes > 200 * 1024 * 1024:
            raise ValueError('恢复正文超过 200 MiB 上限')
        content['units'] = [deepcopy(pool[i]) for i in refs]
    return result


def pack_storage(bundle):
    data = pack_versions(bundle)
    payload = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    return {'storage_encoding': ENCODING, 'sha256': hashlib.sha256(payload.encode()).hexdigest(), 'data': data}


def unpack_storage(value, *, restore_versions=True):
    if isinstance(value, dict) and 'storage_encoding' in value:
        if (set(value) != {'storage_encoding', 'sha256', 'data'} or value['storage_encoding'] != ENCODING
                or not isinstance(value['data'], dict)
                or hashlib.sha256(json.dumps(value['data'], ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest() != value['sha256']):
            raise ValueError('正文存储校验失败')
        if not restore_versions:
            packed = value['data']
            if (set(packed) != {'encoding', 'units', 'bundle'} or packed['encoding'] != ENCODING
                    or not isinstance(packed['units'], list) or not isinstance(packed['bundle'], dict)):
                raise ValueError('正文共享表示无效')
            return packed['bundle']
        return unpack_versions(value['data'])
    return unpack_versions(value)
