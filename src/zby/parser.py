"""Hand-written recursive-descent ZBY V0.2 Parser and AST builder.

Implements accepted syntax and structural-context rules. It does not execute,
resolve names/types, or decide still-open syntax policy (see ParserOptions).
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import json
from pathlib import Path
import sys

from . import ast_nodes as A
from .errors import LexError
from .lexer import tokenize, tokenize_file
from .parser_errors import ZbySyntaxError
from .tokens import Span, Token, TokenKind


BASE_TYPES = frozenset({"int", "float", "string", "bool"})
REL_OPS = frozenset({"<", "<=", ">", ">="})
EQ_OPS = frozenset({"==", "!="})
COMPARISON_OPS = REL_OPS | EQ_OPS


@dataclass(frozen=True, slots=True)
class ParserOptions:
    """Explicit interim policies; these syntax choices are not yet ratified.

    Defaults conservatively reject undecided empty struct declarations and
    trailing commas. Flags enable research only, not a language specification.
    """
    allow_empty_structs: bool = False
    allow_trailing_commas: bool = False


class Parser:
    def __init__(self, tokens: list[Token], filename: str = "<source>",
                 options: ParserOptions | None = None) -> None:
        if not tokens or tokens[-1].kind != TokenKind.EOF:
            raise ValueError("Parser requires the Lexer token stream ending in EOF")
        self.tokens = tokens
        self.filename = filename
        self.options = options or ParserOptions()
        self.i = 0
        self.function_depth = 0
        self.loop_depth = 0

    def current(self) -> Token:
        return self.tokens[self.i]

    def peek(self, ahead: int = 1) -> Token:
        return self.tokens[min(self.i + ahead, len(self.tokens) - 1)]

    def advance(self) -> Token:
        tok = self.current()
        if tok.kind != TokenKind.EOF:
            self.i += 1
        return tok

    def at(self, lexeme: str) -> bool:
        return self.current().lexeme == lexeme and self.current().kind != TokenKind.EOF

    def accept(self, lexeme: str) -> Token | None:
        return self.advance() if self.at(lexeme) else None

    def error(self, reason: str, tok: Token | None = None) -> None:
        target = self.current() if tok is None else tok
        raise ZbySyntaxError(self.filename, target.line, target.column, reason)

    def expect(self, lexeme: str) -> Token:
        if not self.at(lexeme):
            self.error(f"期望 {lexeme!r}，实际为 {self.describe(self.current())}")
        return self.advance()

    def expect_identifier(self) -> Token:
        if self.current().kind != TokenKind.IDENTIFIER:
            self.error(f"期望标识符，实际为 {self.describe(self.current())}")
        return self.advance()

    @staticmethod
    def describe(tok: Token) -> str:
        return "文件结束" if tok.kind == TokenKind.EOF else repr(tok.lexeme)

    @staticmethod
    def span(begin: Token | object, end: Token | object) -> Span:
        return Span(begin.span.start, end.span.end)  # type: ignore[attr-defined]

    def parse_program(self) -> A.Program:
        items: list[A.TopLevel] = []
        first = self.current()
        while self.current().kind != TokenKind.EOF:
            if self.at("fn"):
                items.append(self.parse_function())
            elif self.at("struct"):
                items.append(self.parse_struct())
            else:
                items.append(self.parse_statement())
        return A.Program(tuple(items), self.span(first, self.current()))

    def parse_type(self, allow_void: bool = False) -> A.TypeRef:
        tok = self.current()
        if allow_void and self.at("void"):
            self.advance()
            return A.TypeRef("void", False, tok.span)
        if tok.lexeme in BASE_TYPES and tok.kind == TokenKind.KEYWORD:
            self.advance()
        elif tok.kind == TokenKind.IDENTIFIER:
            self.advance()
        else:
            self.error("期望有效类型名（int、float、string、bool 或结构体名称）")
        is_array = False
        end: Token = tok
        if self.accept("["):
            end = self.expect("]")
            is_array = True
            if self.at("["):
                self.error("V1.0 只支持一维数组类型，不支持 T[][]")
        return A.TypeRef(tok.lexeme, is_array, self.span(tok, end))

    def parse_struct(self) -> A.StructDecl:
        start = self.expect("struct")
        name = self.expect_identifier()
        self.expect("{")
        fields: list[A.StructFieldDecl] = []
        while not self.at("}"):
            if self.current().kind == TokenKind.EOF:
                self.error("结构体定义缺少结束右花括号")
            field = self.expect_identifier()
            self.expect(":")
            typ = self.parse_type()
            last = self.expect(";")
            fields.append(A.StructFieldDecl(field.lexeme, typ, self.span(field, last)))
        end = self.expect("}")
        if not fields and not self.options.allow_empty_structs:
            self.error("空结构体是否允许仍待确认；当前保守解析模式暂不接受", name)
        return A.StructDecl(name.lexeme, tuple(fields), self.span(start, end))

    def parse_function(self) -> A.FunctionDecl:
        start = self.expect("fn")
        name = self.expect_identifier()
        self.expect("(")
        parameters: list[A.Parameter] = []
        if not self.at(")"):
            while True:
                param = self.expect_identifier()
                self.expect(":")
                typ = self.parse_type()
                parameters.append(A.Parameter(param.lexeme, typ, self.span(param, typ)))
                if not self.accept(","):
                    break
                if self.at(")") and not self.options.allow_trailing_commas:
                    self.error("参数列表尾随逗号的规则尚待确认")
                if self.at(")"):
                    break
        self.expect(")")
        self.expect("->")
        result = self.parse_type(allow_void=True)
        saved_loop = self.loop_depth
        self.function_depth += 1
        self.loop_depth = 0  # loop control cannot cross function boundaries
        try:
            body = self.parse_block()
        finally:
            self.function_depth -= 1
            self.loop_depth = saved_loop
        return A.FunctionDecl(name.lexeme, tuple(parameters), result, body, self.span(start, body))

    def parse_block(self) -> A.Block:
        start = self.expect("{")
        statements: list[A.Stmt] = []
        while not self.at("}"):
            if self.current().kind == TokenKind.EOF:
                self.error("代码块缺少结束右花括号")
            if self.at("fn") or self.at("struct"):
                self.error("fn 和 struct 只能在源文件顶层声明")
            statements.append(self.parse_statement())
        return A.Block(tuple(statements), self.span(start, self.expect("}")))

    def parse_statement(self) -> A.Stmt:
        if self.at("let"):
            item = self.parse_var_decl()
            end = self.expect(";")
            return A.VarDecl(item.name, item.type_ref, item.initializer,
                             self.span(item, end))
        if self.at("return"):
            start = self.advance()
            if self.function_depth == 0:
                self.error("return 只能在函数内部出现", start)
            expr = None if self.at(";") else self.parse_expression()
            return A.ReturnStatement(expr, self.span(start, self.expect(";")))
        if self.at("break") or self.at("continue"):
            start = self.advance()
            if self.loop_depth == 0:
                self.error(f"{start.lexeme} 只能在当前函数内部的循环中使用", start)
            end = self.expect(";")
            if start.lexeme == "break":
                return A.BreakStatement(self.span(start, end))
            return A.ContinueStatement(self.span(start, end))
        if self.at("if"):
            return self.parse_if()
        if self.at("while"):
            return self.parse_while()
        if self.at("for"):
            return self.parse_for()
        if self.current().kind == TokenKind.IDENTIFIER:
            if self.peek().lexeme == "(":
                name = self.advance()
                start_expr = A.NameExpr(name.lexeme, name.span)
                call = self.parse_call(start_expr)
                end = self.expect(";")
                return A.CallStatement(call, self.span(name, end))
            item = self.parse_assignment()
            end = self.expect(";")
            return A.Assignment(item.target, item.value, self.span(item, end))
        self.error(f"无效的语句起始 Token：{self.describe(self.current())}")

    def parse_var_decl(self) -> A.VarDecl:
        start = self.expect("let")
        name = self.expect_identifier()
        self.expect(":")
        typ = self.parse_type()
        self.expect("=")
        expr = self.parse_expression()
        return A.VarDecl(name.lexeme, typ, expr, self.span(start, expr))

    def parse_assignment(self) -> A.Assignment:
        name = self.expect_identifier()
        target: A.NameExpr | A.FieldExpr | A.IndexExpr = A.NameExpr(name.lexeme, name.span)
        while True:
            if self.accept("."):
                member = self.expect_identifier()
                target = A.FieldExpr(target, member.lexeme, self.span(target, member))
            elif self.accept("["):
                index = self.parse_expression()
                close = self.expect("]")
                target = A.IndexExpr(target, index, self.span(target, close))
            else:
                break
        self.expect("=")
        expr = self.parse_expression()
        return A.Assignment(target, expr, self.span(name, expr))

    def parse_if(self) -> A.IfStatement:
        start = self.expect("if")
        branches: list[tuple[A.Expr, A.Block]] = []
        self.expect("(")
        condition = self.parse_expression()
        self.expect(")")
        block = self.parse_block()
        branches.append((condition, block))
        while self.at("else") and self.peek().lexeme == "if":
            self.advance()
            self.advance()
            self.expect("(")
            condition = self.parse_expression()
            self.expect(")")
            block = self.parse_block()
            branches.append((condition, block))
        else_block = self.parse_block() if self.accept("else") else None
        return A.IfStatement(tuple(branches), else_block,
                             self.span(start, else_block or block))

    def parse_loop_body(self) -> A.Block:
        self.loop_depth += 1
        try:
            return self.parse_block()
        finally:
            self.loop_depth -= 1

    def parse_while(self) -> A.WhileStatement:
        start = self.expect("while")
        self.expect("(")
        condition = self.parse_expression()
        self.expect(")")
        body = self.parse_loop_body()
        return A.WhileStatement(condition, body, self.span(start, body))

    def parse_for(self) -> A.ForStatement:
        start = self.expect("for")
        self.expect("(")
        init = self.parse_var_decl()
        self.expect(";")
        condition = self.parse_expression()
        self.expect(";")
        update = self.parse_assignment()
        self.expect(")")
        body = self.parse_loop_body()
        return A.ForStatement(init, condition, update, body, self.span(start, body))

    def parse_expression(self) -> A.Expr:
        return self.parse_or()

    def parse_or(self) -> A.Expr:
        left = self.parse_and()
        while self.at("or"):
            op = self.advance()
            right = self.parse_and()
            left = A.BinaryExpr(op.lexeme, left, right, self.span(left, right))
        return left

    def parse_and(self) -> A.Expr:
        left = self.parse_not()
        while self.at("and"):
            op = self.advance()
            right = self.parse_not()
            left = A.BinaryExpr(op.lexeme, left, right, self.span(left, right))
        return left

    def parse_not(self) -> A.Expr:
        if self.at("not"):
            tok = self.advance()
            operand = self.parse_not()
            return A.UnaryExpr(tok.lexeme, operand, self.span(tok, operand))
        return self.parse_equality()

    def parse_equality(self) -> A.Expr:
        left = self.parse_relation()
        if self.current().lexeme in EQ_OPS:
            op = self.advance()
            if isinstance(left, A.BinaryExpr) and left.operator in REL_OPS:
                self.error("比较与相等运算不能无括号连续使用", op)
            right = self.parse_relation()
            if isinstance(right, A.BinaryExpr) and right.operator in REL_OPS:
                self.error("比较与相等运算不能无括号连续使用", op)
            left = A.BinaryExpr(op.lexeme, left, right, self.span(left, right))
            if self.current().lexeme in COMPARISON_OPS:
                self.error("禁止无括号连续比较，请使用括号或逻辑运算")
        return left

    def parse_relation(self) -> A.Expr:
        left = self.parse_add()
        if self.current().lexeme in REL_OPS:
            op = self.advance()
            right = self.parse_add()
            left = A.BinaryExpr(op.lexeme, left, right, self.span(left, right))
            if self.current().lexeme in REL_OPS:
                self.error("禁止无括号连续关系比较")
        return left

    def parse_add(self) -> A.Expr:
        left = self.parse_multiply()
        while self.current().lexeme in ("+", "-"):
            op = self.advance()
            right = self.parse_multiply()
            left = A.BinaryExpr(op.lexeme, left, right, self.span(left, right))
        return left

    def parse_multiply(self) -> A.Expr:
        left = self.parse_unary()
        while self.current().lexeme in ("*", "/", "%"):
            op = self.advance()
            right = self.parse_unary()
            left = A.BinaryExpr(op.lexeme, left, right, self.span(left, right))
        return left

    def parse_unary(self) -> A.Expr:
        if self.at("-"):
            start = self.advance()
            value = self.parse_unary()
            return A.UnaryExpr(start.lexeme, value, self.span(start, value))
        return self.parse_postfix()

    def parse_postfix(self) -> A.Expr:
        left = self.parse_primary()
        while True:
            if self.at("("):
                left = self.parse_call(left)
            elif self.accept("["):
                index = self.parse_expression()
                end = self.expect("]")
                left = A.IndexExpr(left, index, self.span(left, end))
            elif self.accept("."):
                name = self.expect_identifier()
                left = A.FieldExpr(left, name.lexeme, self.span(left, name))
            else:
                break
        return left

    def parse_call(self, callee: A.Expr) -> A.CallExpr:
        self.expect("(")
        args = self.parse_comma_expressions(")", "实参")
        end = self.expect(")")
        return A.CallExpr(callee, tuple(args), self.span(callee, end))

    def parse_comma_expressions(self, close: str, context: str) -> list[A.Expr]:
        values: list[A.Expr] = []
        if self.at(close):
            return values
        while True:
            values.append(self.parse_expression())
            if not self.accept(","):
                break
            if self.at(close):
                if not self.options.allow_trailing_commas:
                    self.error(f"{context}列表尾随逗号的规则尚待确认")
                break
        return values

    def parse_primary(self) -> A.Expr:
        tok = self.current()
        if tok.kind == TokenKind.INT_LITERAL:
            self.advance()
            return A.IntegerExpr(tok.lexeme, tok.span)
        if tok.kind == TokenKind.FLOAT_LITERAL:
            self.advance()
            return A.FloatExpr(tok.lexeme, tok.span)
        if tok.kind == TokenKind.STRING_LITERAL:
            self.advance()
            return A.StringExpr(tok.value or "", tok.span)
        if self.at("true") or self.at("false"):
            self.advance()
            return A.BoolExpr(tok.lexeme == "true", tok.span)
        if tok.kind == TokenKind.IDENTIFIER:
            self.advance()
            if self.at("{"):
                return self.parse_struct_literal(tok)
            return A.NameExpr(tok.lexeme, tok.span)
        if self.at("["):
            start = self.advance()
            values = self.parse_comma_expressions("]", "数组元素")
            end = self.expect("]")
            return A.ArrayExpr(tuple(values), self.span(start, end))
        if self.at("("):
            start = self.advance()
            value = self.parse_expression()
            end = self.expect(")")
            return A.GroupExpr(value, self.span(start, end))
        self.error(f"此处期望表达式，实际为 {self.describe(tok)}")

    def parse_struct_literal(self, name: Token) -> A.StructExpr:
        self.expect("{")
        fields: list[A.NamedTypeInit] = []
        if not self.at("}"):
            while True:
                field = self.expect_identifier()
                self.expect(":")
                value = self.parse_expression()
                fields.append(A.NamedTypeInit(field.lexeme, value, self.span(field, value)))
                if not self.accept(","):
                    break
                if self.at("}"):
                    if not self.options.allow_trailing_commas:
                        self.error("结构体初始化字段尾随逗号的规则尚待确认")
                    break
        end = self.expect("}")
        return A.StructExpr(name.lexeme, tuple(fields), self.span(name, end))


def parse(source: str, filename: str = "<source>",
          options: ParserOptions | None = None) -> A.Program:
    return Parser(tokenize(source, filename), filename, options).parse_program()


def parse_file(path: str | Path, options: ParserOptions | None = None) -> A.Program:
    return Parser(tokenize_file(path), str(path), options).parse_program()


def main(argv: list[str] | None = None) -> int:
    argparser = argparse.ArgumentParser(description="将 ZBY 源码解析为 AST（不执行程序）")
    argparser.add_argument("source", help=".zby 源文件")
    argparser.add_argument("--show-ast", action="store_true", help="输出完整 AST JSON")
    argparser.add_argument("--experimental-empty-structs", action="store_true",
                           help="实验性解析空结构体；不表示语法规则已获认可")
    argparser.add_argument("--experimental-trailing-commas", action="store_true",
                           help="实验性解析尾随逗号；不表示语法规则已获认可")
    args = argparser.parse_args(argv)
    options = ParserOptions(args.experimental_empty_structs, args.experimental_trailing_commas)
    try:
        ast = parse_file(args.source, options)
    except (LexError, ZbySyntaxError) as exc:
        print(exc, file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"{args.source}: InputError: {exc.strerror or exc}", file=sys.stderr)
        return 1
    if args.show_ast:
        print(json.dumps(asdict(ast), ensure_ascii=False, indent=2))
    else:
        print(f"语法检查通过：{len(ast.items)} 个顶层节点（未执行程序）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
