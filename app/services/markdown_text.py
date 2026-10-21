"""Plain text from the site's Markdown, split into sections at its headings, for search.

Understands the MDC syntax the pages use: block components (::person-profile{...} ... ::),
their YAML props (::library-grid / --- / topics: ... / ---), inline components
(:japan-desk-email) and attribute blocks ({.dz-lead}). Their markup is dropped; text inside
them is kept.
"""

import re

_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_FENCE = re.compile(r"^\s*(```|~~~)")
_COMPONENT_OPEN = re.compile(r"^\s*::[a-zA-Z][\w-]*")
_COMPONENT_CLOSE = re.compile(r"^\s*::\s*$")
_PROPS_FENCE = re.compile(r"^\s*---\s*$")
_RULE = re.compile(r"^\s*([-*_])(\s*\1){2,}\s*$")
_ATTRIBUTE_TEXT = re.compile(r'\b(?:name|title|label)="([^"]*)"')

_INLINE_RULES = [
    (re.compile(r"!\[([^\]]*)\]\([^)]*\)"), r"\1"),  # images → alt text
    (re.compile(r"\[([^\]]+)\]\([^)]*\)"), r"\1"),  # links → link text
    (re.compile(r"(?<=[\])])\{[^}\n]*\}"), ""),  # [span]{.class} attributes
    (re.compile(r"(?<![\w:/]):[a-z][a-z0-9]*(?:-[a-z0-9]+)+(?:\{[^}\n]*\})?"), ""),  # :inline-component
    (re.compile(r"<[^>\n]+>"), ""),  # HTML tags
    (re.compile(r"(\*\*|__|\*|`|~~)"), ""),  # emphasis, code, strike
    (re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+"), ""),  # list markers
    (re.compile(r"^\s*>\s?"), ""),  # block quotes
]


def inline_text(line: str) -> str:
    for pattern, replacement in _INLINE_RULES:
        line = pattern.sub(replacement, line)
    return line.strip()


def _lines(markdown: str):
    """Yields ("heading", level, text) or ("text", 0, text) for each meaningful line."""
    lines = _COMMENT.sub("", markdown or "").splitlines()
    in_code = in_props = False
    previous_opened_component = False
    for line in lines:
        if in_props:
            in_props = not _PROPS_FENCE.match(line)
            continue
        if _FENCE.match(line):
            in_code = not in_code
            previous_opened_component = False
            continue
        if in_code:
            yield "text", 0, line.strip()
            continue
        if previous_opened_component and _PROPS_FENCE.match(line):
            # A component's YAML props: data, not text.
            in_props = True
            previous_opened_component = False
            continue
        previous_opened_component = bool(_COMPONENT_OPEN.match(line))
        if previous_opened_component:
            # ::person-profile{name="Jane Cruz"}: the name is worth finding.
            for value in _ATTRIBUTE_TEXT.findall(line):
                yield "text", 0, value.strip()
            continue
        if _COMPONENT_CLOSE.match(line) or _RULE.match(line):
            continue
        heading = _HEADING.match(line)
        if heading:
            yield "heading", len(heading.group(1)), inline_text(heading.group(2))
        else:
            yield "text", 0, inline_text(line)


def to_text(markdown: str) -> str:
    return " ".join(text for _, _, text in _lines(markdown) if text)


def slugify_heading(text: str) -> str:
    """Anchor id for a heading, as the site renders it ("Banking & Finance" → "banking--finance")."""
    slug = re.sub(r"[^\w\- ]", "", text.lower(), flags=re.UNICODE).strip()
    return re.sub(r" ", "-", slug)


def sections(path: str, title: str, markdown: str) -> list[dict]:
    """The page split at its headings: {id, title, titles, level, content}, the same shape as
    @nuxt/content's search sections. Text before the first heading belongs to the page itself."""
    found = [{"id": path, "title": title, "titles": [], "level": 1, "parts": []}]
    trail: list[tuple[int, str]] = []
    for kind, level, text in _lines(markdown):
        if kind == "heading":
            trail = [(lvl, t) for lvl, t in trail if lvl < level]
            found.append(
                {
                    "id": f"{path}#{slugify_heading(text)}",
                    "title": text,
                    "titles": [title, *(t for _, t in trail)],
                    "level": level,
                    "parts": [],
                }
            )
            trail.append((level, text))
        elif text:
            found[-1]["parts"].append(text)

    result = []
    for section in found:
        content = " ".join(section.pop("parts"))
        # Skip an empty intro (e.g. a page that starts with a heading); keep empty headings.
        if section["level"] == 1 and section["id"] == path and not content and len(found) > 1:
            continue
        result.append({**section, "content": content})
    return result
