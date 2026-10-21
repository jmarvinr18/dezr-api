"""Text helpers for ingestion: approximate token counts, sentence splitting and decoding."""

import re

_CJK = "぀-ヿ㐀-䶿一-鿿가-힯豈-﫿"
# One CJK character, a run of letters, a run of digits, or one punctuation mark.
_TOKEN = re.compile(rf"[{_CJK}]|[^\W\d_{_CJK}]+|\d+|[^\w\s]", re.UNICODE)


def count_tokens(text: str) -> int:
    """Roughly how many tokens an embedding model sees in `text`.

    No tokenizer download or model-specific vocabulary: words count about one token per seven
    letters, numbers one per three digits, punctuation and CJK characters one each. Close to
    BPE tokenizers for English, German and Tagalog prose, and on the high side for Japanese.
    """
    total = 0
    for match in _TOKEN.finditer(text):
        piece = match.group()
        if piece[0].isdigit():
            total += -(-len(piece) // 3)
        elif len(piece) > 1:
            total += -(-len(piece) // 7)
        else:
            total += 1
    return total


def hard_split(text: str, max_tokens: int) -> list[str]:
    """Cuts text into pieces of at most `max_tokens`, between tokens (for a run-on sentence or a
    huge table cell, where there is no better place to cut)."""
    pieces, start, used = [], 0, 0
    for match in _TOKEN.finditer(text):
        cost = count_tokens(match.group())
        if used and used + cost > max_tokens:
            pieces.append(text[start:match.start()].strip())
            start, used = match.start(), 0
        used += cost
    pieces.append(text[start:].strip())
    return [p for p in pieces if p]


# Words a full stop follows without ending the sentence (common in legal and business text).
_ABBREVIATIONS = {
    "mr", "mrs", "ms", "dr", "prof", "sr", "jr", "st", "no", "nos", "sec", "secs", "art", "arts",
    "par", "para", "vol", "p", "pp", "inc", "corp", "co", "ltd", "vs", "v", "etc", "e.g", "i.e",
    "cf", "fig", "approx", "dept", "atty", "hon", "gen", "gov", "rep", "ch", "cl", "jan", "feb",
    "mar", "apr", "jun", "jul", "aug", "sep", "sept", "oct", "nov", "dec", "bzw", "z.b", "ca",
    "nr", "abs", "s", "u.a", "d.h",
}
_BOUNDARY = re.compile(r"([.!?]+[\"'”’)\]]*)(\s+)|([。！？]+[」』）]*)")
_LAST_WORD = re.compile(r"([\w.]+)$")


def split_sentences(text: str) -> list[str]:
    """Sentences of a paragraph, keeping abbreviations ("Sec. 5", "Inc.") and initials intact."""
    text = text.strip()
    sentences, start = [], 0
    for match in _BOUNDARY.finditer(text):
        if match.group(1):
            word = _LAST_WORD.search(text[start:match.start()])
            word = word.group(1).lower().rstrip(".") if word else ""
            if match.group(1).startswith(".") and (word in _ABBREVIATIONS or (len(word) == 1 and word.isalpha())):
                continue
            following = text[match.end():match.end() + 1]
            if following.islower():
                continue
            cut = match.start() + len(match.group(1))
            sentences.append(text[start:cut].strip())
            start = match.end()
        else:
            sentences.append(text[start:match.end()].strip())
            start = match.end()
    if text[start:].strip():
        sentences.append(text[start:].strip())
    return [s for s in sentences if s]


def decode_text(data: bytes) -> str:
    """Bytes of a .txt / .md file as text: UTF-8 (with or without BOM), UTF-16 with a BOM, or
    Windows-1252 as a last resort (older Windows text files)."""
    if data.startswith(b"\xef\xbb\xbf"):
        return data[3:].decode("utf-8", errors="replace")
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16", errors="replace")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("cp1252", errors="replace")


def looks_like_text(data: bytes) -> bool:
    """True when the bytes are text rather than a binary file renamed to .txt / .md."""
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return True
    sample = data[:8192]
    if b"\x00" in sample:
        return False
    text = decode_text(sample)
    controls = sum(1 for c in text if ord(c) < 32 and c not in "\t\n\r\f")
    return controls <= len(text) * 0.01


def normalize_whitespace(text: str) -> str:
    return re.sub(r"[ \t ]+", " ", text).strip()
