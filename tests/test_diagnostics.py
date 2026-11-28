"""Source-position rendering: no ZBY interpreter required."""
from __future__ import annotations

import unittest

from zby.diagnostics import _display_parts, render_diagnostic
from zby.errors import LexError
from zby.parser_errors import ZbySyntaxError
from zby.interpreter import ZbyRuntimeError


class DiagnosticRenderingTests(unittest.TestCase):
    def test_lexical_error_line(self):
        e = LexError("main.zby", 1, 1, "非法字符")
        s = render_diagnostic(e, "@")
        self.assertIn("main.zby:1:1: LexError: 非法字符", s)
        self.assertIn("  1 | @", s)
        self.assertIn("    | ^", s)

    def test_syntax_error_line(self):
        e = ZbySyntaxError("main.zby", 1, 6, "缺少分号")
        s = render_diagnostic(e, "hello")
        self.assertIn("main.zby:1:6: SyntaxError", s)
        self.assertIn("    |      ^", s)

    def test_runtime_range_carets(self):
        e = ZbyRuntimeError("x.zby", 1, 16, "TypeError", "类型不匹配", (), 1, 23)
        s = render_diagnostic(e, 'let age: int = "wrong";')
        self.assertIn('^^^^^^^', s)

    def test_expected_actual_fields(self):
        e = ZbyRuntimeError("x.zby", 1, 1, "TypeError", "bad", expected_type="int", actual_type="string")
        s = render_diagnostic(e, "x")
        self.assertIn("期望类型：int", s)
        self.assertIn("实际类型：string", s)

    def test_no_source_still_has_diagnostic(self):
        e = LexError("x", 5, 8, "bad")
        self.assertEqual(render_diagnostic(e, None), "x:5:8: LexError: bad")

    def test_outside_source_still_has_diagnostic(self):
        e = LexError("x", 50, 1, "bad")
        self.assertNotIn(" | ", render_diagnostic(e, "x\n"))

    def test_chinese_width_offsets(self):
        value, offsets = _display_parts("你好a")
        self.assertEqual(value, "你好a")
        self.assertEqual(offsets, [0, 2, 4, 5])

    def test_tab_expansion_offsets(self):
        value, offsets = _display_parts("a\t中")
        self.assertEqual(value, "a   中")
        self.assertEqual(offsets, [0, 1, 4, 6])

    def test_combining_accent_column_is_codepoint(self):
        _, offsets = _display_parts("e\u0301x")
        self.assertEqual(offsets, [0, 1, 1, 2])

    def test_source_unicode_line_separators_not_a_newline(self):
        e = LexError("x", 1, 2, "bad")
        s = render_diagnostic(e, "A\u2028B")
        self.assertIn("1 | A\u2028B", s)

    def test_render_crlf(self):
        e = LexError("x", 2, 1, "bad")
        s = render_diagnostic(e, "line\r\nnext")
        self.assertIn("2 | next", s)
        self.assertNotIn("next\r", s)

    def test_all_stack_entries_when_short(self):
        calls = ("a() @ 1:1", "b() @ 2:3")
        e = ZbyRuntimeError("x", 3, 4, "ValueError", "bad", calls)
        s = render_diagnostic(e, "x\nx\nx")
        self.assertIn("a() @ 1:1", s)
        self.assertIn("b() @ 2:3", s)
        self.assertNotIn("省略", s)

    def test_stack_truncation_count(self):
        calls = tuple(f"f{i}() @ {i}:1" for i in range(30))
        e = ZbyRuntimeError("x", 1, 1, "ResourceError", "bad", calls)
        s = render_diagnostic(e, "x", max_stack_frames=10)
        self.assertIn("省略 20 层调用", s)
        self.assertIn("f0()", s)
        self.assertIn("f29()", s)
        self.assertNotIn("f10()", s)

    def test_invalid_stack_setting(self):
        with self.assertRaises(ValueError):
            render_diagnostic(LexError("x", 1, 1, "bad"), "x", max_stack_frames=0)

    def test_control_char_is_safe_in_excerpt(self):
        display, _ = _display_parts("a\x00b")
        self.assertEqual(display, "a\\u0000b")


if __name__ == "__main__":
    unittest.main()
