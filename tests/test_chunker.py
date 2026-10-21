import pytest

from app.services.ingestion import ChunkingOptions, HybridChunker, contextualize
from app.services.ingestion.blocks import HEADING, LIST, PARAGRAPH, Block, list_block, table_block
from app.services.ingestion.chunker import Chunk
from app.services.ingestion.text import count_tokens, hard_split, split_sentences

SENTENCE = "The firm advises foreign investors on incorporation and licensing in Makati City."


def heading(text, level, page=None):
    return Block(HEADING, text, level=level, page=page)


def paragraph(text, page=None):
    return Block(PARAGRAPH, text, page=page)


def chunker(max_tokens=100, min_tokens=20, overlap_tokens=15):
    return HybridChunker(ChunkingOptions(max_tokens=max_tokens, min_tokens=min_tokens, overlap_tokens=overlap_tokens))


# ---------- text helpers ----------


def test_token_counts_are_rough_but_sensible():
    assert count_tokens("") == 0
    assert count_tokens("the firm") == 2
    assert count_tokens("incorporation") == 2
    assert count_tokens("PHP 50,000.") == 5  # PHP, 50, ",", 000, "."
    assert count_tokens("日本語") == 3


def test_sentences_keep_abbreviations_and_initials():
    text = "See Sec. 5 of the Code. Ask Atty. J. Cruz of ABC Inc. for details! It is due 1 Jan. 2027. 次です。終わり。"
    assert split_sentences(text) == [
        "See Sec. 5 of the Code.",
        "Ask Atty. J. Cruz of ABC Inc. for details!",
        "It is due 1 Jan. 2027.",
        "次です。",
        "終わり。",
    ]


def test_hard_split_respects_the_budget():
    pieces = hard_split("word " * 50, 7)
    assert all(count_tokens(p) <= 7 for p in pieces)
    assert " ".join(pieces).split() == ["word"] * 50


# ---------- 1. structure ----------


def test_passages_never_span_sections_and_keep_their_heading_path():
    long_text = " ".join([SENTENCE] * 4)
    blocks = [
        heading("Guide", 1),
        heading("Corporate", 2),
        paragraph(long_text),
        heading("Incorporation", 3),
        paragraph(long_text),
        heading("Tax", 2),
        paragraph(long_text),
    ]
    chunks = chunker().chunk(blocks)
    assert [c.heading_path for c in chunks] == [
        ["Guide", "Corporate"],
        ["Guide", "Corporate", "Incorporation"],
        ["Guide", "Tax"],
    ]
    assert all(c.content == long_text for c in chunks)


def test_small_blocks_of_a_section_are_packed_together():
    blocks = [heading("Fees", 1), paragraph(SENTENCE), list_block(["- Hourly", "- Fixed"]), paragraph(SENTENCE)]
    [chunk] = chunker().chunk(blocks)
    assert chunk.block_types == ["paragraph", "list"]
    assert chunk.content == f"{SENTENCE}\n\n- Hourly\n- Fixed\n\n{SENTENCE}"


def test_text_before_the_first_heading_has_an_empty_path():
    [chunk, *_] = chunker().chunk([paragraph(" ".join([SENTENCE] * 3)), heading("A", 1), paragraph(SENTENCE * 3)])
    assert chunk.heading_path == []


# ---------- 2. size ----------


def test_long_prose_is_cut_between_sentences_with_overlap():
    sentences = [f"Sentence number {i} explains a separate point about the firm." for i in range(30)]
    chunks = chunker(max_tokens=60, overlap_tokens=15).chunk([heading("Long", 1), paragraph(" ".join(sentences))])

    assert len(chunks) > 3
    assert all(c.token_count <= 60 for c in chunks)
    for previous, current in zip(chunks, chunks[1:], strict=False):
        last_sentence = split_sentences(previous.content)[-1]
        # Each window starts with the end of the previous one, cut at a sentence boundary.
        assert current.content.startswith(last_sentence)
    # Nothing is lost.
    text = " ".join(c.content for c in chunks)
    assert all(s in text for s in sentences)


