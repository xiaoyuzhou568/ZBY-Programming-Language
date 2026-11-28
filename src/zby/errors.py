"""ZBY lexer errors: no host-language traceback exposed by the CLI."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(eq=False)
class LexError(Exception):
    filename: str
    line: int
    column: int
    reason: str

    def __str__(self) -> str:
        return f"{self.filename}:{self.line}:{self.column}: LexError: {self.reason}"
