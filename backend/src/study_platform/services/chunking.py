"""Structure-aware text chunking for newly built material indexes."""

import re
from collections.abc import Iterator

SEMANTIC_PROFILE = "semantic-v3"
SEMANTIC_V4_PROFILE = "semantic-v4"
KNOWN_PIPELINE_PROFILES = frozenset({SEMANTIC_PROFILE, SEMANTIC_V4_PROFILE})

_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
_HEADING = re.compile(r"^ {0,3}#{1,6}[ \t]+")
_SENTENCE = re.compile(r".+?(?:[。！？.!?;；]+|\n+|$)", re.DOTALL)
_SEPARATOR_CELL = re.compile(r":?-{3,}:?")
_SENTENCE_END = "。！？.!?;；"


def resolve_pipeline_profile(value) -> str:
    """Missing means the current default. Any other name is refused."""
    if value is None or value == "":
        return SEMANTIC_PROFILE
    if value in KNOWN_PIPELINE_PROFILES:
        return value
    raise ValueError("unknown pipeline profile")


def semantic_v4_chunks(
    text: str, chunk_size: int = 800, overlap: int = 120, atomic_max: int = 1600,
) -> Iterator[str]:
    """Like semantic-v3, but a single newline is a wrap, not a sentence end."""
    return semantic_chunks(
        text, chunk_size, overlap, atomic_max, newline_is_sentence=False,
    )


def plain_text_units(text: str, profile=None) -> Iterator[tuple[str, dict]]:
    """text/plain units. v3 is one stripped line. v4 joins a wrapped paragraph."""
    selected = resolve_pipeline_profile(profile)
    lines = text.splitlines()
    if selected != SEMANTIC_V4_PROFILE:
        for number, line in enumerate(lines, 1):
            if line.strip():
                yield line.strip(), {"line": number}
        return
    start = None
    buffer: list[str] = []
    for number, line in enumerate(lines, 1):
        if line.strip():
            if start is None:
                start = number
            buffer.append(line.strip())
            continue
        if buffer:
            yield "\n".join(buffer), {"start_line": start, "end_line": number - 1}
            buffer, start = [], None
    if buffer:
        yield "\n".join(buffer), {"start_line": start, "end_line": len(lines)}


def segment_unit(
    text: str, locator: dict | None, profile=None, *,
    chunk_size: int = 800, overlap: int = 120, atomic_max: int = 1600,
) -> Iterator[tuple[str, dict]]:
    """Chunk one extracted unit. v3 keeps its locator. v4 narrows to the fragment."""
    selected = resolve_pipeline_profile(profile)
    source_locator = dict(locator or {})
    if selected != SEMANTIC_V4_PROFILE:
        for part in semantic_chunks(text, chunk_size, overlap, atomic_max):
            yield part, dict(source_locator)
        return
    cursor = 0
    for part in semantic_v4_chunks(text, chunk_size, overlap, atomic_max):
        narrowed, cursor = narrow_locator(text, part, source_locator, cursor=cursor)
        yield part, narrowed


def narrow_locator(source: str, fragment: str, locator: dict | None, *, cursor: int = 0):
    """Point at the fragment that was actually stored. Do not invent a wider span."""
    loc = dict(locator or {})
    found = _find_fragment(source, fragment, cursor)
    if found is None:
        for key in ("line", "start_line", "end_line", "start_char", "end_char"):
            loc.pop(key, None)
        return loc, cursor
    loc["start_char"] = found
    loc["end_char"] = found + len(fragment)
    if "start_line" in loc and "end_line" in loc:
        start, end = _line_span(source, found, len(fragment), loc["start_line"])
        loc["start_line"] = start
        loc["end_line"] = end
    if loc.get("ocr") is not None and fragment != source and fragment.strip() != source.strip():
        loc.pop("ocr", None)
    return loc, found + len(fragment)


def _find_fragment(source: str, fragment: str, cursor: int) -> int | None:
    if not fragment:
        return None
    window = max(0, cursor - 200)
    found = source.find(fragment, window)
    if found < 0:
        found = source.find(fragment)
    if found < 0 and fragment.strip() == source.strip() and source.strip():
        found = source.find(source.strip())
    return found if found >= 0 else None


def _line_span(source: str, found: int, length: int, base: int) -> tuple[int, int]:
    start_off = source[:found].count("\n")
    end_pos = found + length
    while end_pos > found and source[end_pos - 1] == "\n":
        end_pos -= 1
    end_off = source[:end_pos].count("\n") if end_pos else start_off
    return base + start_off, base + end_off


