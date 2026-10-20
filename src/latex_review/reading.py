"""Install shared reading data, controls and scripts for both report modes."""
from dataclasses import dataclass
from importlib.resources import files
import json

from .structure import ParsedProject


@dataclass(frozen=True)
class ReadingFeatures:
    section_toggle: str
    section_directory: str
    translation_mode: str
    translation_actions: str
    translation_status: str
    scripts: str


def _data(identifier: str, value: object) -> str:
    content = json.dumps(value, ensure_ascii=False).replace('<', '\\u003c')
    return f'<script type="application/json" id="{identifier}">{content}</script>'


def _script(name: str, revision: int | None = None) -> str:
    if name == 'annotation_delivery.js':
        from .annotations._suggestions import delivery_script
        content = delivery_script()
    else:
        content = files('latex_review').joinpath(name).read_text(encoding='utf-8')
    if revision is not None:
        content = content.replace('LIVE_REVISION', str(revision))
    return '<script>' + content + '</script>'


def build_reading(new: ParsedProject, units: list[dict], *, old: ParsedProject | None = None,
                  pairs: tuple[tuple[str, str], ...] = (), fingerprint: str = '',
                  revision: int = 1) -> ReadingFeatures:
    live = old is None
    projects = [('new', new)] if live else [('old', old), ('new', new)]
    nodes = [{'side': side, 'id': node.review.id, 'type': node.review.type,
              'parent': node.review.parent_id, 'section': list(node.review.section_path),
              'citations': list(node.citations)}
             for side, project in projects for node in project.nodes]
    reader_data = {'nodes': nodes, 'live': True} if live else {'nodes': nodes, 'fingerprint': fingerprint}
    data = (_data('reader-data', reader_data)
            + _data('translation-units', [{k: v for k, v in unit.items() if k != 'sentences'} for unit in units])
            + _data('translation-results', {}))
    if live:
        scripts = (data + _script('report_annotations.js') + _script('annotation_delivery.js')
                   + _script('report_translation.js') + _script('report_sections.js')
                   + _script('report_search.js') + _script('live_reader.js', revision))
    else:
        pair_data = json.dumps(pairs, ensure_ascii=False).replace('<', '\\u003c')
        scripts = (data + '<script>window.reviewNodePairs=' + pair_data + ';</script>'
                   + _script('report_interaction.js') + _script('report_sections.js')
                   + _script('report_search.js') + _script('report_reader.js')
                   + _script('report_translation.js') + _script('report_annotations.js')
                   + _script('annotation_delivery.js'))
    options = [('bilingual', '英中对照'), ('original', '英文原文')]
    if not live:
        options.reverse()
    mode = ('<label class="control-field"><span>译文显示</span><select id="translation-mode">'
            + ''.join(f'<option value="{value}">{label}</option>' for value, label in options)
            + '</select></label>')
    export_icon = '' if live else ('<svg class="control-icon" width="16" height="16" viewBox="0 0 20 20" '
        'fill="none" stroke="currentColor" stroke-width="1.5" aria-hidden="true">'
        '<path d="M10 2v10m-4-4 4 4 4-4M3 12v5h14v-5"/></svg>')
    actions = ('<div class="translation-actions"><button type="button" id="translate-all">'
               + ('翻译全文' if live else '翻译全部变更') + '</button>'
               + '<button type="button" id="translation-cancel" disabled>停止翻译</button>'
               + '<button type="button" id="translation-export">' + export_icon
               + ('导出英中对照 HTML' if live else '导出译文报告') + '</button></div>')
    status_tag = 'p' if live else 'span'
    status = f'<{status_tag} id="translation-status" role="status">' + (
        '按需翻译标题、摘要、正文和图注；表格和参考文献保留原文。' if live else
        '点击后翻译变更段落、标题和图注；表格暂不翻译。') + f'</{status_tag}>'
    toggle = '<button type="button" id="toggle-sections" aria-expanded="false" aria-controls="sections-side">章节</button>'
    directory = ('<nav id="sections-side" aria-label="章节目录" hidden><div class="directory-heading">'
                 '<h2>章节目录</h2><button type="button" id="close-sections">收起</button></div>'
                 + ('' if live else '<p class="section-legend">变更总数 · 未审阅</p>')
                 + '<ol id="section-tree"></ol></nav>')
    return ReadingFeatures(toggle, directory, mode, actions, status, scripts)
