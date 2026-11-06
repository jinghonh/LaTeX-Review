"""Configuration accepts additive checks without changing legacy CLI arguments."""
import pytest

from latex_review.check_configuration import validate_check_configuration
from latex_review.project_profile import validate
from latex_review.cli import _config, ConfigurationError


def test_disabled_semantic_needs_no_connection():
    assert validate({'checks': {'enabled': True, 'rules': ['references']},
                     'knowledge': {'storage': 'external'}, 'semantic': {'enabled': False}})


@pytest.mark.parametrize('data', [
    {'checks': {'enabled': 1}}, {'checks': {'rules': ['imaginary']}},
    {'checks': {'debounce_ms': -1}}, {'knowledge': {'storage': 'merge'}},
    {'semantic': {'enabled': True}}, {'semantic': {'concurrency': 3}},
    {'semantic': {'api_key': 'secret'}}, {'semantic': {'max_requests': True}},
    {'semantic': {'thinking': 'disabled'}},
    {'semantic': {'provider': 'deepseek', 'thinking': 'unknown'}},
])
def test_invalid_check_config_rejected(data):
    with pytest.raises(ValueError):
        validate_check_configuration(data)


def test_global_project_section_merge(tmp_path, monkeypatch):
    import latex_review.cli as cli
    global_path = tmp_path / 'global.toml'
    global_path.write_text('[checks]\non_save=true\ndebounce_ms=800\n[semantic]\nenabled=false\n')
    local = tmp_path / 'project.toml'
    local.write_text('[checks]\non_save=false\n[knowledge]\nstorage="external"\n')
    monkeypatch.setattr(cli, 'global_config_path', lambda: global_path)
    result = _config(local, True)
    assert result['checks'] == {'on_save': False, 'debounce_ms': 800}
    assert result['semantic'] == {'enabled': False}


def test_unknown_global_key_still_rejected(tmp_path, monkeypatch):
    import latex_review.cli as cli
    global_path = tmp_path / 'global.toml'
    global_path.write_text('[unknown]\nx=1\n')
    monkeypatch.setattr(cli, 'global_config_path', lambda: global_path)
    with pytest.raises(ConfigurationError):
        _config(tmp_path / 'missing.toml', False)


@pytest.mark.parametrize('url', ['http://remote.example/v1', 'https://user:pass@remote.example/v1', 'file:///tmp/model'])
def test_enabled_semantic_connection_safety(url):
    with pytest.raises(ValueError):
        validate({'semantic': {'enabled': True, 'base_url': url, 'model': 'model', 'api_key_env': 'KEY'}})


def test_deepseek_thinking_configuration_is_explicit():
    result = validate({'semantic': {'enabled': True, 'provider': 'deepseek', 'thinking': 'disabled',
                                    'base_url': 'https://api.deepseek.com/v1', 'model': 'deepseek-chat',
                                    'api_key_env': 'KEY'}})
    assert result['semantic']['thinking'] == 'disabled'