def semantic_chunks(
    text: str, chunk_size: int = 800, overlap: int = 120, atomic_max: int = 1600,
    *, _legacy_atoms: bool = False, newline_is_sentence: bool = True,
) -> Iterator[str]:
    """Yield chunks that prefer semantic boundaries and keep Markdown atoms intact."""
    if chunk_size < 1 or overlap < 0 or overlap >= chunk_size or atomic_max < chunk_size:
        raise ValueError("Invalid chunk sizes")

    pending: list[str] = []

    def flush_pending():
        if pending:
            yield from _split_paragraphs(
                "\n\n".join(pending), chunk_size, overlap, newline_is_sentence,
            )
            pending.clear()

    for kind, block in _blocks(text):
        if kind == "text":
            if block:
                first_line = block.splitlines()[0]
                if _HEADING.match(first_line):
                    yield from flush_pending()
                pending.append(block)
            continue

        yield from flush_pending()
        yield from _atomic_chunks(kind, block, atomic_max, legacy_atoms=_legacy_atoms)

    yield from flush_pending()


def semantic_v2_chunks(text: str) -> Iterator[str]:
    """Replay the original v2 output solely to recognize unedited stored chunks."""
    return semantic_chunks(text, _legacy_atoms=True)


def _blocks(text: str) -> Iterator[tuple[str, str]]:
    lines = text.splitlines()
    normal: list[str] = []
    index = 0

    def flush_normal():
        if normal:
            value = "\n".join(normal).strip()
            normal.clear()
            if value:
                yield "text", value

    while index < len(lines):
        line = lines[index]
        fence = _FENCE.match(line)
        if fence:
            yield from flush_normal()
            marker = fence.group(1)
            end = index + 1
            while end < len(lines):
                close = _FENCE.match(lines[end])
                if (
                    close
                    and close.group(1)[0] == marker[0]
                    and len(close.group(1)) >= len(marker)
                    and not close.group(2).strip()
                ):
                    end += 1
                    break
                end += 1
            yield "code", "\n".join(lines[index:end])
            index = end
            continue

        if line.strip().startswith("$$"):
            yield from flush_normal()
            end = index + 1
            opening = line.find("$$")
            if line.find("$$", opening + 2) < 0:
                while end < len(lines):
                    end += 1
                    if "$$" in lines[end - 1]:
                        break
            yield "math", "\n".join(lines[index:end])
            index = end
            continue

        if index + 1 < len(lines) and "|" in line and _is_table_separator(lines[index + 1]):
            yield from flush_normal()
            end = index + 2
            while end < len(lines) and "|" in lines[end] and lines[end].strip():
                end += 1
            yield "table", "\n".join(lines[index:end])
            index = end
            continue

        if _HEADING.match(line):
            yield from flush_normal()
        normal.append(line)
        index += 1

    yield from flush_normal()


def _is_table_separator(line: str) -> bool:
    cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
    return bool(cells) and all(_SEPARATOR_CELL.fullmatch(cell) for cell in cells)


def _split_paragraphs(
    text: str, size: int, overlap: int, newline_is_sentence: bool = True,
) -> Iterator[str]:
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", text) if part.strip()]
    current: list[str] = []
    current_len = 0

    def flush():
        nonlocal current, current_len
        if current:
            yield "\n\n".join(current)
            current, current_len = [], 0

    for paragraph in paragraphs:
        if len(paragraph) > size:
            yield from flush()
            yield from _split_sentences(paragraph, size, overlap, newline_is_sentence)
            continue
        extra = len(paragraph) + (2 if current else 0)
        if current and current_len + extra > size:
            yield from flush()
        current.append(paragraph)
        current_len += len(paragraph) + (2 if len(current) > 1 else 0)
    yield from flush()


def _sentences_keeping_wraps(text: str) -> list[str]:
    """Sentence ends stay. A physical wrap, a decimal, or an abbreviation does not."""
    sentences: list[str] = []
    start = 0
    index = 0
    while index < len(text):
        if not _is_sentence_end(text, index):
            index += 1
            continue
        sentences.append(text[start:index + 1])
        start = index + 1
        index += 1
    if start < len(text):
        sentences.append(text[start:])
    return [sentence for sentence in sentences if sentence]


def _is_sentence_end(text: str, index: int) -> bool:
    character = text[index]
    if character in "。！？!?；;":
        return True
    if character != ".":
        return False
    nxt = text[index + 1] if index + 1 < len(text) else ""
    if nxt and (nxt.isalnum() or nxt == "."):
        return False
    window = text[max(0, index - 16):index + 1]
    if re.search(r"(?:[A-Za-z]\.){2,}$", window):
        return False
    if re.search(r"(?:^|[^A-Za-z])[A-Z][a-z]{1,3}\.$", window):
        return False
    return not nxt or nxt.isspace() or nxt.isupper() or nxt in "\"'”’)]）"


def _split_sentences(
    text: str, size: int, overlap: int, newline_is_sentence: bool = True,
) -> Iterator[str]:
    if newline_is_sentence:
        sentences = [match.group() for match in _SENTENCE.finditer(text)]
    else:
        sentences = _sentences_keeping_wraps(text)
    if not sentences:
        sentences = [text]

    chunks: list[str] = []
    current: list[str] = []
    current_len = 0

    def flush():
        nonlocal current, current_len
        if current:
            chunks.append("".join(current).strip())
            current, current_len = [], 0

    for sentence in sentences:
        if len(sentence) > size:
            flush()
            chunks.extend(_split_long_text(sentence.strip(), size, overlap))
            continue
        if current and current_len + len(sentence) > size:
            previous = current[:]
            flush()
            current = _overlap_sentences(previous, sentence, size, overlap)
            current_len = sum(map(len, current))
        current.append(sentence)
        current_len += len(sentence)
    flush()
    yield from (chunk for chunk in chunks if chunk)


