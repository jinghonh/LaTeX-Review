"""Differential coverage for the conservative literal-prose preview fast path."""
from types import SimpleNamespace
import pytest
from latex_review import preview


@pytest.mark.parametrize('raw', [
    '', ' ', '\n\n', 'Hello world.', ' A  B\nC\tD. ',
    'Paragraph 42 describes a synthetic evaluation.',
    'A < B > C "quoted" and \'single\' (1/2) = 0.5!',
    'A-b. [ordinary brackets]: yes?',
    'First.\n\nSecond.', 'First.\r\n\r\nSecond.',
    ' First. \n \n Second. ', 'First.\rSecond.', '\r',
    '<script> \'quoted\' & unsafe </script>',
])
def test_literal_preview_matches_legacy_dom(raw, monkeypatch):
    project = SimpleNamespace(macros={})
    optimized = preview._inline_html(raw, project, 'new')
    monkeypatch.setattr(preview, '_literal_inline_html', lambda raw: None)
    assert optimized == preview._inline_html(raw, project, 'new')


def test_literal_prose_never_initializes_tex(monkeypatch):
    def unexpected(*args):
        raise AssertionError('literal prose should not initialize plasTeX')
    monkeypatch.setattr(preview, '_parse_dom', unexpected)
    assert preview._inline_html('An ordinary prose sentence.', SimpleNamespace(macros={}), 'new') == ' An ordinary prose sentence. '


@pytest.mark.parametrize('raw', [r'\textbf{bold}', '$x$', 'x_y', 'x^2', 'a~b',
                                     'a&b', '#1', '% comment', '{group}', '`quote',
                                     'a--b', "a''b", 'α😀', 'nonbreaking\u00a0space'])
def test_special_or_uncertain_text_keeps_existing_parser(raw):
    assert preview._literal_inline_html(raw) is None
