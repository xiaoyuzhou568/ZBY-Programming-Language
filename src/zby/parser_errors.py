"""ZBY Parser error with stable source-file, line, and column data."""
from dataclasses import dataclass


@dataclass(eq=False)
class ZbySyntaxError(Exception):
    filename: str
    line: int
    column: int
    reason: str

    def __str__(self) -> str:
        return f"{self.filename}:{self.line}:{self.column}: SyntaxError: {self.reason}"