def _overlap_sentences(previous: list[str], following: str, size: int, overlap: int) -> list[str]:
    capacity = size - len(following)
    if capacity <= 0 or overlap <= 0:
        return []
    selected: list[str] = []
    total = 0
    for sentence in reversed(previous):
        if total + len(sentence) > capacity:
            break
        selected.insert(0, sentence)
        total += len(sentence)
        if total >= overlap:
            break
    if selected:
        return selected
    suffix = _safe_suffix(previous[-1], min(overlap, capacity))
    return [suffix] if suffix else []


def _safe_suffix(text: str, limit: int) -> str:
    if limit <= 0:
        return ""
    start = max(0, len(text) - limit)
    while start < len(text) and not text[start].isspace():
        start += 1
    return text[start:].lstrip()


def _split_long_text(text: str, size: int, overlap: int) -> list[str]:
    parts: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + size, len(text))
        if end < len(text):
            lower_bound = start + max(1, size // 2)
            boundaries = [
                pos + 1 for pos in range(lower_bound, end)
                if text[pos] in _SENTENCE_END and (pos + 1 == len(text) or text[pos + 1].isspace())
            ]
            spaces = [pos for pos in range(lower_bound, end) if text[pos].isspace()]
            if boundaries:
                end = boundaries[-1]
            elif spaces:
                end = spaces[-1]
        piece = text[start:end].strip()
        if piece:
            parts.append(piece)
        if end >= len(text):
            break
        start = max(start + 1, end - overlap)
        while start < end and not text[start].isspace():
            start += 1
        while start < end and text[start].isspace():
            start += 1
    return parts


def _atomic_chunks(kind: str, block: str, limit: int, *, legacy_atoms: bool) -> list[str]:
    if len(block) <= limit:
        return [block]
    lines = block.splitlines()
    if kind == "code":
        opener = lines[0]
        closing = lines[-1] if len(lines) > 1 and _is_fence_close(lines[0], lines[-1]) else ""
        body = lines[1:-1] if closing else lines[1:]
        body_limit = max(1, limit - len(opener) - len(closing) - 2)
        return [f"{opener}\n{part}\n{closing}" if closing else f"{opener}\n{part}"
                for part in _line_parts(body, body_limit, legacy_atoms=legacy_atoms)]
    if kind == "table":
        prefix = "\n".join(lines[:2])
        allowance = limit - len(prefix) - 1
        if allowance <= 0 and not legacy_atoms:
            # An oversized header cannot be repeated inside the per-chunk limit.
            return [block[start:start + limit] for start in range(0, len(block), limit)]
        return [f"{prefix}\n{part}"
                for part in _line_parts(lines[2:], max(1, allowance), legacy_atoms=legacy_atoms)]
    if kind == "math":
        opening = lines[0].find("$$")
        same_line_close = lines[0].find("$$", opening + 2)
        if len(lines) == 1 and same_line_close >= 0:
            body_lines = [lines[0][opening + 2 : same_line_close]]
        else:
            closing = lines[-1].find("$$") if len(lines) > 1 else -1
            first = lines[0][opening + 2 :].strip()
            last = lines[-1][:closing].strip() if closing >= 0 else lines[-1]
            body_lines = [line for line in (first, *lines[1:-1], last) if line]
        body = "\n".join(body_lines)
        parts = _line_parts(body.splitlines(), max(1, limit - 6), legacy_atoms=legacy_atoms)
        return [f"$$\n{part}\n$$" for part in parts]
    return _line_parts(lines, limit, legacy_atoms=legacy_atoms)


def _is_fence_close(opening: str, closing: str) -> bool:
    start, end = _FENCE.match(opening), _FENCE.match(closing)
    return bool(
        start
        and end
        and start.group(1)[0] == end.group(1)[0]
        and len(end.group(1)) >= len(start.group(1))
        and not end.group(2).strip()
    )


def _line_parts(lines: list[str], limit: int, *, legacy_atoms: bool) -> list[str]:
    parts: list[str] = []
    current: list[str] = []
    current_size = 0
    for line in lines:
        if len(line) <= limit:
            pieces = [line]
        elif legacy_atoms:
            pieces = _split_long_text(line, limit, 0)
        else:
            pieces = [line[start:start + limit] for start in range(0, len(line), limit)]
        for piece in pieces:
            extra = len(piece) + (1 if current else 0)
            if current and current_size + extra > limit:
                parts.append("\n".join(current))
                current, current_size = [], 0
            current.append(piece)
            current_size += len(piece) + (1 if len(current) > 1 else 0)
    if current:
        parts.append("\n".join(current))
    return parts
