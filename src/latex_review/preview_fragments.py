"""Immutable renderer-owned root chunks; optional internal extraction hints only."""
from dataclasses import dataclass


@dataclass(frozen=True)
class RootFragment:
    node_id: str
    html: str


@dataclass(frozen=True)
class FragmentPlan:
    html: str
    before: str
    after: str
    roots: tuple[RootFragment, ...]


def bind_fragments(html, roots):
    """Bind exact root concatenation to exact default composed HTML, or decline."""
    roots = tuple(roots)
    body = ''.join(root.html for root in roots)
    if not body or html.count(body) != 1:
        return None
    start = html.index(body)
    return FragmentPlan(html, html[:start], html[start + len(body):], roots)
