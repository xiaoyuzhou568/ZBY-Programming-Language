"""Execution-stage tests for the primitive-focused ZBY runtime prototype."""
from __future__ import annotations

import contextlib
import io
from pathlib import Path
import tempfile
import unittest

from zby.interpreter import PrototypeLimitError, ZbyRuntimeError, run, main
from zby.errors import LexError
from zby.parser_errors import ZbySyntaxError


def execute(source: str, stdin: str = "") -> str:
    output = io.StringIO()
    run(source, stdout=output, stdin=io.StringIO(stdin))
    return output.getvalue()


class RuntimeHappyPath(unittest.TestCase):
    def test_integer_precedence(self):
        self.assertEqual(execute('let answer: int = 2 + 3 * 4; print(answer);'), '14\n')

    def test_float_and_int_division(self):
        self.assertEqual(execute('print(7 / 2, int_div(-7, 3), -7 % 3);'), '3.5 -3 2\n')

    def test_int_is_arbitrary_precision(self):
        self.assertEqual(execute('print(999999999999999999999999 + 1);'), '1000000000000000000000000\n')

    def test_very_large_int_beyond_host_decimal_limit(self):
        digits = '9' * 5000
        self.assertEqual(execute(f'print({digits} + 1);'), '1' + '0' * 5000 + '\n')
        self.assertEqual(execute(f'print(to_int("{digits}"));'), digits + '\n')

    def test_huge_division_finite_quotient(self):
        huge = '1' + '0'*400
        self.assertEqual(execute(f'print({huge} / {huge});'), '1.0\n')

    def test_signed_float_zero(self):
        self.assertEqual(execute('print(-0.0, -0.0 == 0.0);'), '-0.0 true\n')

    def test_typed_reassignment(self):
        self.assertEqual(execute('let i: int = 1; i = i + 2; print(i);'), '3\n')

    def test_strings_and_utf8(self):
        self.assertEqual(execute('print("你好" + " 世界", len("你🙂"), "中国"[1]);'), '你好 世界 2 国\n')

    def test_print_no_args_and_multiple(self):
        self.assertEqual(execute('print(); print("X", true, 1.0);'), '\nX true 1.0\n')

    def test_not_and_comparison_precedence(self):
        self.assertEqual(execute('print(not 3 < 5, not (3 > 5));'), 'false true\n')

    def test_bool_short_circuit_skips_zero_division(self):
        self.assertEqual(execute('print(false and (1 / 0 > 0.0), true or (1 / 0 > 0.0));'), 'false true\n')

    def test_bool_skipped_wrong_rhs(self):
        self.assertEqual(execute('print(false and 123, true or "broken");'), 'false true\n')

    def test_signed_modulo(self):
        self.assertEqual(execute('print(-7 % 3, 7 % -3, -7 % -3);'), '2 -2 -1\n')

    def test_numeric_conversions(self):
        self.assertEqual(execute('print(to_int("+42"), to_int(-3.9), to_float("3"), to_float("-0"));'),
                         '42 -3 3.0 -0.0\n')

    def test_to_string_and_bool(self):
        self.assertEqual(execute('print(to_string(true), to_string(1.0), to_string(44));'), 'true 1.0 44\n')

    def test_input_prompt_and_empty(self):
        self.assertEqual(execute('let s: string = input("Name: "); print(s);', '小雨  \r\n'), 'Name: 小雨  \n')
        self.assertEqual(execute('print(input());', '\n'), '\n')

    def test_whitespace_string_length(self):
        self.assertEqual(execute('print(len("é"));'), '2\n')

    def test_if_elif_else(self):
        self.assertEqual(execute('let x: int = 4; if (x > 5) { print(1); } else if (x < 5) { print(2); } else { print(3); }'), '2\n')

    def test_local_scope_and_shadow(self):
        self.assertEqual(execute('let x: int = 1; if (true) { let x: int = 5; print(x); } print(x);'), '5\n1\n')

    def test_for_loop_continue_updates(self):
        src = '''for(let i: int = 0; i < 5; i = i + 1) {
            if (i == 1) { continue; }
            if (i == 4) { break; }
            print(i);
        }'''
        self.assertEqual(execute(src), '0\n2\n3\n')

    def test_while_loop(self):
        self.assertEqual(execute('let i: int=0; while (i<3) { print(i); i=i+1; }'), '0\n1\n2\n')

    def test_void_function_and_return(self):
        src='fn hello() -> void { print("Hi"); return; } hello();'
        self.assertEqual(execute(src), 'Hi\n')

    def test_user_function_and_call(self):
        src='fn add(a: int,b: int)->int { return a+b; } print(add(3,4));'
        self.assertEqual(execute(src), '7\n')

    def test_fn_params_shadow_global(self):
        src='let n:int=10; fn plus(n:int)->int { return n+1; } print(plus(3), n);'
        self.assertEqual(execute(src), '4 10\n')

    def test_function_updates_global(self):
        src='let n:int=1; fn step()->void { n=n+1; } step(); print(n);'
        self.assertEqual(execute(src), '2\n')

    def test_mutual_recursion_registered_before_calls(self):
        src='''fn even(n:int)->bool { if(n==0){return true;} return odd(n-1); }
        fn odd(n:int)->bool { if(n==0){return false;} return even(n-1); }
        print(even(4), odd(5));'''
        self.assertEqual(execute(src), 'true true\n')


