"""Independent semantic units with real tokenizer budgets and lossless source links."""

from collections.abc import Sequence

from .config import PipelineConfig
from .errors import ResourceLimit
from .models import Chunk, NormalizedDocument, Paragraph, SourceRef, Table, Visual, stable_id
from .providers import Tokenizer


class TiktokenTokenizer:
    """Default budget is specifically for OpenAI text-embedding-3-small, not Cohere."""

    name = "text-embedding-3-small:cl100k_base"

    def count(self, text: str) -> int:
        import tiktoken

        return len(tiktoken.get_encoding("cl100k_base").encode(text, disallowed_special=()))


class Chunker:
    def __init__(self, config: PipelineConfig, tokenizer: Tokenizer) -> None:
        self.config, self.tokenizer = config, tokenizer

    def chunk(self, document: NormalizedDocument) -> list[Chunk]:
        output: list[Chunk] = []
        prose: list[Paragraph] = []

        def flush() -> None:
            if prose:
                output.extend(self._prose(document, prose))
                prose.clear()

        for item in document.objects:
            if isinstance(item, Paragraph):
                if prose and item.section_id != prose[-1].section_id:
                    flush()
                prose.append(item)
            else:
                flush()
                output.extend(
                    self._table(document, item)
                    if isinstance(item, Table)
                    else self._visual(document, item)
                )
        flush()
        if len(output) > 1000:
            raise ResourceLimit("chunk_budget")
        return output

    def _context(self, document: NormalizedDocument, path: list[str]) -> str:
        return "\n".join(value for value in (document.title, " > ".join(path)) if value)

    def _make(
        self,
        document: NormalizedDocument,
        kind: str,
        text: str,
        ids: list[str],
        path: list[str],
        sources: list[SourceRef],
        **metadata: object,
    ) -> Chunk:
        count = self.tokenizer.count(text)
        if count <= 0 or count > self.config.max_tokens:
            raise ResourceLimit("unrepresentable_semantic_unit")
        # Configuration and tokenizer form part of chunk identity; reprocessing is stable.
        return Chunk.model_validate(
            {
                "chunk_id": stable_id(
                    document.document_id,
                    document.config_fingerprint,
                    self.tokenizer.name,
                    ids,
                    text,
                    metadata,
                ),
                "document_id": document.document_id,
                "source_object_ids": list(dict.fromkeys(ids)),
                "kind": kind,
                "text": text,
                "token_count": count,
                "tokenizer": self.tokenizer.name,
                "heading_path": path,
                "sources": sources,
                **metadata,
            }
        )

    def _fragments(self, prefix: str, text: str) -> list[str]:
        """Only an oversized sentence is split; binary-search Unicode character boundaries."""
        if self.tokenizer.count(prefix + "\n" + text) <= self.config.max_tokens:
            return [text]
        if self.tokenizer.count(prefix + "\n" + "x") > self.config.max_tokens:
            raise ResourceLimit("hierarchy_exceeds_token_budget")
        result = []
        rest = text
        while rest:
            lo, hi, best = 1, len(rest), 0
            while lo <= hi:
                middle = (lo + hi) // 2
                if self.tokenizer.count(prefix + "\n" + rest[:middle]) <= self.config.max_tokens:
                    best, lo = middle, middle + 1
                else:
                    hi = middle - 1
            if not best:
                raise ResourceLimit("token_budget_too_small")
            if best < len(rest):
                space = rest.rfind(" ", 0, best)
                if space > best // 2:
                    best = space
            result.append(rest[:best])
            rest = rest[best:]
        return result

    def _prose(self, document: NormalizedDocument, paragraphs: Sequence[Paragraph]) -> list[Chunk]:
        prefix = self._context(document, paragraphs[0].heading_path)
        # text, paragraph, sentence ID, fragment flag
        units: list[tuple[str, Paragraph, str, bool]] = []
        for paragraph in paragraphs:
            for sentence in paragraph.sentences:
                fragments = self._fragments(prefix, sentence.text)
                units.extend(
                    (part, paragraph, sentence.sentence_id, len(fragments) > 1)
                    for part in fragments
                )
        output = []
        start = 0
        while start < len(units):
            end = start
            while end < len(units):
                content = prefix + "\n" + " ".join(unit[0] for unit in units[start : end + 1])
                count = self.tokenizer.count(content)
                if count > self.config.max_tokens:
                    break
                end += 1
                if count >= self.config.target_tokens:
                    break
            if end == start:
                raise ResourceLimit("sentence_exceeds_token_budget")
            selected = units[start:end]
            ids = list(dict.fromkeys(unit[1].object_id for unit in selected))
            sources = list(
                {ref.region_id: ref for unit in selected for ref in unit[1].sources}.values()
            )
            output.append(
                self._make(
                    document,
                    "text",
                    prefix + "\n" + " ".join(unit[0] for unit in selected),
                    ids,
                    paragraphs[0].heading_path,
                    sources,
                    paragraph_ids=ids,
                    sentence_ids=list(dict.fromkeys(unit[2] for unit in selected)),
                    fragment=any(unit[3] for unit in selected),
                )
            )
            if end == len(units):
                break
            overlap = min(self.config.overlap_sentences, end - start - 1)
            # Never overlap a partial sentence; preserve forward progress.
            if any(unit[3] for unit in selected[-max(overlap, 1) :]):
                overlap = 0
            candidate = end - overlap
            while (
                candidate < end
                and self.tokenizer.count(
                    prefix + "\n" + " ".join(unit[0] for unit in units[candidate : end + 1])
                )
                > self.config.max_tokens
            ):
                candidate += 1
            start = candidate
        return output

    def _table(self, document: NormalizedDocument, table: Table) -> list[Chunk]:
        prefix = "\n".join(
            (
                self._context(document, table.heading_path),
                table.title,
                "Units: " + ", ".join(table.units),
                " | ".join(table.headers),
            )
        )
        output = []
        start = 0
        while start < len(table.rows):
            end = start
            while end < len(table.rows):
                text = (
                    prefix
                    + "\n"
                    + "\n".join(" | ".join(row) for row in table.rows[start : end + 1])
                )
                if self.tokenizer.count(text) > self.config.max_tokens:
                    break
                end += 1
            if start == end:
                raise ResourceLimit("table_row_exceeds_token_budget")
            text = prefix + "\n" + "\n".join(" | ".join(row) for row in table.rows[start:end])
            output.append(
                self._make(
                    document,
                    "table",
                    text,
                    [table.object_id],
                    table.heading_path,
                    table.sources,
                    row_start=start,
                    row_end=end,
                )
            )
            start = end
        return output

    def _visual(self, document: NormalizedDocument, visual: Visual) -> list[Chunk]:
        if not (
            visual.visual_description
            or visual.ocr_text
            or visual.native_text
            or visual.values
            or visual.nodes
        ):
            return []  # Retain source object without embedding empty/unextracted content.
        context = self._context(document, visual.heading_path)
        base = "\n".join(
            part
            for part in (
                context,
                visual.title,
                visual.caption,
                visual.chart_type if visual.kind == "chart" else "",
                "Generated interpretation; review required.",
                visual.visual_description,
                "Axes: "
                + "; ".join(f"{axis.label} [{axis.unit}] {axis.scale}" for axis in visual.axes),
                "Legend: " + ", ".join(visual.legend),
            )
            if part
        )
        # Large descriptions become linked chunks, never silently truncated.
        pieces = self._fragments(context, base[len(context) :].lstrip())
        output = [
            self._make(
                document,
                visual.kind,
                context + "\n" + part,
                [visual.object_id],
                visual.heading_path,
                visual.sources,
                fragment=len(pieces) > 1,
            )
            for part in pieces
        ]
        details = [
            f"{point.series}: {point.label} = {point.raw_value if point.raw_value is not None else point.value} {point.unit} ({point.evidence}; verified={point.verified})"
            for point in visual.values
        ]
        by_id = {node.node_id: node.label for node in visual.nodes}
        connected = {identity for edge in visual.edges for identity in (edge.source, edge.target)}
        # Connected labels travel with their relationship; isolated nodes remain
        # searchable without inventing an edge to another component.
        details += [
            f"Isolated node: {node.label}" for node in visual.nodes if node.node_id not in connected
        ]
        details += [
            f"{by_id[edge.source]} -> {by_id[edge.target]}: {edge.label} ({edge.direction}; visible={edge.visible})"
            for edge in visual.edges
        ]
        if visual.ocr_text:
            details.append("OCR transcription (review required):\n" + visual.ocr_text)
        if visual.native_text:
            details.append("Native PDF labels:\n" + visual.native_text)
        for detail in details:
            prefix = context + "\n" + visual.title
            # Relations retain both endpoint labels in one unit; never sever an edge.
            if (
                " -> " in detail
                and self.tokenizer.count(prefix + "\n" + detail) > self.config.max_tokens
            ):
                raise ResourceLimit("diagram_relationship_exceeds_token_budget")
            for part in self._fragments(prefix, detail):
                text = prefix + "\n" + part
                if (
                    len(output) == 1
                    and self.tokenizer.count(output[0].text + "\n" + detail)
                    <= self.config.max_tokens
                ):
                    output[0] = self._make(
                        document,
                        visual.kind,
                        output[0].text + "\n" + detail,
                        [visual.object_id],
                        visual.heading_path,
                        visual.sources,
                    )
                    break
                output.append(
                    self._make(
                        document,
                        visual.kind,
                        text,
                        [visual.object_id],
                        visual.heading_path,
                        visual.sources,
                    )
                )
        return output
