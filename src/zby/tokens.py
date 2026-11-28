"""Token definitions and Unicode-code-point based source locations."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class TokenKind(str, Enum):
    IDENTIFIER = "IDENTIFIER"
    KEYWORD = "KEYWORD"
    INT_LITERAL = "INT_LITERAL"
    FLOAT_LITERAL = "FLOAT_LITERAL"
    STRING_LITERAL = "STRING_LITERAL"
    SYMBOL = "SYMBOL"
    EOF = "EOF"


@dataclass(frozen=True, slots=True)
class Position:
    """0-based Unicode code-point offset, 1-based line and column."""

    offset: int
    line: int
    column: int


@dataclass(frozen=True, slots=True)
class Span:
    """Half-open source span; the end position is exclusive."""

    start: Position
    end: Position


@dataclass(frozen=True, slots=True)
class Token:
    kind: TokenKind
    lexeme: str
    span: Span
    value: str | None = None

    @property
    def line(self) -> int:
        return self.span.start.line

    @property
    def column(self) -> int:
        return self.span.start.column
