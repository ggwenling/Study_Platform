"""Structure-aware text chunking for newly built material indexes."""

import re
from collections.abc import Iterator

_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
_HEADING = re.compile(r"^ {0,3}#{1,6}[ \t]+")
_SENTENCE = re.compile(r".+?(?:[。！？.!?;；]+|\n+|$)", re.DOTALL)
_SEPARATOR_CELL = re.compile(r":?-{3,}:?")
_SENTENCE_END = "。！？.!?;；"


def semantic_chunks(
    text: str, chunk_size: int = 800, overlap: int = 120, atomic_max: int = 1600
) -> Iterator[str]:
    """Yield chunks that prefer semantic boundaries and keep Markdown atoms intact."""
    if chunk_size < 1 or overlap < 0 or overlap >= chunk_size or atomic_max < chunk_size:
        raise ValueError("Invalid chunk sizes")

    pending: list[str] = []

    def flush_pending():
        if pending:
            yield from _split_paragraphs("\n\n".join(pending), chunk_size, overlap)
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
        yield from _atomic_chunks(kind, block, atomic_max)

    yield from flush_pending()


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


def _split_paragraphs(text: str, size: int, overlap: int) -> Iterator[str]:
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
            yield from _split_sentences(paragraph, size, overlap)
            continue
        extra = len(paragraph) + (2 if current else 0)
        if current and current_len + extra > size:
            yield from flush()
        current.append(paragraph)
        current_len += len(paragraph) + (2 if len(current) > 1 else 0)
    yield from flush()


def _split_sentences(text: str, size: int, overlap: int) -> Iterator[str]:
    sentences = [match.group() for match in _SENTENCE.finditer(text)]
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


def _atomic_chunks(kind: str, block: str, limit: int) -> list[str]:
    if len(block) <= limit:
        return [block]
    lines = block.splitlines()
    if kind == "code":
        opener = lines[0]
        closing = lines[-1] if len(lines) > 1 and _is_fence_close(lines[0], lines[-1]) else ""
        body = lines[1:-1] if closing else lines[1:]
        return _wrap_line_parts(opener, closing, body, limit)
    if kind == "table":
        prefix = "\n".join(lines[:2])
        allowance = limit - len(prefix) - 1
        if allowance <= 0:
            # An oversized header cannot be repeated inside the per-chunk limit.
            return [block[start:start + limit] for start in range(0, len(block), limit)]
        return [f"{prefix}\n{part}" for part in _line_parts(lines[2:], allowance)]
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
        parts = _line_parts(body.splitlines(), max(1, limit - 6))
        return [f"$$\n{part}\n$$" for part in parts]
    return _line_parts(lines, limit)


def _is_fence_close(opening: str, closing: str) -> bool:
    start, end = _FENCE.match(opening), _FENCE.match(closing)
    return bool(
        start
        and end
        and start.group(1)[0] == end.group(1)[0]
        and len(end.group(1)) >= len(start.group(1))
        and not end.group(2).strip()
    )


def _wrap_line_parts(opener: str, closing: str, lines: list[str], limit: int) -> list[str]:
    wrapper_size = len(opener) + len(closing) + 2
    body_limit = max(1, limit - wrapper_size)
    return [f"{opener}\n{part}\n{closing}" if closing else f"{opener}\n{part}"
            for part in _line_parts(lines, body_limit)]


def _line_parts(lines: list[str], limit: int) -> list[str]:
    parts: list[str] = []
    current: list[str] = []
    current_size = 0
    for line in lines:
        pieces = [line[start:start + limit] for start in range(0, len(line), limit)] if line else [""]
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
