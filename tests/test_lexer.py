"""Pure-Lexer tests: independently run without implementing parser/runtime."""
from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest

from zby.errors import LexError
from zby.lexer import BUILTIN_NAMES, KEYWORDS, tokenize, tokenize_file
from zby.tokens import TokenKind


def pairs(src: str) -> list[tuple[str, str]]:
    return [(tok.kind.value, tok.lexeme) for tok in tokenize(src) if tok.kind != TokenKind.EOF]


class TestLexer(unittest.TestCase):
    def test_keywords_exactly_20(self):
        self.assertEqual(len(KEYWORDS), 20)
        self.assertEqual(len(BUILTIN_NAMES), 10)
        self.assertEqual(len(KEYWORDS & BUILTIN_NAMES), 0)

    def test_all_keywords_tokenized_as_keyword(self):
        tokens = tokenize(" ".join(sorted(KEYWORDS)))
        self.assertEqual([tok.lexeme for tok in tokens[:-1]], sorted(KEYWORDS))
        self.assertTrue(all(tok.kind == TokenKind.KEYWORD for tok in tokens[:-1]))

    def test_all_builtin_names_are_identifiers(self):
        tokens = tokenize(" ".join(sorted(BUILTIN_NAMES)))
        self.assertTrue(all(tok.kind == TokenKind.IDENTIFIER for tok in tokens[:-1]))

    def test_spans_utf8_unicode_crlf_tab_and_bom(self):
        tokens = tokenize('\ufeff// 注释\r\n\tlet name: string = "你好";\r\nprint(name);')
        self.assertEqual((tokens[0].line, tokens[0].column), (2, 2))
        string = next(tok for tok in tokens if tok.kind == TokenKind.STRING_LITERAL)
        self.assertEqual((string.line, string.column), (2, 21))
        self.assertEqual(string.value, "你好")
        p = next(tok for tok in tokens if tok.lexeme == "print")
        self.assertEqual((p.line, p.column), (3, 1))
        self.assertEqual(tokens[0].span.end.offset - tokens[0].span.start.offset, 3)

    def test_half_open_spans(self):
        t = tokenize('foo\n')[0]
        self.assertEqual((t.span.start.line, t.span.start.column), (1, 1))
        self.assertEqual((t.span.end.line, t.span.end.column), (1, 4))
        self.assertEqual(t.span.end.offset, 3)

    def test_eof_positions(self):
        self.assertEqual((tokenize("")[0].line, tokenize("")[0].column), (1, 1))
        self.assertEqual((tokenize("\n")[0].line, tokenize("\n")[0].column), (2, 1))
        self.assertEqual((tokenize("\r\n")[0].line, tokenize("\r\n")[0].column), (2, 1))
        self.assertEqual((tokenize('\ufefflet')[0].line, tokenize('\ufefflet')[0].column), (1, 1))

    def test_comment_vs_url_string(self):
        t = tokenize('let url: string = "https://zby.dev"; // trailing comment\nlet x: int = 1;')
        self.assertIn('"https://zby.dev"', [x.lexeme for x in t])
        self.assertEqual([x.lexeme for x in t].count("let"), 2)
        self.assertNotIn("trailing", [x.lexeme for x in t])

    def test_slash_and_comment(self):
        self.assertEqual(pairs("12/3// hi\n4"), [
            ("INT_LITERAL", "12"), ("SYMBOL", "/"),
            ("INT_LITERAL", "3"), ("INT_LITERAL", "4")
        ])

    def test_raw_escape_decoding_and_lexeme(self):
        t = tokenize(r'"a\n\t\r\"\\b"')[0]
        self.assertEqual(t.kind, TokenKind.STRING_LITERAL)
        self.assertEqual(t.value, 'a\n\t\r"\\b')
        self.assertEqual(t.lexeme, r'"a\n\t\r\"\\b"')

    def test_unicode_string_not_encoded_as_utf8_bytes(self):
        t = tokenize('"你好🌅"')[0]
        self.assertEqual(t.value, "你好🌅")
        self.assertEqual(t.span.end.offset - t.span.start.offset, 5)

    def test_number_is_raw_no_float_conversion(self):
        t = tokenize("1e309")[0]
        self.assertEqual(t.kind, TokenKind.FLOAT_LITERAL)
        self.assertEqual(t.lexeme, "1e309")
        self.assertIsNone(t.value)

    def test_symbol_maximal_munch(self):
        self.assertEqual(pairs("<= >= == != -> < > = -"),
                         [("SYMBOL", x) for x in "<= >= == != -> < > = -".split()])

    def test_sign_is_separate(self):
        self.assertEqual(pairs("-42 +1"), [
            ("SYMBOL", "-"), ("INT_LITERAL", "42"),
            ("SYMBOL", "+"), ("INT_LITERAL", "1")
        ])

    def test_member_after_integer_is_separate_tokens(self):
        self.assertEqual(pairs("1.age"), [
            ("INT_LITERAL", "1"), ("SYMBOL", "."), ("IDENTIFIER", "age")
        ])

    def test_decoded_file_crlf_preserved(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "crlf.zby"
            p.write_bytes(b"let x: int = 1;\r\nprint(x);\r\n")
            t = tokenize_file(p)
            pr = next(x for x in t if x.lexeme == "print")
            self.assertEqual((pr.line, pr.column), (2, 1))

    def test_invalid_utf8_file_reports_lexerror(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "bad.zby"
            p.write_bytes(b'// ok\r\nlet x: int = \xff;')
            with self.assertRaises(LexError) as caught:
                tokenize_file(p)
            self.assertEqual((caught.exception.line, caught.exception.column), (2, 14))

    def test_error_location_in_second_line(self):
        with self.assertRaises(LexError) as caught:
            tokenize('let x: int = 1;\nlet y: float = .5;', 'case.zby')
        e = caught.exception
        self.assertEqual(e.filename, 'case.zby')
        self.assertEqual((e.line, e.column), (2, 16))
        self.assertIn('LexError', str(e))


# Every generated method is a distinct unittest, making counts meaningful.
VALID_CASES = [
    ("empty_source", "", []),
    ("spaces", "  \t  \n", []),
    ("only_line_comment", "// 备注", []),
    ("comment_followed_by_lf", "//中文\nlet", [("KEYWORD", "let")]),
    ("comment_followed_by_crlf", "//中文\r\nlet", [("KEYWORD", "let")]),
    ("simple_declaration", "let x: int = 1;", None),
    ("underscore_id", "_foo9", [("IDENTIFIER", "_foo9")]),
    ("case_sensitive_ids", "x X", [("IDENTIFIER", "x"), ("IDENTIFIER", "X")]),
    ("keyword_prefix", "ifx if", [("IDENTIFIER", "ifx"), ("KEYWORD", "if")]),
    ("ascii_digits_zero", "0", [("INT_LITERAL", "0")]),
    ("ascii_digits_many", "98765432101234567890", [("INT_LITERAL", "98765432101234567890")]),
    ("integer_subtract", "10-4", None),
    ("decimal_zero", "0.0", [("FLOAT_LITERAL", "0.0")]),
    ("decimal_small", "0.000001", [("FLOAT_LITERAL", "0.000001")]),
    ("decimal_large", "100.234", [("FLOAT_LITERAL", "100.234")]),
    ("float_exponent_lower", "1e3", [("FLOAT_LITERAL", "1e3")]),
    ("float_exponent_upper", "1E3", [("FLOAT_LITERAL", "1E3")]),
    ("float_exponent_plus", "12E+10", [("FLOAT_LITERAL", "12E+10")]),
    ("float_exponent_minus", "1e-10", [("FLOAT_LITERAL", "1e-10")]),
    ("float_decimal_exponent", "1.5e-2", [("FLOAT_LITERAL", "1.5e-2")]),
    ("float_negative_sign", "-3.0", [("SYMBOL", "-"), ("FLOAT_LITERAL", "3.0")]),
    ("float_extreme_but_lexically_valid", "999e999", [("FLOAT_LITERAL", "999e999")]),
    ("float_underflow_but_lexically_valid", "1e-999", [("FLOAT_LITERAL", "1e-999")]),
    ("empty_string", '""', [("STRING_LITERAL", '""')]),
    ("single_string", '"abc"', [("STRING_LITERAL", '"abc"')]),
    ("chinese_string", '"中文"', [("STRING_LITERAL", '"中文"')]),
    ("emoji_string", '"🌅"', [("STRING_LITERAL", '"🌅"')]),
    ("url_string", '"https://example.org/a"', [("STRING_LITERAL", '"https://example.org/a"')]),
    ("double_quote_escape", r'"\""', [("STRING_LITERAL", r'"\""')]),
    ("backslash_escape", r'"\\"', [("STRING_LITERAL", r'"\\"')]),
    ("newline_escape", r'"\n"', [("STRING_LITERAL", r'"\n"')]),
    ("tab_escape", r'"\t"', [("STRING_LITERAL", r'"\t"')]),
    ("carriage_return_escape", r'"\r"', [("STRING_LITERAL", r'"\r"')]),
    ("struct_syntax_tokens", "struct S { age: int; }", None),
    ("function_arrow", "fn a() -> void {}", None),
    ("comparison_symbols", "<= >= == !=", None),
    ("postfix", "students[0].age", None),
    ("operators", "+ - * / %", None),
    ("grouping", "() [] {}", None),
    ("more_symbols", ". , : ; =", None),
    ("boolean_keywords", "true false and or not", None),
    ("builtin_as_identifiers", "print len to_float int_div", None),
    ("no_bom_needed", "let", [("KEYWORD", "let")]),
    ("bom_allowed_first", "\ufefflet", [("KEYWORD", "let")]),
    ("newline_lf", "x\ny", None),
    ("newline_crlf", "x\r\ny", None),
]

INVALID_CASES = [
    ("leading_zero_00", "00", "前导零"),
    ("leading_zero_01", "01", "前导零"),
    ("leading_zero_012", "012", "前导零"),
    ("leading_zero_decimal", "00.5", "前导零"),
    ("leading_zero_exponent", "01e3", "前导零"),
    ("leading_dot", ".5", "整数部分"),
    ("trailing_dot", "1.", "小数点后"),
    ("empty_decimal_before_semicolon", "1.;", "小数点后"),
    ("double_dot", "1..2", "小数点后"),
    ("missing_exponent", "1e", "指数部分"),
    ("missing_exponent_plus", "1e+", "指数部分"),
    ("missing_exponent_minus", "1e-", "指数部分"),
    ("exponent_non_digit", "1ea", "指数部分"),
    ("exponent_dot", "1e.5", "指数部分"),
    ("float_exponent_incomplete", "3.5E", "指数部分"),
    ("num_identifier_suffix", "123abc", "非法后缀"),
    ("int_hex", "0xFF", "非法后缀"),
    ("int_binary", "0b101", "非法后缀"),
    ("numeric_underscore", "1_000", "非法后缀"),
    ("number_e_suffix", "1e2abc", "非法后缀"),
    ("float_alpha_suffix", "1.2a", "非法后缀"),
    ("invalid_escape", r'"bad\q"', "不支持的字符串转义"),
    ("invalid_escape_zero", r'"bad\0"', "不支持的字符串转义"),
    ("invalid_escape_unicode", r'"bad\u4e2d"', "不支持的字符串转义"),
    ("unterminated_string", '"unterminated', "缺少结束双引号"),
    ("unfinished_slash", '"abc' + '\\', "转义序列未完成"),
    ("raw_newline_in_string", '"a\nb"', "不能直接跨行"),
    ("raw_crlf_in_string", '"a\r\nb"', "不能直接跨行"),
    ("slash_newline_in_string", '"a' + '\\' + '\n' + 'b"', "转义序列未完成"),
    ("unsupported_single_quote", "'a'", "非法字符"),
    ("unsupported_unicode_id", "学生", "非法字符"),
    ("unsupported_emoji_identifier", "🌅", "非法字符"),
    ("unsupported_at", "@", "非法字符"),
    ("unsupported_hash", "#", "非法字符"),
    ("unsupported_ampersand", "&", "非法字符"),
    ("unsupported_pipe", "|", "非法字符"),
    ("unsupported_question", "?", "非法字符"),
    ("unsupported_bang", "!", "非法字符"),
    ("unsupported_lonely_cr", "x\ry", "孤立回车"),
    ("unsupported_bom_midfile", "let\ufeffx", "只能出现在"),
    ("unsupported_block_comment", "/* nope */", "不支持"),
    ("other_unicode_digit", "１２", "非法字符"),
]


def _add_valid(case_id: str, src: str, expected: list[tuple[str, str]] | None) -> None:
    def check(self: TestLexer) -> None:
        actual = pairs(src)
        if expected is not None:
            self.assertEqual(actual, expected)
        self.assertEqual(tokenize(src)[-1].kind, TokenKind.EOF)
    setattr(TestLexer, f"test_valid_{case_id}", check)


def _add_invalid(case_id: str, src: str, message: str) -> None:
    def check(self: TestLexer) -> None:
        with self.assertRaises(LexError) as ctx:
            tokenize(src)
        self.assertIn(message, ctx.exception.reason)
    setattr(TestLexer, f"test_invalid_{case_id}", check)


for _id, _src, _expected in VALID_CASES:
    _add_valid(_id, _src, _expected)
for _id, _src, _message in INVALID_CASES:
    _add_invalid(_id, _src, _message)
