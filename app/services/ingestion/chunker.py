"""Hybrid chunking: the document's structure first, then a token budget.

1. **Structure.** Blocks are grouped into sections by their heading path (the headings they sit
   under, outermost first). A passage never spans two sections, and a block (paragraph, list,
   table, code) is never cut while it fits the budget.
2. **Size.** A block larger than `max_tokens` is cut at the best boundary its kind offers:
   tables between rows (the header row is repeated in every piece), lists between items, code
   between lines, prose between sentences in overlapping windows (`overlap_tokens` of the
   previous window's last sentences start the next one). The pieces of a section are then
   packed greedily into passages of up to `max_tokens`.
3. **Merge.** A passage under `min_tokens` is merged into its neighbour when the two are siblings
   or parent and child sections and still fit the budget. The merged passage keeps the shared
   heading path and writes the other headings into its text, so no context is lost.

Every passage keeps its heading path and page range as metadata. `contextualize()` puts the
document title and heading path in front of the passage for embedding, so a passage such as
"Fees are due within 30 days." is embedded as part of "Engagement terms > Billing".
This is the same idea as Docling's HybridChunker, without its model dependencies.
"""

from dataclasses import dataclass

from app.services.ingestion.blocks import CODE, HEADING, LIST, TABLE, Block
from app.services.ingestion.text import count_tokens, hard_split, split_sentences


@dataclass(frozen=True)
class ChunkingOptions:
    max_tokens: int = 400
    min_tokens: int = 60
    overlap_tokens: int = 50

    def __post_init__(self):
        if not 0 < self.min_tokens < self.max_tokens:
            raise ValueError("Chunking needs 0 < min_tokens < max_tokens.")
        if not 0 <= self.overlap_tokens <= self.max_tokens // 2:
            raise ValueError("Chunk overlap must be between 0 and half of max_tokens.")


@dataclass
class Chunk:
    content: str
    heading_path: list[str]
    page_start: int | None
    page_end: int | None
    token_count: int
    block_types: list[str]


@dataclass
class _Piece:
    text: str
    kind: str
    page: int | None
    tokens: int


def contextualize(title: str, chunk: Chunk) -> str:
    """What gets embedded: the passage with the document title and heading path in front."""
    path = list(chunk.heading_path)
    if path and title and path[0].strip().lower() == title.strip().lower():
        path = path[1:]
    context = [line for line in (title, " > ".join(path)) if line]
    return "\n".join(context) + "\n\n" + chunk.content if context else chunk.content


