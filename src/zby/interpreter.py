"""ZBY V0.2 interpreter prototype, stage 3: source diagnostics and black-box validation.

Builds on the original hand-written Lexer, Parser and AST. No host-language
traceback or implicit conversion is part of ZBY semantics.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from fractions import Fraction
import math
from pathlib import Path
import re
import sys
from typing import TextIO

from . import ast_nodes as A
from .errors import LexError
from .diagnostics import render_diagnostic, source_for_diagnostic
from .lexer import BUILTIN_NAMES
from .parser import parse, parse_file
from .parser_errors import ZbySyntaxError
from .tokens import Span


INT_PATTERN = re.compile(r"[+-]?(?:0|[1-9][0-9]*)\Z", re.ASCII)
FLOAT_PATTERN = re.compile(
    r"[+-]?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?\Z",
    re.ASCII,
)


@dataclass(eq=False)
class ZbyRuntimeError(Exception):
    filename: str
    line: int
    column: int
    category: str
    detail: str
    calls: tuple[str, ...] = ()
    end_line: int | None = None
    end_column: int | None = None
    expected_type: str | None = None
    actual_type: str | None = None

    def __str__(self) -> str:
        s = f"{self.filename}:{self.line}:{self.column}: {self.category}: {self.detail}"
        if self.calls:
            s += "\nZBY 调用路径：\n" + "\n".join("  " + c for c in self.calls)
        return s


@dataclass(eq=False)
class PrototypeLimitError(ZbyRuntimeError):
    """An implementation-stage gap, never a ratified ZBY language restriction."""


@dataclass(slots=True)
class Binding:
    type_name: str
    value: object


@dataclass(slots=True)
class Scope:
    parent: Scope | None = None
    values: dict[str, Binding] = field(default_factory=dict)

    def find(self, name: str) -> Scope | None:
        scope: Scope | None = self
        while scope is not None:
            if name in scope.values:
                return scope
            scope = scope.parent
        return None


@dataclass(eq=False, slots=True)
class ZbyArray:
    element_type: str
    elements: list[object]


@dataclass(eq=False, slots=True)
class ZbyStruct:
    type_name: str
    fields: dict[str, object]


@dataclass(slots=True)
class TargetLocation:
    """Already-resolved LHS: never re-evaluate base object or index after RHS."""
    owner: object
    key: str | int
    type_name: str
    kind: str


@dataclass(eq=False)
class ReturnFlow(Exception):
    value: object
    has_value: bool
    span: Span


class BreakFlow(Exception):
    pass


class ContinueFlow(Exception):
    pass


VOID = object()


def parse_decimal_integer(text: str) -> int:
    """Avoid Python 3.11+ int(string) decimal-digit safety cutoff for valid ZBY int."""
    sign = -1 if text.startswith("-") else 1
    digits = text.lstrip("+-")
    number = 0
    for offset in range(0, len(digits), 9):
        part = digits[offset:offset + 9]
        number = number * (10 ** len(part)) + int(part)
    return sign * number


def format_decimal_integer(value: int) -> str:
    """Format arbitrarily large ZBY integers without Python str(int) cutoff."""
    if value == 0:
        return "0"
    prefix = "-" if value < 0 else ""
    number = abs(value)
    pieces: list[int] = []
    while number:
        number, remainder = divmod(number, 1_000_000_000)
        pieces.append(remainder)
    return prefix + str(pieces[-1]) + "".join(f"{p:09d}" for p in reversed(pieces[:-1]))


def value_type(value: object) -> str:
    if value is VOID:
        return "void"
    if type(value) is bool:
        return "bool"
    if type(value) is int:
        return "int"
    if type(value) is float:
        return "float"
    if type(value) is str:
        return "string"
    if isinstance(value, ZbyArray):
        return value.element_type + "[]"
    if isinstance(value, ZbyStruct):
        return value.type_name
    return "<unsupported>"


def escaped_string(value: str) -> str:
    replacements = {'\\': '\\\\', '"': '\\"', '\n': '\\n',
                    '\r': '\\r', '\t': '\\t'}
    return '"' + ''.join(replacements.get(ch, ch) for ch in value) + '"'


def printable(value: object, *, nested: bool = False,
              active: set[int] | None = None) -> str:
    """ZBY-readable output; guard against accidental cyclic host-created graphs."""
    typ = value_type(value)
    if typ == "bool":
        return "true" if value else "false"
    if typ == "float":
        return repr(value)
    if typ == "int":
        return format_decimal_integer(value)
    if typ == "string":
        return escaped_string(value) if nested else value
    if isinstance(value, (ZbyArray, ZbyStruct)):
        active = active if active is not None else set()
        if id(value) in active:
            return "<circular>"  # Defensive: valid nonrecursive ZBY types cannot form cycles.
        active.add(id(value))
        try:
            if isinstance(value, ZbyArray):
                return "[" + ", ".join(printable(v, nested=True, active=active)
                                        for v in value.elements) + "]"
            fields = ", ".join(f"{k}: {printable(v, nested=True, active=active)}"
                                for k, v in value.fields.items())
            return value.type_name + " { " + fields + " }"
        finally:
            active.remove(id(value))
    raise ValueError("void 及未知类型不能打印")


class Interpreter:
    def __init__(self, *, filename: str = "<source>",
                 stdout: TextIO | None = None, stdin: TextIO | None = None,
                 max_call_depth: int = 128) -> None:
        if max_call_depth < 1:
            raise ValueError("max_call_depth must be positive")
        self.filename = filename
        self.stdout = stdout if stdout is not None else sys.stdout
        self.stdin = stdin if stdin is not None else sys.stdin
        self.global_scope = Scope()
        self.scope = self.global_scope
        self.functions: dict[str, A.FunctionDecl] = {}
        self.structs: dict[str, dict[str, str]] = {}
        self.return_types: list[str] = []
        self.call_stack: list[str] = []
        self.max_call_depth = max_call_depth

    def fail(self, category: str, message: str, node: object) -> None:
        p = node.span.start  # type: ignore[attr-defined]
        span = node.span  # type: ignore[attr-defined]
        raise ZbyRuntimeError(self.filename, p.line, p.column, category, message,
                              tuple(self.call_stack), span.end.line, span.end.column)

    def unsupported(self, feature: str, node: object) -> None:
        p = node.span.start  # type: ignore[attr-defined]
        raise PrototypeLimitError(
            self.filename, p.line, p.column, "PrototypeLimitError",
            f"当前执行原型尚未实现{feature}；并非 ZBY 语言禁止该功能",
            tuple(self.call_stack),
        )

    def exact_type(self, value: object, expected: str, node: object) -> None:
        actual = value_type(value)
        if actual != expected:
            span = node.span  # type: ignore[attr-defined]
            raise ZbyRuntimeError(
                self.filename, span.start.line, span.start.column, "TypeError",
                f"期望 {expected}，实际 {actual}；不允许隐式转换",
                tuple(self.call_stack), span.end.line, span.end.column,
                expected_type=expected, actual_type=actual,
            )

    def check_type_ref(self, typ: A.TypeRef, allow_void: bool = False) -> str:
        primitive = {"int", "float", "string", "bool"}
        if typ.name == "void":
            if typ.array or not allow_void:
                self.fail("TypeError", "void 只能用作函数返回类型", typ)
            return "void"
        if typ.name not in primitive and typ.name not in self.structs:
            self.fail("NameError", f"未知或尚未注册的类型 {typ.name!r}", typ)
        return typ.name + ("[]" if typ.array else "")

    def declare(self, name: str, type_name: str, value: object, node: object, *, value_node: object | None = None) -> None:
        if name in BUILTIN_NAMES or name in self.functions or name in self.structs:
            self.fail("NameError", f"名称 {name!r} 与内置函数、类型或已注册函数冲突", node)
        if name in self.scope.values:
            self.fail("NameError", f"当前作用域已声明名称 {name!r}", node)
        self.exact_type(value, type_name, value_node if value_node is not None else node)
        self.scope.values[name] = Binding(type_name, value)

    def register_struct(self, node: A.StructDecl) -> None:
        if node.name in BUILTIN_NAMES or node.name in self.functions or node.name in self.structs or node.name in self.global_scope.values:
            self.fail("NameError", f"结构体类型名 {node.name!r} 已占用", node)
        fields: dict[str, str] = {}
        for field in node.fields:
            if field.name in fields:
                self.fail("NameError", f"结构体字段 {field.name!r} 重复", field)
            fields[field.name] = self.check_type_ref(field.type_ref)
        # Register only after checking: direct/indirect recursive type dependencies
        # are excluded by the required pre-declaration of every field type.
        self.structs[node.name] = fields

    def lookup(self, name: str, node: object) -> Binding:
        owner = self.scope.find(name)
        if owner is None:
            self.fail("NameError", f"未定义变量 {name!r}", node)
        return owner.values[name]

    def evaluate(self, expr: A.Expr, expected_type: str | None = None) -> object:
        if isinstance(expr, A.IntegerExpr):
            return parse_decimal_integer(expr.lexeme)
        if isinstance(expr, A.FloatExpr):
            try:
                v = float(expr.lexeme)
            except (OverflowError, ValueError):
                self.fail("ValueError", "浮点数值超出有效范围", expr)
            if not math.isfinite(v):
                self.fail("ValueError", "浮点数值超出有限 binary64 范围", expr)
            return v
        if isinstance(expr, A.StringExpr):
            return expr.value
        if isinstance(expr, A.BoolExpr):
            return expr.value
        if isinstance(expr, A.NameExpr):
            return self.lookup(expr.name, expr).value
        if isinstance(expr, A.GroupExpr):
            return self.evaluate(expr.value, expected_type)
        if isinstance(expr, A.UnaryExpr):
            v = self.evaluate(expr.value)
            if expr.operator == "not":
                self.exact_type(v, "bool", expr.value)
                return not v
            if value_type(v) not in ("int", "float"):
                self.fail("TypeError", "一元负号只接受 int 或 float", expr)
            return -v
        if isinstance(expr, A.BinaryExpr):
            return self.eval_binary(expr)
        if isinstance(expr, A.CallExpr):
            return self.eval_call(expr)
        if isinstance(expr, A.ArrayExpr):
            if expected_type is not None and expected_type.endswith("[]"):
                element_type = expected_type[:-2]
            else:
                element_type = None
            elements: list[object] = []
            for element in expr.elements:
                value = self.evaluate(element, element_type)
                if element_type is None:
                    element_type = value_type(value)
                    if element_type.endswith("[]") or element_type == "void":
                        self.fail("TypeError", "数组元素必须为基本类型或结构体，不支持嵌套数组或 void", element)
                self.exact_type(value, element_type, element)
                elements.append(value)
            if element_type is None:
                self.fail("TypeError", "空数组 [] 必须有明确的数组类型上下文", expr)
            return ZbyArray(element_type, elements)
        if isinstance(expr, A.StructExpr):
            if expr.type_name not in self.structs:
                self.fail("NameError", f"结构体类型 {expr.type_name!r} 尚未注册", expr)
            spec = self.structs[expr.type_name]
            names = [field.name for field in expr.fields]
            if len(names) != len(set(names)):
                self.fail("ValueError", "结构体初始化字段重复", expr)
            if set(names) != set(spec):
                missing = sorted(set(spec) - set(names))
                unknown = sorted(set(names) - set(spec))
                self.fail("ValueError", f"结构体字段不完整或非法；缺少 {missing}，未知 {unknown}", expr)
            values: dict[str, object] = {}
            # Source-order evaluation; declaration-order storage/printing.
            for field in expr.fields:
                val = self.evaluate(field.value, spec[field.name])
                self.exact_type(val, spec[field.name], field.value)
                values[field.name] = val
            return ZbyStruct(expr.type_name, {name: values[name] for name in spec})
        if isinstance(expr, A.IndexExpr):
            target = self.evaluate(expr.target)
            index = self.evaluate(expr.index)
            self.exact_type(index, "int", expr.index)
            if index < 0:
                self.fail("IndexError", "下标不能为负数", expr.index)
            if type(target) is str:
                if index >= len(target):
                    self.fail("IndexError", "字符串下标越界", expr)
                return target[index]
            if isinstance(target, ZbyArray):
                if index >= len(target.elements):
                    self.fail("IndexError", "数组下标越界", expr)
                return target.elements[index]
            self.fail("TypeError", f"类型 {value_type(target)} 不支持下标读取", expr)
        if isinstance(expr, A.FieldExpr):
            target = self.evaluate(expr.target)
            if not isinstance(target, ZbyStruct):
                self.fail("TypeError", f"类型 {value_type(target)} 不支持字段访问", expr)
            if expr.field not in target.fields:
                self.fail("NameError", f"结构体 {target.type_name} 不存在字段 {expr.field!r}", expr)
            return target.fields[expr.field]
        self.unsupported("未知表达式类型", expr)

    def eval_binary(self, expr: A.BinaryExpr) -> object:
        op = expr.operator
        left = self.evaluate(expr.left)
        if op in ("and", "or"):
            self.exact_type(left, "bool", expr.left)
            if op == "and" and left is False:
                return False
            if op == "or" and left is True:
                return True
            right = self.evaluate(expr.right)
            self.exact_type(right, "bool", expr.right)
            return right
        right = self.evaluate(expr.right)
        lt, rt = value_type(left), value_type(right)
        if lt != rt:
            self.fail("TypeError", f"{op} 不支持 {lt} 与 {rt} 混合运算", expr)
        if op in {"==", "!="}:
            if lt not in {"int", "float", "string", "bool"}:
                self.fail("TypeError", f"{lt} 类型不能直接比较相等", expr)
            equal = left == right
            return equal if op == "==" else not equal
        if op in {"<", "<=", ">", ">="}:
            if lt not in {"int", "float"}:
                self.fail("TypeError", "大小比较仅支持同类型数值", expr)
            return {"<":lambda: left < right, "<=":lambda: left <= right,
                    ">":lambda: left > right, ">=":lambda: left >= right}[op]()
        if op == "+" and lt == "string":
            return left + right
        if lt not in {"int", "float"}:
            self.fail("TypeError", f"运算符 {op} 不支持 {lt}", expr)
        if op == "%" and lt != "int":
            self.fail("TypeError", "取余运算只支持 int", expr)
        if op not in {"+", "-", "*", "/", "%"}:
            self.fail("TypeError", f"不支持运算符 {op}", expr)
        if op in {"/", "%"} and right == 0:
            self.fail("ZeroDivisionError", "除数不能为零", expr)
        try:
            value = (
                left + right if op == "+" else
                left - right if op == "-" else
                left * right if op == "*" else
                # DD-072: arbitrary-sized int operands must be divided as
                # an exact rational before rounding the quotient to binary64.
                # Converting the operands separately would overflow for e.g.
                # 10**400 / 10**400, even though the quotient is 1.0.
                float(Fraction(left, right)) if op == "/" and lt == "int" else
                left / right if op == "/" else
                left % right
            )
        except (OverflowError, ZeroDivisionError):
            self.fail("ValueError", "数值运算超出有限 binary64 范围", expr)
        if type(value) is float and not math.isfinite(value):
            self.fail("ValueError", "数值运算产生非有限 float", expr)
        return value

    def eval_call(self, expr: A.CallExpr) -> object:
        if not isinstance(expr.callee, A.NameExpr):
            self.unsupported("非直接名称调用", expr.callee)
        name = expr.callee.name
        if name in BUILTIN_NAMES:
            return self.call_builtin(name, expr)
        if name in self.functions:
            return self.call_function(self.functions[name], expr)
        if self.scope.find(name) is not None:
            self.fail("TypeError", f"变量 {name!r} 不是可调用函数", expr)
        self.fail("NameError", f"函数 {name!r} 在调用时尚未注册", expr)

    def checked_args(self, expr: A.CallExpr, expected: list[str] | None) -> list[object]:
        # DD-065: arity before *any* side effect; type-check each argument before
        # evaluation of the next argument, for both user and built-in functions.
        if expected is not None and len(expr.arguments) != len(expected):
            self.fail("ArgumentError", f"期望 {len(expected)} 个参数，实际 {len(expr.arguments)} 个", expr)
        out: list[object] = []
        for i, arg in enumerate(expr.arguments):
            value = self.evaluate(arg, expected[i] if expected is not None else None)
            if expected is not None:
                self.exact_type(value, expected[i], arg)
            out.append(value)
        return out

    def call_builtin(self, name: str, expr: A.CallExpr) -> object:
        if name == "print":
            args: list[object] = []
            for arg in expr.arguments:
                val = self.evaluate(arg)
                if val is VOID:
                    self.fail("TypeError", "void 返回值不能用于 print 参数", arg)
                args.append(val)
            try:
                rendered = " ".join(printable(v) for v in args)
            except (MemoryError, RecursionError):
                self.fail("ResourceError", "格式化复合值时资源不足", expr)
            self.stdout.write(rendered + "\n")
            return VOID
        if name == "input":
            if len(expr.arguments) > 1:
                self.fail("ArgumentError", "input 仅允许0或1个参数", expr)
            if expr.arguments:
                value = self.checked_args(expr, ["string"])[0]
                self.stdout.write(value)
                self.stdout.flush()
            line = self.stdin.readline()
            if line == "":
                self.fail("InputError", "输入流已结束（EOF）", expr)
            return line[:-2] if line.endswith("\r\n") else line[:-1] if line.endswith("\n") else line
        if name == "len":
            if len(expr.arguments) != 1:
                self.fail("ArgumentError", "len 需要1个参数", expr)
            value = self.evaluate(expr.arguments[0])
            if type(value) is str:
                return len(value)
            if isinstance(value, ZbyArray):
                return len(value.elements)
            self.fail("TypeError", "len 仅接受 string 或数组", expr.arguments[0])
        if name == "push":
            if len(expr.arguments) != 2:
                self.fail("ArgumentError", "push 需要2个参数", expr)
            arr = self.evaluate(expr.arguments[0])
            if not isinstance(arr, ZbyArray):
                self.fail("TypeError", "push 第一个参数必须是数组", expr.arguments[0])
            value = self.evaluate(expr.arguments[1], arr.element_type)
            self.exact_type(value, arr.element_type, expr.arguments[1])
            arr.elements.append(value)
            return VOID
        if name == "pop":
            if len(expr.arguments) != 1:
                self.fail("ArgumentError", "pop 需要1个参数", expr)
            arr = self.evaluate(expr.arguments[0])
            if not isinstance(arr, ZbyArray):
                self.fail("TypeError", "pop 参数必须是数组", expr.arguments[0])
            if not arr.elements:
                self.fail("ValueError", "不能从空数组中 pop 元素", expr)
            return arr.elements.pop()
        if name == "clone":
            if len(expr.arguments) != 1:
                self.fail("ArgumentError", "clone 需要1个参数", expr)
            value = self.evaluate(expr.arguments[0])
            if not isinstance(value, (ZbyArray, ZbyStruct)):
                self.fail("TypeError", "clone 仅支持数组与结构体", expr.arguments[0])
            try:
                return self.deep_clone(value, {})
            except (MemoryError, RecursionError):
                self.fail("ResourceError", "深拷贝时资源不足", expr)
        if name == "int_div":
            a, b = self.checked_args(expr, ["int", "int"])
            if b == 0:
                self.fail("ZeroDivisionError", "除数不能为零", expr)
            return a // b
        if name in {"to_int", "to_float", "to_string"}:
            if len(expr.arguments) != 1:
                self.fail("ArgumentError", f"{name} 需要1个参数", expr)
            v = self.evaluate(expr.arguments[0])
            return self.convert(name, v, expr)
        self.unsupported(f"内置函数 {name}", expr)

    def deep_clone(self, value: object, memo: dict[int, object]) -> object:
        if isinstance(value, (ZbyArray, ZbyStruct)):
            ref = id(value)
            if ref in memo:
                return memo[ref]
            if isinstance(value, ZbyArray):
                copy_arr = ZbyArray(value.element_type, [])
                memo[ref] = copy_arr
                copy_arr.elements.extend(self.deep_clone(v, memo) for v in value.elements)
                return copy_arr
            copy_struct = ZbyStruct(value.type_name, {})
            memo[ref] = copy_struct
            copy_struct.fields.update({k: self.deep_clone(v, memo) for k, v in value.fields.items()})
            return copy_struct
        return value  # immutable basic values are safely shared

    def convert(self, name: str, v: object, node: object) -> object:
        typ = value_type(v)
        if name == "to_string":
            if typ in {"int", "float", "string", "bool"}:
                return printable(v)
            self.fail("TypeError", "to_string 不接受复合类型", node)
        if typ not in {"int", "float", "string"}:
            self.fail("TypeError", f"{name} 不接受 {typ}", node)
        if name == "to_int":
            if typ == "string":
                if not INT_PATTERN.fullmatch(v):
                    self.fail("ValueError", "整数文本格式非法", node)
                return parse_decimal_integer(v)
            return int(v)
        if typ == "string":
            if not FLOAT_PATTERN.fullmatch(v):
                self.fail("ValueError", "浮点文本格式非法", node)
        try:
            result = float(v)
        except (ValueError, OverflowError):
            self.fail("ValueError", "数值转换超出有限 binary64 范围", node)
        if not math.isfinite(result):
            self.fail("ValueError", "数值转换超出有限 binary64 范围", node)
        return result

    def register_function(self, node: A.FunctionDecl) -> None:
        if node.name in BUILTIN_NAMES or node.name in self.functions or node.name in self.structs or node.name in self.global_scope.values:
            self.fail("NameError", f"函数名 {node.name!r} 已占用", node)
        seen: set[str] = set()
        for p in node.parameters:
            self.check_type_ref(p.type_ref)
            if p.name in seen or p.name in BUILTIN_NAMES or p.name in self.functions or p.name in self.structs or p.name == node.name:
                self.fail("NameError", f"无效或重复形参名 {p.name!r}", p)
            seen.add(p.name)
        self.check_type_ref(node.return_type, allow_void=True)
        self.functions[node.name] = node

    def call_function(self, func: A.FunctionDecl, call: A.CallExpr) -> object:
        specs = [self.check_type_ref(p.type_ref) for p in func.parameters]
        args = self.checked_args(call, specs)
        if len(self.call_stack) >= self.max_call_depth:
            self.fail("ResourceError", "函数调用深度超过当前原型上限", call)
        caller = self.scope
        self.scope = Scope(self.global_scope)
        self.call_stack.append(f"{func.name}() @ {call.span.start.line}:{call.span.start.column}")
        return_type = self.check_type_ref(func.return_type, allow_void=True)
        self.return_types.append(return_type)
        try:
            for p, v, spec in zip(func.parameters, args, specs):
                self.declare(p.name, spec, v, p)
            try:
                self.execute_block(func.body, create_scope=False)
            except ReturnFlow as result:
                if return_type == "void":
                    if result.has_value:
                        self.fail("TypeError", "void 函数不能返回具体值", result)
                    return VOID
                if not result.has_value:
                    self.fail("TypeError", "非 void 函数必须返回值", result)
                self.exact_type(result.value, return_type, result)
                return result.value
            if return_type != "void":
                self.fail("ValueError", "非 void 函数执行到末尾却没有 return", func)
            return VOID
        finally:
            self.return_types.pop()
            self.call_stack.pop()
            self.scope = caller

    def locate_target(self, expr: A.NameExpr | A.IndexExpr | A.FieldExpr) -> TargetLocation:
        if isinstance(expr, A.NameExpr):
            binding = self.lookup(expr.name, expr)
            return TargetLocation(binding, "value", binding.type_name, "variable")
        if isinstance(expr, A.IndexExpr):
            target = self.evaluate(expr.target)
            index = self.evaluate(expr.index)
            self.exact_type(index, "int", expr.index)
            if type(target) is str:
                self.fail("TypeError", "字符串不可变，不能对字符位置赋值", expr)
            if not isinstance(target, ZbyArray):
                self.fail("TypeError", "下标赋值目标必须是数组", expr)
            if index < 0 or index >= len(target.elements):
                self.fail("IndexError", "数组赋值下标越界", expr)
            return TargetLocation(target, index, target.element_type, "array")
        if isinstance(expr, A.FieldExpr):
            target = self.evaluate(expr.target)
            if not isinstance(target, ZbyStruct):
                self.fail("TypeError", "字段赋值目标必须是结构体", expr)
            if expr.field not in target.fields:
                self.fail("NameError", f"结构体 {target.type_name} 不存在字段 {expr.field!r}", expr)
            return TargetLocation(target, expr.field, self.structs[target.type_name][expr.field], "struct")
        self.unsupported("未知赋值目标", expr)

    def assign(self, node: A.Assignment) -> None:
        location = self.locate_target(node.target)  # entire left side before RHS
        value = self.evaluate(node.value, location.type_name)
        self.exact_type(value, location.type_name, node.value)
        if location.kind == "array":
            assert isinstance(location.owner, ZbyArray)
            assert isinstance(location.key, int)
            if location.key >= len(location.owner.elements):
                self.fail("IndexError", "右侧求值后数组下标已失效", node)
            location.owner.elements[location.key] = value
        elif location.kind == "struct":
            assert isinstance(location.owner, ZbyStruct)
            assert isinstance(location.key, str)
            location.owner.fields[location.key] = value
        else:
            assert isinstance(location.owner, Binding)
            location.owner.value = value

    def execute_block(self, block: A.Block, *, create_scope: bool = True) -> None:
        original = self.scope
        if create_scope:
            self.scope = Scope(original)
        try:
            for stmt in block.statements:
                self.execute_statement(stmt)
        finally:
            self.scope = original

    def condition(self, node: A.Expr) -> bool:
        value = self.evaluate(node)
        self.exact_type(value, "bool", node)
        return value

    def execute_statement(self, node: A.Stmt) -> None:
        if isinstance(node, A.VarDecl):
            typ = self.check_type_ref(node.type_ref)
            value = self.evaluate(node.initializer, typ)
            self.declare(node.name, typ, value, node, value_node=node.initializer)
        elif isinstance(node, A.Assignment):
            self.assign(node)
        elif isinstance(node, A.CallStatement):
            self.eval_call(node.call)
        elif isinstance(node, A.ReturnStatement):
            value = self.evaluate(node.value, self.return_types[-1] if self.return_types else None) if node.value is not None else VOID
            raise ReturnFlow(value, node.value is not None, node.span)
        elif isinstance(node, A.BreakStatement):
            raise BreakFlow()
        elif isinstance(node, A.ContinueStatement):
            raise ContinueFlow()
        elif isinstance(node, A.IfStatement):
            for cond, body in node.branches:
                if self.condition(cond):
                    self.execute_block(body)
                    return
            if node.else_block is not None:
                self.execute_block(node.else_block)
        elif isinstance(node, A.WhileStatement):
            while self.condition(node.condition):
                try:
                    self.execute_block(node.body)
                except ContinueFlow:
                    continue
                except BreakFlow:
                    break
        elif isinstance(node, A.ForStatement):
            original = self.scope
            self.scope = Scope(original)
            try:
                self.execute_statement(node.initializer)
                while self.condition(node.condition):
                    try:
                        self.execute_block(node.body)
                    except ContinueFlow:
                        pass
                    except BreakFlow:
                        break
                    self.assign(node.update)
            finally:
                self.scope = original
        else:
            self.unsupported("语句", node)

    def run(self, program: A.Program) -> None:
        for item in program.items:
            if isinstance(item, A.StructDecl):
                self.register_struct(item)
            elif isinstance(item, A.FunctionDecl):
                self.register_function(item)
            else:
                self.execute_statement(item)


def run(source: str, filename: str = "<source>", *,
        stdout: TextIO | None = None, stdin: TextIO | None = None,
        max_call_depth: int = 128) -> Interpreter:
    # Parsing of the entire program happens before ANY statements are executed.
    program = parse(source, filename)
    vm = Interpreter(filename=filename, stdout=stdout, stdin=stdin,
                     max_call_depth=max_call_depth)
    vm.run(program)
    return vm


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ZBY V0.2 解释器（开发版本）")
    parser.add_argument("source", type=Path, help="待执行 .zby 源文件")
    parser.add_argument("--max-call-depth", type=int, default=128,
                        help="原型函数调用深度上限，默认128（非正式规范默认值）")
    args = parser.parse_args(argv)
    # Ensure ZBY's CLI text streams use UTF-8 and LF even on Windows.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", newline="\n")
            except (OSError, ValueError):
                pass
    if hasattr(sys.stdin, "reconfigure"):
        try:
            sys.stdin.reconfigure(encoding="utf-8", newline=None)
        except (OSError, ValueError):
            pass
    if args.max_call_depth < 1:
        print("命令行参数错误：--max-call-depth 必须是正整数", file=sys.stderr)
        return 1
    try:
        # Use the same decode behavior as the Lexer file entry point.
        program = parse_file(args.source)
        vm = Interpreter(filename=str(args.source),
                         max_call_depth=args.max_call_depth)
        vm.run(program)
    except (LexError, ZbySyntaxError, ZbyRuntimeError) as exc:
        print(render_diagnostic(exc, source_for_diagnostic(args.source)), file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"{args.source}: InputError: {exc.strerror or exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
