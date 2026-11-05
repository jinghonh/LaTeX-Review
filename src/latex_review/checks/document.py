"""Thin check views over ParsedProject and its existing SourceMap."""
from dataclasses import dataclass
from hashlib import sha256
from functools import cached_property
from heapq import heappush, heappop

from .snapshot import ContentSnapshot


@dataclass(frozen=True)
class CheckDocument:
    snapshot: ContentSnapshot
    parsed: object

    @cached_property
    def _texts(self):
        # Evidence can only originate from expanded source segments. Resource
        # dependencies such as PDFs are captured for identity but are binary.
        return self.parsed.expanded.source_map.files

    @cached_property
    def _hashes(self):
        return {name: sha256(raw).hexdigest() for name, raw in self.snapshot.files.items()}

    def evidence(self, start, end):
        """Return separate exact Unicode fragments, never a synthetic joined span."""
        result = []
        for mapped in self.parsed.expanded.origin_ranges(start, end):
            origin = mapped.origin
            text = self._texts[origin.file]
            quote = text[origin.start:origin.end]
            exact = (origin.confidence == "exact" and
                     quote == self.parsed.expanded.text[mapped.expanded_start:mapped.expanded_end])
            result.append({"entry": self.snapshot.entry, "content_id": self.snapshot.content_id,
                           "file": origin.file, "file_sha256": self._hashes[origin.file],
                           "start": origin.start, "end": origin.end, "quote": quote,
                           "include_instance": origin.include_instance,
                           "start_line": origin.start_line, "start_column": origin.start_column,
                           "end_line": origin.end_line, "end_column": origin.end_column,
                           "reliability": "exact" if exact else "unknown",
                           "reason": None if exact else "source_mapping_uncertain"})
        return result

    @cached_property
    def units(self):
        # Atomic intervals avoid double counting parent containers and child nodes.
        # These are raw source units; no plain_text/display offsets are claimed.
        nodes = self.parsed.nodes
        boundaries = sorted({p for n in nodes for p in (n.expanded_start, n.expanded_end)})
        units = []
        # Sweep start boundaries once; the shortest active interval is the
        # same owner as the previous all-node scan (original order breaks ties).
        ordered = sorted(enumerate(nodes), key=lambda item: item[1].expanded_start)
        active, cursor = [], 0
        for left, right in zip(boundaries, boundaries[1:]):
            while cursor < len(ordered) and ordered[cursor][1].expanded_start <= left:
                index, node = ordered[cursor]
                heappush(active, (node.expanded_end - node.expanded_start, index, node.expanded_end, node))
                cursor += 1
            while active and active[0][2] < right:
                heappop(active)
            if not active or left == right:
                continue
            owner = active[0][3]
            units.append({"node_id": owner.review.id, "kind": owner.review.type,
                          "section_path": list(owner.review.section_path), "reading_order": len(units),
                          "expanded_start": left, "expanded_end": right,
                          "representation": "raw_latex", "evidence": self.evidence(left, right)})
        return units


def build_check_document(snapshot, parsed_project=None):
    parsed = parsed_project or snapshot.parsed
    if parsed.expanded is not snapshot.parsed.expanded:
        raise ValueError("parsed project must belong to this snapshot")
    return CheckDocument(snapshot, parsed)
