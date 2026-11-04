"""Shared validation for the additive writing-check configuration sections."""
from urllib.parse import urlsplit


FIELDS = {
    'checks': {'enabled': bool, 'on_save': bool, 'debounce_ms': int, 'rules': list},
    'knowledge': {'storage': str, 'path': str},
    'semantic': {'enabled': bool, 'provider': str, 'base_url': str, 'model': str,
                 'api_key_env': str, 'max_requests': int, 'max_input_tokens': int,
                 'max_output_tokens': int, 'concurrency': int, 'timeout': int},
}
RULE_GROUPS = {'references', 'terminology', 'introductions', 'symbols'}


def validate_check_configuration(data):
    for section, fields in FIELDS.items():
        values = data.get(section, {})
        if not isinstance(values, dict):
            raise ValueError(f'配置 [{section}] 必须是表')
        if extra := set(values) - set(fields):
            raise ValueError(f'配置 [{section}] 含未知字段：{", ".join(sorted(extra))}')
        for key, value in values.items():
            if type(value) is not fields[key] or (isinstance(value, str) and not value.strip()):
                raise ValueError(f'配置 [{section}].{key} 类型或内容无效')
            if type(value) is int and value < (0 if key == 'debounce_ms' else 1):
                raise ValueError(f'配置 [{section}].{key} 超出范围')
    rules = data.get('checks', {}).get('rules', [])
    if any(not isinstance(rule, str) or rule not in RULE_GROUPS for rule in rules):
        raise ValueError('配置 [checks].rules 包含不支持的规则组')
    knowledge = data.get('knowledge', {})
    if knowledge.get('storage', 'project') not in {'project', 'external'}:
        raise ValueError('配置 [knowledge].storage 必须是 project 或 external')
    semantic = data.get('semantic', {})
    if semantic.get('provider', 'openai-compatible') != 'openai-compatible':
        raise ValueError('配置 [semantic].provider 不受支持')
    if semantic.get('concurrency', 2) > 2:
        raise ValueError('配置 [semantic].concurrency 不得大于 2')
    # Disabled semantic checks do not require a connection or credentials.
    if semantic.get('enabled', False):
        import re
        for key in ('base_url', 'model', 'api_key_env'):
            if not semantic.get(key):
                raise ValueError(f'启用语义审查需要 [semantic].{key}')
        endpoint = urlsplit(semantic['base_url'])
        if endpoint.scheme not in {'http', 'https'} or not endpoint.hostname or endpoint.username or endpoint.password or endpoint.query or endpoint.fragment:
            raise ValueError('语义接口地址必须为不含凭据、查询或片段的 HTTP(S) 地址')
        if endpoint.scheme == 'http' and endpoint.hostname not in {'localhost', '127.0.0.1', '::1'}:
            raise ValueError('非本机语义接口必须使用 HTTPS')
        if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', semantic['api_key_env']):
            raise ValueError('api_key_env 必须是环境变量名，不是密钥')
    return data
