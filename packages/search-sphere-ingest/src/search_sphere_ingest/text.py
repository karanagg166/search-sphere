"""Deterministic sentence offsets and conservative font-based heading hierarchy."""

import re

from .models import Paragraph, Section, Sentence, SourceRef, stable_id

_BOUNDARY = re.compile(r"(?<=[.!?।。！？])\s+|\n\s*\n")
_ABBREVIATIONS = {"dr.", "mr.", "mrs.", "ms.", "prof.", "e.g.", "i.e.", "vs.", "fig."}


def sentences(text: str, paragraph_id: str) -> list[Sentence]:
    spans = []
    start = 0
    for match in _BOUNDARY.finditer(text):
        before = text[start : match.start()]
        if before.split() and before.split()[-1].lower() in _ABBREVIATIONS:
            continue
        spans.append((start, match.start()))
        start = match.end()
    spans.append((start, len(text)))
    output = []
    for left, right in spans:
        raw = text[left:right]
        begin = left + len(raw) - len(raw.lstrip())
        end = right - len(raw) + len(raw.rstrip())
        if begin < end:
            output.append(
                Sentence(
                    sentence_id=stable_id(paragraph_id, begin, end),
                    text=text[begin:end],
                    start=begin,
                    end=end,
                )
            )
    return output


class Hierarchy:
    def __init__(self) -> None:
        self.sections: list[Section] = []
        self.stack: list[Section] = []

    def heading(self, text: str, level: int, source: SourceRef) -> None:
        while self.stack and self.stack[-1].level >= level:
            self.stack.pop()
        section = Section(
            section_id=stable_id(source.region_id, "section"),
            parent_section_id=self.stack[-1].section_id if self.stack else None,
            title=text,
            level=level,
            path=[item.title for item in self.stack] + [text],
            source=source,
        )
        self.stack.append(section)
        self.sections.append(section)

    def paragraph(self, text: str, source: SourceRef) -> Paragraph:
        identity = stable_id(source.region_id, "paragraph")
        return Paragraph(
            object_id=identity,
            text=text,
            sources=[source],
            sentences=sentences(text, identity),
            section_id=self.stack[-1].section_id if self.stack else None,
            heading_path=[item.title for item in self.stack],
        )
