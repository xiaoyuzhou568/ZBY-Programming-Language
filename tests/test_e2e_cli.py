"""Run the real Python CLI as a separate process and check byte-level outputs."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent
ENV = dict(os.environ, PYTHONPATH=str(ROOT / "src"), PYTHONIOENCODING="utf-8")


class InterpreterBlackBoxTests(unittest.TestCase):
    def execute(self, source: str | bytes, *, input_bytes: bytes = b"", extra_args: tuple[str, ...] = ()) -> subprocess.CompletedProcess[bytes]:
        with tempfile.TemporaryDirectory(prefix="zby-e2e-") as tmp:
            path = Path(tmp) / "main.zby"
            path.write_bytes(source.encode("utf-8") if isinstance(source, str) else source)
            return self.invoke(path, input_bytes=input_bytes, extra_args=extra_args)

    def invoke(self, path: Path, *, input_bytes: bytes = b"", extra_args: tuple[str, ...] = ()) -> subprocess.CompletedProcess[bytes]:
        return subprocess.run(
            [sys.executable, "-m", "zby.interpreter", *extra_args, str(path)],
            cwd=ROOT, env=ENV, input=input_bytes,
            capture_output=True, timeout=10, check=False,
        )

    def test_run_scorebook_end_to_end(self):
        result = self.invoke(ROOT / "examples/scorebook.zby")
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8"))
        self.assertEqual(result.stdout, ("学生: 小林\n当前成绩: [80, 90, 100]\n备份成绩: [80, 90]\n平均分: 90.0\n").encode("utf-8"))
        self.assertEqual(result.stderr, b"")

    def test_input_prompt_and_utf8_output(self):
        result = self.invoke(ROOT / "examples/input_demo.zby", input_bytes="阿明\n".encode("utf-8"))
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "姓名: 你好, 阿明\n".encode("utf-8"))
        self.assertEqual(result.stderr, b"")

    def test_empty_input_eof_is_error(self):
        result = self.execute('print("before");\ninput();', input_bytes=b"")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, b"before\n")
        self.assertIn(b"InputError", result.stderr)
        self.assertNotIn(b"Traceback", result.stderr)

    def test_empty_input_line_not_eof(self):
        result = self.execute('let s: string = input(); print("<" + s + ">");', input_bytes=b"\n")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, b"<>\n")

    def test_source_late_syntax_error_prevents_any_execution(self):
        result = self.execute('print("earlier");\nlet x: int = ;')
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, b"")
        self.assertIn(b"SyntaxError", result.stderr)
        self.assertIn(b"2 | let x", result.stderr)

    def test_source_late_lexical_error_prevents_execution(self):
        result = self.execute('print("earlier");\n@')
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, b"")
        self.assertIn(b"LexError", result.stderr)
        self.assertIn(b"2 | @", result.stderr)

    def test_control_flow_violation_prevents_execution(self):
        result = self.execute('print("earlier");\nbreak;')
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, b"")
        self.assertIn(b"SyntaxError", result.stderr)

    def test_type_mismatch_has_source_and_types(self):
        result = self.execute('let age: int = "twenty";')
        s = result.stderr.decode("utf-8")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, b"")
        self.assertIn('1 | let age: int = "twenty";', s)
        self.assertIn('^^^^^^^', s)
        self.assertIn("期望类型：int", s)
        self.assertIn("实际类型：string", s)

    def test_non_ascii_before_error_column_remains_codepoint_based(self):
        src = 'print("你好"); let x: int = "bad";'
        result = self.execute(src)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "你好\n".encode("utf-8"))
        s = result.stderr.decode("utf-8")
        col = len('print("你好"); let x: int = ') + 1
        self.assertIn(f":1:{col}: TypeError", s)
        self.assertIn("期望类型：int", s)

    def test_utf8_bom_does_not_displace_error_caret(self):
        result = self.execute("\ufefflet x: int = \"bad\";")
        self.assertEqual(result.returncode, 1)
        self.assertIn(":1:14: TypeError", result.stderr.decode("utf-8"))
        self.assertIn('1 | let x: int = "bad";', result.stderr.decode("utf-8"))

    def test_tabs_are_expanded_for_caret(self):
        result = self.execute('\tlet x: int = "bad";')
        s = result.stderr.decode("utf-8")
        self.assertEqual(result.returncode, 1)
        self.assertIn('1 |     let x: int = "bad";', s)
        self.assertIn('TypeError', s)
        self.assertNotIn('Traceback', s)

    def test_crlf_line_numbers(self):
        result = self.execute('print("first");\r\nlet x: int = "bad";\r\n')
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, b"first\n")
        self.assertIn(":2:14: TypeError", result.stderr.decode("utf-8"))

    def test_error_preserves_prior_stdout_does_not_print_later(self):
        src = 'print("before");\nlet a: int[] = [1];\na[2] = 9;\nprint("after");'
        result = self.execute(src)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, b"before\n")
        self.assertIn(b"IndexError", result.stderr)
        self.assertNotIn(b"after", result.stdout)

    def test_function_call_stack_has_frames(self):
        src = '''fn divide(a: int, b: int) -> float { return a / b; }
fn calc() -> float { return divide(10, 0); }
print(calc());'''
        result = self.execute(src)
        self.assertEqual(result.returncode, 1)
        s = result.stderr.decode("utf-8")
        self.assertIn("ZeroDivisionError", s)
        self.assertIn("调用路径", s)
        self.assertIn("calc() @ 3:", s)
        self.assertIn("divide() @ 2:", s)
        self.assertNotIn("Traceback", s)

    def test_recursive_call_limit_reports_resource_error(self):
        src = 'fn loop() -> void { loop(); }\nloop();'
        result = self.execute(src, extra_args=("--max-call-depth", "4"))
        self.assertEqual(result.returncode, 1)
        self.assertIn(b"ResourceError", result.stderr)
        self.assertIn(b"loop()", result.stderr)
        self.assertNotIn(b"RecursionError", result.stderr)

    def test_invalid_call_limit_returns_one(self):
        result = self.execute('print("hi");', extra_args=("--max-call-depth", "0"))
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, b"")
        self.assertIn("正整数".encode("utf-8"), result.stderr)

    def test_missing_file_returns_one_with_input_error(self):
        path = ROOT / "examples/__no_such_file__.zby"
        result = self.invoke(path)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, b"")
        self.assertIn(b"InputError", result.stderr)
        self.assertNotIn(b"Traceback", result.stderr)

    def test_invalid_utf8_file_returns_lex_error(self):
        result = self.execute(b'print("ok");\n\xff')
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, b"")
        self.assertIn(b"LexError", result.stderr)
        self.assertNotIn(b"Traceback", result.stderr)

    def test_short_circuit_prevents_division_error(self):
        src = 'print(false and (1 / 0 > 0.0));\nprint(true or (1 / 0 > 0.0));'
        result = self.execute(src)
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8"))
        self.assertEqual(result.stdout, b"false\ntrue\n")

    def test_argument_arity_check_prevents_side_effect(self):
        src = '''fn side() -> int { print("side"); return 3; }
fn f(x: int) -> void { print(x); }
f(side(), side());'''
        result = self.execute(src)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, b"")
        self.assertIn(b"ArgumentError", result.stderr)

    def test_argument_early_type_check_prevents_second_effect(self):
        src = '''fn side() -> int { print("side"); return 3; }
fn f(x: int, y: int) -> void { print(x); }
f("wrong", side());'''
        result = self.execute(src)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, b"")
        self.assertIn(b"TypeError", result.stderr)

    def test_int_div_and_negative_remainder(self):
        result = self.execute('print(int_div(-7, 3), -7 % 3);')
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, b"-3 2\n")

    def test_float_overflow_reports_zby_error(self):
        result = self.execute('let a: float = 1e309;')
        self.assertEqual(result.returncode, 1)
        self.assertIn(b"ValueError", result.stderr)
        self.assertIn(b"1 | let a", result.stderr)
        self.assertNotIn(b"Traceback", result.stderr)

    def test_prints_nested_structure_unicode(self):
        src = '''struct Name { text: string; }
let n: Name = Name { text: "你好" };
print(n);'''
        result = self.execute(src)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, 'Name { text: "你好" }\n'.encode("utf-8"))

    def test_clone_preserves_original(self):
        src = 'let a: int[] = [1]; let b: int[] = clone(a); push(b, 2); print(a, b);'
        result = self.execute(src)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, b"[1] [1, 2]\n")

    def test_compilation_error_does_not_print_prior_output(self):
        result = self.execute('print("should-not");\nlet x: int = 01;')
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, b"")
        self.assertIn(b"LexError", result.stderr)

    def test_print_still_uses_lf(self):
        result = self.execute('print("a");\nprint("b");')
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, b"a\nb\n")
        self.assertEqual(result.stderr, b"")

    def test_known_pending_empty_struct_is_rejected_by_default(self):
        result = self.execute('struct Empty {}')
        self.assertEqual(result.returncode, 1)
        self.assertIn(b"SyntaxError", result.stderr)

    def test_known_pending_trailing_comma_is_rejected_by_default(self):
        result = self.execute('let a: int[] = [1, 2,];')
        self.assertEqual(result.returncode, 1)
        self.assertIn(b"SyntaxError", result.stderr)


if __name__ == "__main__":
    unittest.main()