class RuntimeErrors(unittest.TestCase):
    def expect_error(self, code: str, category: str, detail: str | None = None):
        with self.assertRaises(ZbyRuntimeError) as cm:
            execute(code)
        self.assertEqual(cm.exception.category, category)
        if detail:
            self.assertIn(detail, str(cm.exception))
        return cm.exception

    def test_type_mismatch(self):
        self.expect_error('let i: int = "hello";', 'TypeError', '期望 int')

    def test_bool_not_int(self):
        self.expect_error('print(true + 1);', 'TypeError')
        self.expect_error('print(1 == 1.0);', 'TypeError')

    def test_float_overflow_literal(self):
        self.expect_error('print(1e309);', 'ValueError', 'binary64')

    def test_float_overflow_arithmetic(self):
        self.expect_error('print(1e308 * 10.0);', 'ValueError')

    def test_int_div_overflow(self):
        huge = '1' + '0'*400
        self.expect_error(f'print({huge} / 1);', 'ValueError')

    def test_div_zero(self):
        self.expect_error('print(1 / 0);', 'ZeroDivisionError')
        self.expect_error('print(int_div(1,0));', 'ZeroDivisionError')

    def test_wrong_conversion(self):
        for code in ('to_int("042");', 'to_int(" 42 ");', 'to_float("nan");'):
            with self.subTest(code=code):
                self.expect_error('print(' + code[:-1] + ');', 'ValueError')

    def test_not_int(self):
        self.expect_error('print(not 1);', 'TypeError')

    def test_undefined_name(self):
        self.expect_error('print(missing);', 'NameError')

    def test_duplicate_variable(self):
        self.expect_error('let a:int=1; let a:int=2;', 'NameError')

    def test_builtin_name_collision(self):
        self.expect_error('let print: int = 3;', 'NameError')

    def test_func_arity_precedes_argument_effect(self):
        out = io.StringIO()
        with self.assertRaises(ZbyRuntimeError) as cm:
            run('fn add(x:int)->int {return x;} add(print("never"), 2);', stdout=out)
        self.assertEqual(out.getvalue(), '')
        self.assertEqual(cm.exception.category, 'ArgumentError')

    def test_func_type_precedes_later_argument_effect(self):
        out = io.StringIO()
        with self.assertRaises(ZbyRuntimeError) as cm:
            run('fn f(x:int,y:int)->void {} f("wrong", print("never"));', stdout=out)
        self.assertEqual(cm.exception.category, 'TypeError')
        self.assertEqual(out.getvalue(), '')

    def test_short_circuit_evaluated_rhs_type_error(self):
        self.expect_error('print(true and 123);', 'TypeError')

    def test_non_bool_condition(self):
        self.expect_error('if(1){print(2);}', 'TypeError')

    def test_missing_return(self):
        self.expect_error('fn f()->int {} print(f());', 'ValueError', '没有 return')

    def test_wrong_return_type(self):
        self.expect_error('fn f()->int{return "x";} f();', 'TypeError')

    def test_void_return_has_value(self):
        self.expect_error('fn f()->void{return 1;} f();', 'TypeError')

    def test_invalid_input_eof(self):
        self.expect_error('print(input());', 'InputError')

    def test_wrong_index(self):
        self.expect_error('print("ab"[-1]);', 'IndexError')
        self.expect_error('print("ab"[2]);', 'IndexError')
        self.expect_error('print("ab"[1.0]);', 'TypeError')

    def test_unregistered_function_at_call_time(self):
        self.expect_error('first(); fn first()->void{}', 'NameError')

    def test_call_stack(self):
        err = self.expect_error('fn f()->int{return 1 / 0;} print(f());', 'ZeroDivisionError')
        self.assertIn('f()', str(err))

    def test_depth_limit(self):
        with self.assertRaises(ZbyRuntimeError) as cm:
            run('fn go()->void{ go(); } go();', max_call_depth=4, stdout=io.StringIO())
        self.assertEqual(cm.exception.category, 'ResourceError')

    def test_early_parser_error_no_output(self):
        out = io.StringIO()
        with self.assertRaises(ZbySyntaxError):
            run('print("before"); break;', stdout=out)
        self.assertEqual(out.getvalue(), '')

    def test_early_lexer_error_no_output(self):
        out = io.StringIO()
        with self.assertRaises(LexError):
            run('print("before"); let x:int=012;', stdout=out)
        self.assertEqual(out.getvalue(), '')

    def test_error_after_print_does_not_rollback(self):
        out=io.StringIO()
        with self.assertRaises(ZbyRuntimeError):
            run('print("before"); print(1/0); print("after");', stdout=out)
        self.assertEqual(out.getvalue(), 'before\n')

    def test_cli_success_and_error(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'demo.zby'
            p.write_text('print("Hello", 42);', encoding='utf-8')
            out=io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(main([str(p)]),0)
            self.assertEqual(out.getvalue(),'Hello 42\n')
            p.write_text('print(1 / 0);',encoding='utf-8')
            err=io.StringIO()
            with contextlib.redirect_stderr(err):
                self.assertEqual(main([str(p)]),1)
            self.assertIn('ZeroDivisionError',err.getvalue())

    def test_composites_implemented_in_stage_two(self):
        self.assertEqual(execute('let a:int[]=[1,2]; print(a);'), "[1, 2]\n")
        self.assertEqual(execute('struct S{x:int;} let a:S=S{x:3}; print(a);'), "S { x: 3 }\n")


if __name__ == '__main__':
    unittest.main()