def test_oversized_table_is_split_between_rows_with_the_header_repeated():
    rows = [["Service", "Fee"]] + [[f"Service number {i}", f"PHP {i},000"] for i in range(40)]
    table = table_block(rows)
    chunks = chunker(max_tokens=80, min_tokens=10).chunk([heading("Fees", 1), table])

    assert len(chunks) > 2
    for chunk in chunks:
        assert chunk.content.startswith("| Service | Fee |\n| --- | --- |\n")
        assert chunk.token_count <= 80
        assert chunk.block_types == ["table"]
    body_rows = [line for c in chunks for line in c.content.split("\n")[2:]]
    assert body_rows == table.parts  # every row exactly once, in order


def test_a_table_that_fits_is_never_cut():
    table = table_block([["A", "B"]] + [[str(i), "x"] for i in range(10)])
    chunks = chunker(max_tokens=200).chunk([heading("T", 1), paragraph(" ".join([SENTENCE] * 8)), table])
    assert any(c.content.endswith(table.text) or table.text in c.content for c in chunks)


def test_oversized_list_is_split_between_items():
    items = [f"- Item {i}: a requirement for foreign investors" for i in range(30)]
    chunks = chunker(max_tokens=50, min_tokens=10).chunk([heading("Requirements", 1), list_block(items)])
    assert len(chunks) > 1
    assert [line for c in chunks for line in c.content.split("\n")] == items


# ---------- 3. merge ----------


def test_tiny_sibling_sections_merge_with_their_headings_written_in():
    blocks = [
        heading("Practice", 1),
        heading("Immigration", 2),
        paragraph("We obtain visas."),
        heading("Employment", 2),
        paragraph("We advise employers."),
    ]
    [chunk] = chunker().chunk(blocks)
    assert chunk.heading_path == ["Practice"]
    assert chunk.content == "## Immigration\n\nWe obtain visas.\n\n## Employment\n\nWe advise employers."


def test_tiny_parent_intro_merges_into_its_first_subsection():
    blocks = [heading("Corporate", 1), paragraph("Overview."), heading("Mergers", 2), paragraph(SENTENCE)]
    [chunk] = chunker().chunk(blocks)
    assert chunk.heading_path == ["Corporate"]
    assert chunk.content == f"Overview.\n\n## Mergers\n\n{SENTENCE}"


def test_distant_sections_are_not_merged():
    blocks = [
        heading("A", 1), heading("A1", 2), heading("A1a", 3), paragraph("Tiny."),
        heading("B", 1), paragraph("Also tiny."),
    ]
    chunks = chunker().chunk(blocks)
    assert [c.heading_path for c in chunks] == [["A", "A1", "A1a"], ["B"]]


def test_merging_never_exceeds_the_budget():
    big = " ".join([SENTENCE] * 6)  # ~90 tokens
    blocks = [heading("A", 1), paragraph(big), heading("B", 1), paragraph("Tiny.")]
    chunks = chunker(max_tokens=100).chunk(blocks)
    assert len(chunks) == 2 and all(c.token_count <= 100 for c in chunks)


def test_pages_span_the_merged_blocks():
    blocks = [heading("A", 1, page=1), paragraph("One.", page=1), heading("B", 1, page=2), paragraph("Two.", page=3)]
    [chunk] = chunker().chunk(blocks)
    assert (chunk.page_start, chunk.page_end) == (1, 3)


# ---------- context ----------


def test_contextualize_puts_title_and_headings_first():
    chunk = Chunk("Fees are due in 30 days.", ["Engagement", "Billing"], None, None, 7, [PARAGRAPH])
    assert contextualize("Client Guide", chunk) == "Client Guide\nEngagement > Billing\n\nFees are due in 30 days."
    same = Chunk("x", ["Client Guide", "Billing"], None, None, 1, [LIST])
    assert contextualize("Client Guide", same) == "Client Guide\nBilling\n\nx"


@pytest.mark.parametrize(("max_tokens", "min_tokens", "overlap"), [(100, 100, 10), (100, 0, 10), (100, 20, 60)])
def test_bad_options_are_refused(max_tokens, min_tokens, overlap):
    with pytest.raises(ValueError):
        ChunkingOptions(max_tokens=max_tokens, min_tokens=min_tokens, overlap_tokens=overlap)
