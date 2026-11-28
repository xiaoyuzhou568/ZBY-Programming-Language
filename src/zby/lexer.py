"""ZBY V0.2 lexer prototype.

Implements accepted lexical decisions DD-008, DD-021, DD-052 through DD-056.
This module emits tokens; it is NOT a Parser, static type checker or runtime.

Important: numeric lexemes are preserved verbatim. The finite-binary64
range check belongs to runtime evaluation, not to this lexer.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import sys
from typing import Final

from .errors import LexError
from .tokens import Position, Span, Token, TokenKind


KEYWORDS: Final[frozenset[str]] = frozenset({
    "let", "fn", "return", "struct", "if", "else", "while", "for",
    "break", "continue", "true", "false", "and", "or", "not",
    "int", "float", "string", "bool", "void",
})

# Built-in names are IDENTIFIER tokens. Name-collision checks are NOT lexical.
BUILTIN_NAMES: Final[frozenset[str]] = frozenset({
    "print", "input", "len", "push", "pop", "clone", "to_int",
    "to_float", "to_string", "int_div",
})

TWO_CHAR_SYMBOLS: Final[frozenset[str]] = frozenset({
    "->", "<=", ">=", "==", "!=",
})
ONE_CHAR_SYMBOLS: Final[frozenset[str]] = frozenset(
    "+-*/%<>=()[]{}.,:;"
)
ESCAPES: Final[dict[str, str]] = {
    "n": "\n", "t": "\t", "r": "\r", '"': '"', "\\": "\\",
}


def _ascii_digit(c: str) -> bool:
    return "0" <= c <= "9" if c else False


def _ascii_letter(c: str) -> bool:
    return ("a" <= c <= "z") or ("A" <= c <= "Z") if c else False


def _identifier_start(c: str) -> bool:
    return _ascii_letter(c) or c == "_"


def _identifier_continue(c: str) -> bool:
    return _identifier_start(c) or _ascii_digit(c)


class Lexer:
    def __init__(self, source: str, filename: str = "<source>") -> None:
        self.source = source
        self.filename = filename
        self.i = 0
        self.line = 1
        self.column = 1
        self.tokens: list[Token] = []

    def position(self) -> Position:
        return Position(self.i, self.line, self.column)

    def current(self) -> str:
        return self.source[self.i] if self.i < len(self.source) else ""

    def peek(self, delta: int = 1) -> str:
        at = self.i + delta
        return self.source[at] if at < len(self.source) else ""

    def advance(self) -> str:
        """Advance one Unicode code point, treating CRLF as one line break."""
        c = self.current()
        if not c:
            return ""
        if c == "\r" and self.peek() == "\n":
            self.i += 2
            self.line += 1
            self.column = 1
            return "\r\n"
        self.i += 1
        if c == "\n":
            self.line += 1
            self.column = 1
        else:
            self.column += 1
        return c

    def error(self, reason: str, at: Position | None = None) -> None:
        pos = at if at is not None else self.position()
        raise LexError(self.filename, pos.line, pos.column, reason)

    def emit(self, kind: TokenKind, start: Position, value: str | None = None) -> None:
        self.tokens.append(Token(
            kind, self.source[start.offset:self.i], Span(start, self.position()), value
        ))

    def scan_identifier(self) -> None:
        start = self.position()
        while _identifier_continue(self.current()):
            self.advance()
        word = self.source[start.offset:self.i]
        kind = TokenKind.KEYWORD if word in KEYWORDS else TokenKind.IDENTIFIER
        self.emit(kind, start)

    def scan_number(self) -> None:
        """Scan strict decimal numbers, diagnosing malformed contiguous candidates.

        12.foo remains INT(12), '.', IDENT(foo) because field access has
        already been accepted by the grammar; it is a semantic type error.
        In contrast, 12. and 12.3foo have malformed numeric syntax.
        """
        start = self.position()
        while _ascii_digit(self.current()):
            self.advance()
        integer_part = self.source[start.offset:self.i]
        if len(integer_part) > 1 and integer_part.startswith("0"):
            self.error("数字字面量不允许前导零", start)

        floating = False
        if self.current() == ".":
            if _ascii_digit(self.peek()):
                floating = True
                self.advance()  # decimal point
                while _ascii_digit(self.current()):
                    self.advance()
            elif not _identifier_start(self.peek()):
                # 1.foo is parsed as postfix member access; 1. is malformed.
                self.error("小数点后必须至少有一位数字", self.position())

        if self.current() in ("e", "E"):
            floating = True
            exp_start = self.position()
            self.advance()
            if self.current() in ("+", "-"):
                self.advance()
            if not _ascii_digit(self.current()):
                self.error("指数部分必须至少有一位数字", exp_start)
            while _ascii_digit(self.current()):
                self.advance()

        if _identifier_continue(self.current()):
            self.error("数字字面量后存在非法后缀", self.position())
        self.emit(TokenKind.FLOAT_LITERAL if floating else TokenKind.INT_LITERAL, start)

    def scan_string(self) -> None:
        start = self.position()
        self.advance()  # opening quote
        decoded: list[str] = []
        while True:
            c = self.current()
            if c == "":
                self.error("字符串缺少结束双引号", start)
            if c == '"':
                self.advance()
                self.emit(TokenKind.STRING_LITERAL, start, "".join(decoded))
                return
            if c in ("\n", "\r"):
                self.error("字符串不能直接跨行；请使用 \\n 转义")
            if c == "\\":
                slash = self.position()
                self.advance()
                next_char = self.current()
                if next_char == "" or next_char in ("\r", "\n"):
                    self.error("字符串中的转义序列未完成", slash)
                if next_char not in ESCAPES:
                    self.error(f"不支持的字符串转义：\\{next_char}", slash)
                decoded.append(ESCAPES[next_char])
                self.advance()
            else:
                decoded.append(c)
                self.advance()

    def skip_line_comment(self) -> None:
        self.advance()  # /
        self.advance()  # /
        while self.current() not in ("", "\r", "\n"):
            self.advance()

    def scan(self) -> list[Token]:
        # BOM is permitted only as the first Unicode code point.
        if self.source.startswith("\ufeff"):
            # BOM is ignored, not counted as source column 1.
            self.i = 1
        while self.i < len(self.source):
            c = self.current()
            if c in (" ", "\t", "\n"):
                self.advance()
                continue
            if c == "\r":
                if self.peek() != "\n":
                    self.error("孤立回车不是合法换行，请使用 LF 或 CRLF")
                self.advance()
                continue
            if c == "/" and self.peek() == "/":
                self.skip_line_comment()
                continue
            if c == "/" and self.peek() == "*":
                self.error("V1.0 不支持 /* ... */ 块注释")
            if _identifier_start(c):
                self.scan_identifier()
                continue
            if _ascii_digit(c):
                self.scan_number()
                continue
            if c == '"':
                self.scan_string()
                continue
            if c == "." and _ascii_digit(self.peek()):
                self.error("小数必须包含整数部分，例如 0.5")
            start = self.position()
            pair = c + self.peek()
            if pair in TWO_CHAR_SYMBOLS:
                self.advance()
                self.advance()
                self.emit(TokenKind.SYMBOL, start)
                continue
            if c in ONE_CHAR_SYMBOLS:
                self.advance()
                self.emit(TokenKind.SYMBOL, start)
                continue
            if c == "\ufeff":
                self.error("UTF-8 BOM 只能出现在源文件开头")
            self.error(f"非法字符 U+{ord(c):04X}")
        at = self.position()
        self.tokens.append(Token(TokenKind.EOF, "", Span(at, at)))
        return self.tokens


def tokenize(source: str, filename: str = "<source>") -> list[Token]:
    """Lex a decoded source string, returning tokens including final EOF."""
    return Lexer(source, filename).scan()


def tokenize_file(path: str | Path) -> list[Token]:
    """Strict UTF-8 decode, preserving original CRLF positions."""
    p = Path(path)
    data = p.read_bytes()
    try:
        source = data.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        prefix = data[:exc.start].decode("utf-8", errors="replace")
        prefix = prefix.removeprefix("\ufeff")
        normalized = prefix.replace("\r\n", "\n")
        line = normalized.count("\n") + 1
        col = len(normalized.rsplit("\n", 1)[-1]) + 1
        raise LexError(str(p), line, col, "源文件不是合法的 UTF-8 编码") from None
    return tokenize(source, str(p))


def main(argv: list[str] | None = None) -> int:
    argparser = argparse.ArgumentParser(description="显示 ZBY Lexer Token（不会执行程序）")
    argparser.add_argument("source", help="要进行词法分析的 .zby 文件")
    args = argparser.parse_args(argv)
    try:
        toks = tokenize_file(args.source)
    except LexError as err:
        print(err, file=sys.stderr)
        return 1
    except OSError as err:
        print(f"{args.source}: InputError: {err.strerror or err}", file=sys.stderr)
        return 1
    for tok in toks:
        extra = "" if tok.value is None else f"  value={tok.value!r}"
        print(f"{tok.line:>3}:{tok.column:<3} {tok.kind.value:<14} {tok.lexeme!r}{extra}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
