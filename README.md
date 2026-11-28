# ZBY 编程语言

ZBY（ZBY Programming Language）是一门面向编程初学者与 AI 编程 Agent 协作开发的轻量级解释型脚本语言。
设计遵循 **Simplicity（简洁性）、Explicitness（明确性）、Explainability（可解释性）**。

> **状态**：V0.1.0 为已建立的项目基线；本仓库整合的是 V0.2 的开发中解释器与规范初稿，**尚未发布 `v0.2.0`**。功能实现、测试通过与规范正式冻结是不同的里程碑。

## 快速开始

需要 Python 3.10 或更新版本。运行时不依赖第三方 Python 包。

从仓库根目录运行 ZBY 程序：

```bash
PYTHONPATH=src python -m zby examples/scorebook.zby
```

在 Windows PowerShell 中：

```powershell
$env:PYTHONPATH = "src"
python -m zby examples/scorebook.zby
```

也可选择安装可编辑命令行入口（需要 Python 打包工具）：

```bash
python -m pip install -e .
zby examples/scorebook.zby
```

调试 Token 或 AST：

```bash
PYTHONPATH=src python -m zby.lexer examples/lexer_demo.zby
PYTHONPATH=src python -m zby.parser examples/parser_demo.zby --show-ast
```

## 运行测试

在项目根目录：

```bash
PYTHONPATH=src python -m unittest discover -s tests -q
```

PowerShell：

```powershell
$env:PYTHONPATH = "src"
python -m unittest discover -s tests -q
```

自动化测试既有 Lexer/Parser/Interpreter 单元测试，也有通过子进程调用解释器的端到端测试。Windows/macOS 的 CI 测试**需要合并后由 GitHub Actions 实际执行**，本地 Linux 测试不能替代其他操作系统的真实结果。

## 项目目录

- `src/zby/`：Lexer、Parser、AST、解释执行器与诊断模块。
- `tests/`：单元测试、语法验证案例及命令行黑盒测试。
- `examples/`：可运行的 `.zby` 示例及错误演示。
- `docs/language-spec.md`：语言规范初稿。
- `docs/type-system.md`：类型规则、内置函数与转换语义。
- `docs/grammar.ebnf`：与 Parser 对照的 EBNF 草案。
- `docs/design-decisions.md`：设计历史、备选方案、取舍与测试。
- `docs/open-questions.md`：待决的语言和实现细节。
- `docs/development/`：各阶段工程与解析器审查记录。

## 当前边界

- 空结构体、尾随逗号等语法尚待正式选择，原型当前保守拒绝；不代表语言最终禁止。
- 仍需全面核对极端数值、资源不足、复合对象打印和 Windows/macOS 终端行为。
- 不提供完整的静态类型检查器；词法与语法在执行前检查，运行时按实际执行路径检查类型和名称。
- `++`、`--`、`+=`、`-=` 在第一版暂缓引入，但保留后续复评意向。

项目旨在完成语言规范、解释器、自动化测试、Python 性能对比，以及基于 ZBY 的实际应用和开发指南。

本轮工程整合的验证记录见 `docs/development/integration-audit.md`。不应将开发包直接视为正式发行版本。