class HybridChunker:
    def __init__(self, options: ChunkingOptions | None = None):
        self.options = options or ChunkingOptions()

    def chunk(self, blocks: list[Block]) -> list[Chunk]:
        chunks: list[Chunk] = []
        for path, section in self._sections(blocks):
            pieces = [piece for block in section for piece in self._split(block)]
            chunks.extend(self._pack(path, pieces))
        return self._merge_small(chunks)

    # ---------- 1. Structure ----------

    @staticmethod
    def _sections(blocks: list[Block]):
        """(heading path, blocks) for each run of blocks under the same headings."""
        stack: list[tuple[int, str]] = []
        current: list[Block] = []
        for block in blocks:
            if block.kind == HEADING:
                if current:
                    yield [text for _, text in stack], current
                    current = []
                while stack and stack[-1][0] >= block.level:
                    stack.pop()
                stack.append((block.level, " ".join(block.text.split())[:200]))
            elif block.text.strip():
                current.append(block)
        if current:
            yield [text for _, text in stack], current

    # ---------- 2. Size ----------

    def _split(self, block: Block) -> list[_Piece]:
        tokens = count_tokens(block.text)
        if tokens <= self.options.max_tokens:
            return [_Piece(block.text, block.kind, block.page, tokens)]
        if block.kind == TABLE and block.parts:
            texts = self._split_table(block)
        elif block.kind == LIST and block.parts:
            texts = self._pack_texts(block.parts, "\n")
        elif block.kind == CODE:
            texts = self._pack_texts(block.text.split("\n"), "\n")
        else:
            texts = self._windows(block.text)
        return [_Piece(text, block.kind, block.page, count_tokens(text)) for text in texts]

    def _split_table(self, block: Block) -> list[str]:
        """Rows packed under a repeated header, so every piece is a readable table."""
        budget = self.options.max_tokens - count_tokens(block.header)
        if budget < self.options.max_tokens // 4:
            # A header too big to repeat: cut the table like a list of rows.
            return self._pack_texts([block.header, *block.parts], "\n")
        return [f"{block.header}\n{rows}" for rows in self._pack_texts(block.parts, "\n", budget)]

    def _pack_texts(self, parts: list[str], separator: str, budget: int | None = None) -> list[str]:
        """Consecutive parts joined into texts of up to `budget` tokens; a part larger than
        the budget on its own is cut into windows."""
        budget = budget or self.options.max_tokens
        texts, current, used = [], [], 0
        for part in parts:
            cost = count_tokens(part)
            if cost > budget:
                if current:
                    texts.append(separator.join(current))
                    current, used = [], 0
                texts.extend(self._windows(part, budget))
                continue
            if current and used + cost > budget:
                texts.append(separator.join(current))
                current, used = [], 0
            current.append(part)
            used += cost
        if current:
            texts.append(separator.join(current))
        return texts

    def _windows(self, text: str, budget: int | None = None) -> list[str]:
        """Prose cut between sentences into windows of up to `budget` tokens, each starting with
        the previous window's last sentences (up to overlap_tokens)."""
        budget = budget or self.options.max_tokens
        overlap = min(self.options.overlap_tokens, budget // 2)
        sentences = [part for s in split_sentences(text) for part in hard_split(s, budget)]
        windows: list[str] = []
        current: list[tuple[str, int]] = []
        used = 0
        for sentence in sentences:
            cost = count_tokens(sentence)
            if current and used + cost > budget:
                windows.append(" ".join(s for s, _ in current))
                tail, kept = [], 0
                for previous, previous_cost in reversed(current):
                    if kept + previous_cost > overlap:
                        break
                    tail.insert(0, (previous, previous_cost))
                    kept += previous_cost
                if kept + cost > budget:
                    tail, kept = [], 0
                current, used = tail, kept
            current.append((sentence, cost))
            used += cost
        if current:
            windows.append(" ".join(s for s, _ in current))
        return windows

    def _pack(self, path: list[str], pieces: list[_Piece]) -> list[Chunk]:
        chunks, current, used = [], [], 0
        for piece in pieces:
            if current and used + piece.tokens > self.options.max_tokens:
                chunks.append(self._chunk_of(path, current))
                current, used = [], 0
            current.append(piece)
            used += piece.tokens
        if current:
            chunks.append(self._chunk_of(path, current))
        return chunks

    @staticmethod
    def _chunk_of(path: list[str], pieces: list[_Piece]) -> Chunk:
        content = "\n\n".join(p.text for p in pieces)
        pages = [p.page for p in pieces if p.page is not None]
        kinds = list(dict.fromkeys(p.kind for p in pieces))
        return Chunk(content, list(path), min(pages, default=None), max(pages, default=None), count_tokens(content), kinds)

    # ---------- 3. Merge ----------

    def _merge_small(self, chunks: list[Chunk]) -> list[Chunk]:
        merged: list[Chunk] = []
        for chunk in chunks:
            if merged:
                combined = self._combined(merged[-1], chunk)
                if combined is not None:
                    merged[-1] = combined
                    continue
            merged.append(chunk)
        return merged

    def _combined(self, a: Chunk, b: Chunk) -> Chunk | None:
        """a and b as one passage, or None when they shouldn't or can't be merged."""
        if a.token_count >= self.options.min_tokens and b.token_count >= self.options.min_tokens:
            return None
        common = _common_prefix(a.heading_path, b.heading_path)
        # Same section, siblings, or parent and child; not distant parts of the document.
        if len(common) < max(len(a.heading_path), len(b.heading_path)) - 1:
            return None
        content = _under(a, common) + "\n\n" + _under(b, common)
        tokens = count_tokens(content)
        if tokens > self.options.max_tokens:
            return None
        pages = [p for p in (a.page_start, a.page_end, b.page_start, b.page_end) if p is not None]
        return Chunk(
            content,
            common,
            min(pages, default=None),
            max(pages, default=None),
            tokens,
            list(dict.fromkeys(a.block_types + b.block_types)),
        )


def _common_prefix(a: list[str], b: list[str]) -> list[str]:
    prefix = []
    for x, y in zip(a, b, strict=False):
        if x != y:
            break
        prefix.append(x)
    return prefix


def _under(chunk: Chunk, common: list[str]) -> str:
    """The chunk's text with the headings below `common` written in as Markdown headings."""
    headings = [
        "#" * (len(common) + depth + 1) + " " + heading
        for depth, heading in enumerate(chunk.heading_path[len(common):])
    ]
    return "\n\n".join([*headings, chunk.content])
