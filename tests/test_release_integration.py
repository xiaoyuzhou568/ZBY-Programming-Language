"""Integration checks for the Git repository baseline and DD-072."""
from __future__ import annotations

import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from zby.interpreter import ZbyRuntimeError, run

ROOT = Path(__file__).resolve().parent.parent


class IntegrationTests(unittest.TestCase):
    def test_module_entry_point_executes_source(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "hello.zby"
            path.write_text('print("ZBY works");', encoding="utf-8")
            env = dict(os.environ, PYTHONPATH=str(ROOT / "src"), PYTHONIOENCODING="utf-8")
            result = subprocess.run([sys.executable, "-m", "zby", str(path)],
                                    cwd=ROOT, env=env, capture_output=True,
                                    timeout=10, check=False)
            self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8"))
            self.assertEqual(result.stdout, b"ZBY works\n")
            self.assertEqual(result.stderr, b"")

    def test_huge_integer_division_finite_quotient(self):
        huge = "1" + "0" * 400
        vm = run(f'let x: float = {huge} / {huge};')
        self.assertEqual(vm.global_scope.values["x"].value, 1.0)

    def test_huge_integer_division_scaled_quotient(self):
        huge = "1" + "0" * 400
        vm = run(f'let x: float = {huge} / {"2" + "0" * 400};')
        # 10**400 / (2 * 10**400) = 0.5.
        self.assertEqual(vm.global_scope.values["x"].value, 0.5)

    def test_huge_integer_division_overflow_is_zby_error(self):
        huge = "1" + "0" * 400
        with self.assertRaises(ZbyRuntimeError) as cm:
            run(f'let x: float = {huge} / 1;')
        self.assertEqual(cm.exception.category, "ValueError")

    def test_huge_integer_division_underflow(self):
        huge = "1" + "0" * 400
        vm = run(f'let x: float = 1 / {huge};')
        self.assertEqual(vm.global_scope.values["x"].value, 0.0)

    def test_huge_integer_division_negative_underflow_preserves_sign(self):
        huge = "1" + "0" * 400
        vm = run(f'let x: float = -1 / {huge};')
        result = vm.global_scope.values["x"].value
        self.assertEqual(result, 0.0)
        self.assertLess(math.copysign(1.0, result), 0)

    def test_huge_integer_division_midpoint_ties_even(self):
        vm = run('let x: float = 9007199254740993 / 1;')
        self.assertEqual(vm.global_scope.values["x"].value, 9007199254740992.0)

    def test_integer_division_fractional_result(self):
        vm = run('let x: float = 7 / 3;')
        self.assertEqual(vm.global_scope.values["x"].value, 7 / 3)


if __name__ == "__main__":
    unittest.main()
