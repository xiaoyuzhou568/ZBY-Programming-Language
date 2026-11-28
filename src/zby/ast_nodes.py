"""Typed AST data containers for the ZBY V0.2 Parser prototype.

These nodes describe syntax only. Runtime type resolution, name validation,
container mutability and execution do not happen in this module.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TypeAlias
from .tokens import Span


@dataclass(frozen=True, slots=True)
class TypeRef:
    name: str
    array: bool
    span: Span


@dataclass(frozen=True, slots=True)
class NamedTypeInit:
    name: str
    value: Expr
    span: Span


@dataclass(frozen=True, slots=True)
class NameExpr:
    name: str
    span: Span


@dataclass(frozen=True, slots=True)
class IntegerExpr:
    lexeme: str
    span: Span


@dataclass(frozen=True, slots=True)
class FloatExpr:
    lexeme: str
    span: Span


@dataclass(frozen=True, slots=True)
class StringExpr:
    value: str
    span: Span


@dataclass(frozen=True, slots=True)
class BoolExpr:
    value: bool
    span: Span


@dataclass(frozen=True, slots=True)
class GroupExpr:
    value: Expr
    span: Span


@dataclass(frozen=True, slots=True)
class UnaryExpr:
    operator: str
    value: Expr
    span: Span


@dataclass(frozen=True, slots=True)
class BinaryExpr:
    operator: str
    left: Expr
    right: Expr
    span: Span


@dataclass(frozen=True, slots=True)
class CallExpr:
    callee: Expr
    arguments: tuple[Expr, ...]
    span: Span


@dataclass(frozen=True, slots=True)
class IndexExpr:
    target: Expr
    index: Expr
    span: Span


@dataclass(frozen=True, slots=True)
class FieldExpr:
    target: Expr
    field: str
    span: Span


@dataclass(frozen=True, slots=True)
class ArrayExpr:
    elements: tuple[Expr, ...]
    span: Span


@dataclass(frozen=True, slots=True)
class StructExpr:
    type_name: str
    fields: tuple[NamedTypeInit, ...]
    span: Span


Expr: TypeAlias = (
    NameExpr | IntegerExpr | FloatExpr | StringExpr | BoolExpr | GroupExpr |
    UnaryExpr | BinaryExpr | CallExpr | IndexExpr | FieldExpr | ArrayExpr |
    StructExpr
)


@dataclass(frozen=True, slots=True)
class Parameter:
    name: str
    type_ref: TypeRef
    span: Span


@dataclass(frozen=True, slots=True)
class StructFieldDecl:
    name: str
    type_ref: TypeRef
    span: Span


@dataclass(frozen=True, slots=True)
class Block:
    statements: tuple[Stmt, ...]
    span: Span


@dataclass(frozen=True, slots=True)
class VarDecl:
    name: str
    type_ref: TypeRef
    initializer: Expr
    span: Span


@dataclass(frozen=True, slots=True)
class Assignment:
    target: NameExpr | IndexExpr | FieldExpr
    value: Expr
    span: Span


@dataclass(frozen=True, slots=True)
class CallStatement:
    call: CallExpr
    span: Span


@dataclass(frozen=True, slots=True)
class ReturnStatement:
    value: Expr | None
    span: Span


@dataclass(frozen=True, slots=True)
class BreakStatement:
    span: Span


@dataclass(frozen=True, slots=True)
class ContinueStatement:
    span: Span


@dataclass(frozen=True, slots=True)
class IfStatement:
    branches: tuple[tuple[Expr, Block], ...]
    else_block: Block | None
    span: Span


@dataclass(frozen=True, slots=True)
class WhileStatement:
    condition: Expr
    body: Block
    span: Span


@dataclass(frozen=True, slots=True)
class ForStatement:
    initializer: VarDecl
    condition: Expr
    update: Assignment
    body: Block
    span: Span


Stmt: TypeAlias = (
    VarDecl | Assignment | CallStatement | ReturnStatement | BreakStatement |
    ContinueStatement | IfStatement | WhileStatement | ForStatement
)


@dataclass(frozen=True, slots=True)
class StructDecl:
    name: str
    fields: tuple[StructFieldDecl, ...]
    span: Span


@dataclass(frozen=True, slots=True)
class FunctionDecl:
    name: str
    parameters: tuple[Parameter, ...]
    return_type: TypeRef
    body: Block
    span: Span


TopLevel: TypeAlias = StructDecl | FunctionDecl | Stmt


@dataclass(frozen=True, slots=True)
class Program:
    items: tuple[TopLevel, ...]
    span: Span
