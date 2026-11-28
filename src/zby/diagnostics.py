"""ZBY source-level diagnostics for the command line.

Only formats errors already produced by the Lexer, Parser and interpreter.
It does not introduce additional static checks or change the execution order.
"""
from __future__ import annotations

import unicodedata
from .errors import LexError
from .parser_errors import ZbySyntaxError


def _display_parts(text: str) -> tuple[str, list[int]]:
    """Return display text plus visual offsets for each Unicode code point.

    Reported line/column numbers remain *code-point* based. The caret layout
    accounts for wide East Asian characters, combining marks, and tabs.
    """
    pieces: list[str] = []
    offsets = [0]
    visual_col = 0
    for char in text:
        if char == "\t":
            width = 4 - visual_col % 4
            pieces.append(" " * width)
        elif unicodedata.category(char).startswith("C"):
            replacement = f"\\u{ord(char):04X}" if ord(char) <= 0xFFFF else f"\\U{ord(char):08X}"
            pieces.append(replacement)
            width = len(replacement)
        else:
            pieces.append(char)
            if unicodedata.combining(char):
                width = 0
            else:
                width = 2 if unicodedata.east_asian_width(char) in ("W", "F") else 1
        visual_col += width
        offsets.append(visual_col)
    return "".join(pieces), offsets


def render_diagnostic(error: LexError | ZbySyntaxError | Exception,
                      source: str | None,
                      *, max_stack_frames: int = 12) -> str:
    """Format source excerpt/caret, structured type hints and bounded ZBY stack.

    File locations refer to 1-based Unicode-code-point positions, not byte
    offsets or terminal visual cells. A missing/invalid source simply omits
    the excerpt without masking the original diagnostic.
    """
    if max_stack_frames < 1:
        raise ValueError("max_stack_frames must be >= 1")
    filename = getattr(error, "filename", "<source>")
    line = getattr(error, "line", 0)
    column = getattr(error, "column", 0)
    category = getattr(error, "category", None)
    if category is None:
        category = "LexError" if isinstance(error, LexError) else (
            "SyntaxError" if isinstance(error, ZbySyntaxError) else "Error")
    detail = getattr(error, "detail", None)
    if detail is None:
        detail = getattr(error, "reason", str(error))
    lines = [f"{filename}:{line}:{column}: {category}: {detail}"]
    if source is not None and isinstance(line, int) and isinstance(column, int):
        # split('\n') is intentional: Unicode U+2028 is not a ZBY source newline.
        source_lines = source.removeprefix("\ufeff").split("\n")
        if 1 <= line <= len(source_lines) and column >= 1:
            original = source_lines[line - 1].removesuffix("\r")
            display, offsets = _display_parts(original)
            start = min(column - 1, len(original))
            end_col = getattr(error, "end_column", None)
            end_line = getattr(error, "end_line", None)
            if end_line == line and isinstance(end_col, int) and end_col > column:
                end = min(end_col - 1, len(original))
            else:
                end = min(start + 1, len(original))
            width = max(1, offsets[end] - offsets[start])
            line_number = str(line)
            lines.extend([
                f"  {line_number} | {display}",
                f"  {' ' * len(line_number)} | {' ' * offsets[start]}{'^' * min(width, 60)}",
            ])
    expected = getattr(error, "expected_type", None)
    actual = getattr(error, "actual_type", None)
    if expected is not None and actual is not None:
        lines.extend([f"期望类型：{expected}", f"实际类型：{actual}"])
    calls = getattr(error, "calls", ())
    if calls:
        count = len(calls)
        if count > max_stack_frames:
            first_count = min(2, max_stack_frames // 2)
            last_count = max_stack_frames - first_count
            entries = list(calls[:first_count]) + [f"... 省略 {count - max_stack_frames} 层调用 ..."] + list(calls[-last_count:])
        else:
            entries = list(calls)
        lines.append("ZBY 调用路径（外层 → 内层）：")
        lines.extend(f"  {entry}" for entry in entries)
    return "\n".join(lines)


def source_for_diagnostic(path: object) -> str | None:
    """Recover original newlines for the source excerpt without altering errors."""
    try:
        from pathlib import Path
        return Path(path).read_bytes().decode("utf-8", errors="strict")
    except (OSError, UnicodeError):
        return None
