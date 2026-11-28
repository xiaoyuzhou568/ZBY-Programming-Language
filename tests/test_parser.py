"""ZBY V0.2 parser and AST tests; never execute ZBY code."""
from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest

from zby import ast_nodes as A
from zby.errors import LexError
from zby.parser import ParserOptions, parse, parse_file, main
from zby.parser_errors import ZbySyntaxError


HERE = Path(__file__).resolve().parent


def first_expr(source: str) -> A.Expr:
    program = parse('print(' + source + ');')
    statement = program.items[0]
    assert isinstance(statement, A.CallStatement)
    return statement.call.arguments[0]


class TestParserPrecedence(unittest.TestCase):
    def test_product_before_sum(self):
        expr = first_expr('2 + 3 * 4')
        self.assertIsInstance(expr, A.BinaryExpr)
        self.assertEqual(expr.operator, '+')
        self.assertEqual(expr.right.operator, '*')
        self.assertEqual(expr.left.lexeme, '2')

    def test_not_after_comparison(self):
        expr = first_expr('not a < b')
        self.assertIsInstance(expr, A.UnaryExpr)
        self.assertEqual(expr.operator, 'not')
        self.assertEqual(expr.value.operator, '<')

    def test_equality_below_relational_but_no_implicit_chain(self):
        expr = first_expr('(a < b) == true')
        self.assertIsInstance(expr, A.BinaryExpr)
        self.assertEqual(expr.operator, '==')
        self.assertIsInstance(expr.left, A.GroupExpr)
        self.assertEqual(expr.left.value.operator, '<')
        for bad in ('a < b == true', 'a == b < c', 'a < b < c', 'a == b == c', 'a > b != c'):
            with self.subTest(bad=bad), self.assertRaises(ZbySyntaxError):
                first_expr(bad)

    def test_logic_and_arithmetic_associativity(self):
        expr = first_expr('a or b and c')
        self.assertEqual(expr.operator, 'or')
        self.assertEqual(expr.right.operator, 'and')
        expr = first_expr('20 - 5 - 3')
        self.assertEqual(expr.operator, '-')
        self.assertEqual(expr.left.operator, '-')
        expr = first_expr('not not a')
        self.assertEqual(expr.operator, 'not')
        self.assertEqual(expr.value.operator, 'not')
        expr = first_expr('- -a')
        self.assertEqual(expr.operator, '-')
        self.assertEqual(expr.value.operator, '-')

    def test_postfix_chaining(self):
        expr = first_expr('get()[0].name')
        self.assertIsInstance(expr, A.FieldExpr)
        self.assertEqual(expr.field, 'name')
        self.assertIsInstance(expr.target, A.IndexExpr)
        self.assertIsInstance(expr.target.target, A.CallExpr)
        self.assertEqual(expr.target.target.callee.name, 'get')

    def test_struct_and_array_literal_ast(self):
        expr = first_expr('Student { scores: [1, 2], name: "小雨" }')
        self.assertIsInstance(expr, A.StructExpr)
        self.assertEqual([x.name for x in expr.fields], ['scores', 'name'])
        self.assertIsInstance(expr.fields[0].value, A.ArrayExpr)
        self.assertEqual(expr.fields[1].value.value, '小雨')

    def test_literals_are_raw_and_no_numeric_range_check(self):
        expr = first_expr('1e309')
        self.assertIsInstance(expr, A.FloatExpr)
        self.assertEqual(expr.lexeme, '1e309')
        expr = first_expr('-42')
        self.assertEqual(expr.operator, '-')
        self.assertEqual(expr.value.lexeme, '42')


