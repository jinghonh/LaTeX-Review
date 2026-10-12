"""The installed skill must be self-contained after a plain directory copy."""
import re
import shutil
from pathlib import Path


def test_latex_review_skill_links_survive_standalone_install(tmp_path):
    source = Path(__file__).resolve().parents[1] / '.agents/skills/latex-review'
    installed = tmp_path / 'skills/latex-review'
    shutil.copytree(source, installed)
    for document in installed.rglob('*.md'):
        for target in re.findall(r'\]\(([^)]+)\)', document.read_text()):
            if '://' in target or target.startswith('#'):
                continue
            path = (document.parent / target.split('#')[0]).resolve()
            assert path.is_relative_to(installed.resolve()), (document.name, target)
            assert path.is_file(), (document.name, target)