class TestParserDeclarations(unittest.TestCase):
    def test_declarations_and_type_annotations(self):
        p = parse('let x: int[] = []; struct S {a: int; b: string[];}')
        self.assertIsInstance(p.items[0], A.VarDecl)
        self.assertEqual(p.items[0].type_ref.name, 'int')
        self.assertTrue(p.items[0].type_ref.array)
        self.assertIsInstance(p.items[0].initializer, A.ArrayExpr)
        self.assertEqual(len(p.items[0].initializer.elements), 0)
        self.assertIsInstance(p.items[1], A.StructDecl)
        self.assertEqual([x.name for x in p.items[1].fields], ['a', 'b'])

    def test_function_parameters_return_and_body(self):
        p = parse('fn sum(a: int, b: int) -> int { return a+b; }')
        fn = p.items[0]
        self.assertIsInstance(fn, A.FunctionDecl)
        self.assertEqual([x.name for x in fn.parameters], ['a', 'b'])
        self.assertEqual(fn.return_type.name, 'int')
        self.assertIsInstance(fn.body.statements[0], A.ReturnStatement)

    def test_while_for_if_block_shapes(self):
        s = '''
        if (true) { print(1); }
        else if (false) { print(2); }
        else { print(3); }
        while (true) { break; }
        for (let i: int = 0; i < 3; i = i + 1) { continue; }
        '''
        p = parse(s)
        self.assertIsInstance(p.items[0], A.IfStatement)
        self.assertEqual(len(p.items[0].branches), 2)
        self.assertIsNotNone(p.items[0].else_block)
        self.assertIsInstance(p.items[1], A.WhileStatement)
        self.assertIsInstance(p.items[2], A.ForStatement)
        self.assertEqual(p.items[2].update.target.name, 'i')

    def test_assignment_target_chains(self):
        p = parse('students[0].scores[1] = 95;')
        target = p.items[0].target
        self.assertIsInstance(target, A.IndexExpr)
        self.assertIsInstance(target.target, A.FieldExpr)
        self.assertIsInstance(target.target.target, A.IndexExpr)
        self.assertEqual(target.target.target.target.name, 'students')

    def test_nested_declaration_rejected(self):
        for s in ['if (true) { fn f() -> void {} }',
                  'while (true) { struct Person { x: int; } }',
                  'fn f() -> void { fn g() -> void {} }']:
            with self.subTest(source=s), self.assertRaises(ZbySyntaxError):
                parse(s)

    def test_type_only_void_return(self):
        for s in ['let v: void = 1;', 'fn f(x: void) -> int {return 1;}',
                  'struct S { data: void; }', 'let a: int[][] = [];']:
            with self.subTest(source=s), self.assertRaises(ZbySyntaxError):
                parse(s)

    def test_empty_blocks_and_empty_program(self):
        self.assertEqual(len(parse('').items), 0)
        self.assertEqual(len(parse('// hi').items), 0)
        self.assertEqual(len(parse('fn f() -> void {}').items[0].body.statements), 0)

    def test_call_statements_only(self):
        self.assertIsInstance(parse('print(1);').items[0], A.CallStatement)
        for s in ['2 + 3;', 'foo().bar = 3;', 'foo().bar();', 'foo(1) + 2;', 'a = b = 3;']:
            with self.subTest(source=s), self.assertRaises(ZbySyntaxError):
                parse(s)

    def test_structure_does_not_resolve_names_types(self):
        for s in ['let x: Unknown = 3;', 'let x: int = "not-int";',
                  'print([]);', 'let print: int = 2;',
                  'fn f() -> int {} f();', 'print(true + 1);']:
            with self.subTest(source=s):
                self.assertGreater(len(parse(s).items), 0)


class TestStructuralChecks(unittest.TestCase):
    def test_illegal_control_context(self):
        for s in ['return;', 'break;', 'continue;', 'fn f() -> void { break; }',
                  'fn f() -> void { continue; }', 'if (true) { return; }']:
            with self.subTest(source=s), self.assertRaises(ZbySyntaxError):
                parse(s)

    def test_legal_nested_loops_function(self):
        p = parse('fn f() -> void { while (true) { for (let i: int=0; i<2; i=i+1) { break; continue; } } }')
        self.assertEqual(len(p.items), 1)

    def test_parser_no_execution(self):
        sink = io.StringIO()
        with contextlib.redirect_stdout(sink):
            parse('print("NEVER_EXECUTED");')
        self.assertEqual(sink.getvalue(), '')

    def test_error_positions(self):
        with self.assertRaises(ZbySyntaxError) as ctx:
            parse('let x: int = 1;\r\n\tbreak;', 'main.zby')
        err = ctx.exception
        self.assertEqual((err.filename, err.line, err.column), ('main.zby', 2, 2))
        self.assertIn('SyntaxError', str(err))
        with self.assertRaises(LexError):
            parse('let x: int = 012;')

    def test_policy_is_explicit_and_reversible(self):
        source = 'struct Empty {} let e: Empty = Empty {};'
        with self.assertRaises(ZbySyntaxError) as ctx:
            parse(source)
        self.assertIn('待确认', str(ctx.exception))
        self.assertIsInstance(parse(source, options=ParserOptions(allow_empty_structs=True)).items[0], A.StructDecl)
        with self.assertRaises(ZbySyntaxError):
            parse('print(1,);')
        self.assertIsInstance(parse('print(1,);', options=ParserOptions(allow_trailing_commas=True)).items[0], A.CallStatement)
        with self.assertRaises(ZbySyntaxError):
            parse('struct S {a: int;} let x: S = S {a: 1,};')
        opts = ParserOptions(allow_trailing_commas=True)
        self.assertIsInstance(parse('struct S {a: int;} let x: S = S {a: 1,};',options=opts).items[1], A.VarDecl)

    def test_file_and_cli(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d)/'sample.zby'
            f.write_text('fn main() -> void { print("hi"); }',encoding='utf8')
            self.assertEqual(len(parse_file(f).items),1)
            sink = io.StringIO()
            with contextlib.redirect_stdout(sink):
                status = main([str(f)])
            self.assertEqual(status,0)
            self.assertIn('语法检查通过',sink.getvalue())
            bad = Path(d)/'bad.zby'
            bad.write_text('return;',encoding='utf8')
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(main([str(bad)]),1)


class TestGrammarAuditCorpus(unittest.TestCase):
    """Baseline cases from the previous independent 70-case LALR audit."""

    def test_70_prior_audit_cases(self):
        data = json.loads((HERE/'grammar_cases.json').read_text(encoding='utf8'))
        self.assertEqual(len(data), 70)
        for item in data:
            source = item['source']
            category = item['expected']
            opts = ParserOptions(allow_empty_structs=True) if item['id']=='S28' else None
            with self.subTest(case=item['id'], category=category):
                if category in ('accept','semantic-reject'):
                    p = parse(source, options=opts)
                    self.assertIsInstance(p,A.Program)
                elif category in ('reject','structural-reject'):
                    with self.assertRaises((LexError,ZbySyntaxError)):
                        parse(source)
                else:
                    self.fail('Unknown legacy case type')


if __name__ == '__main__':
    unittest.main()
