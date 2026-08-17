"""Pin the public surface of ``beval``.

``scripts/scope_guard.py`` only fires on a pull request, and only when it can read
the description. This test is the copy that runs on every push: it fails when a
public definition disappears from a module, so the removal shows up as a red
suite next to the change instead of as somebody's ImportError later.

Adding a name here is a one-line edit. That is the point — removing one should
cost a deliberate edit too.
"""

from __future__ import annotations

import ast
import pathlib
import unittest

import beval

PACKAGE_DIR = pathlib.Path(beval.__file__).parent

EXPECTED_SURFACE = {
    "__init__.py": [],
    "__main__.py": [],
    "bedrock.py": ["BedrockConverseClient", "MissingSDK"],
    "checks.py": ["validate_check"],
    "cli.py": ["main"],
    "client.py": [
        "ConverseClient",
        "ResponseShapeError",
        "ScriptedClient",
        "make_converse_response",
        "read_response",
    ],
    "compare.py": ["Comparison", "compare_scored"],
    "evaluate.py": ["CaseResult", "CheckResult", "evaluate_case", "evaluate_check"],
    "ledger.py": [
        "PriceList",
        "Scored",
        "load_prices",
        "parse_prices",
        "percentile",
        "score_run",
    ],
    "request.py": ["build_converse_body", "converse_kwargs", "resolve_setting"],
    "runfile.py": ["Response", "Run", "load_run", "parse_run"],
    "runner.py": [
        "Exchange",
        "Record",
        "RecordMismatch",
        "RecordedClient",
        "RunAborted",
        "RunOutcome",
        "load_record",
        "parse_record",
        "run_suite",
        "run_to_json",
        "write_json",
    ],
    "suite.py": [
        "Case",
        "Suite",
        "SuiteError",
        "load_suite",
        "load_suite_or_raise",
        "parse_suite",
    ],
}

EXPECTED_PACKAGE_EXPORTS = [
    "Case",
    "Suite",
    "SuiteError",
    "load_suite",
    "load_suite_or_raise",
]


def public_names(path: pathlib.Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return sorted(
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        and not node.name.startswith("_")
    )


class TestPublicSurface(unittest.TestCase):
    def test_every_module_is_covered(self) -> None:
        on_disk = sorted(p.name for p in PACKAGE_DIR.glob("*.py"))
        self.assertEqual(
            on_disk,
            sorted(EXPECTED_SURFACE),
            "a module was added or removed; update EXPECTED_SURFACE in this file",
        )

    def test_public_definitions_are_unchanged(self) -> None:
        for module, expected in sorted(EXPECTED_SURFACE.items()):
            with self.subTest(module=module):
                self.assertEqual(public_names(PACKAGE_DIR / module), sorted(expected))

    def test_package_exports_are_unchanged(self) -> None:
        self.assertEqual(sorted(beval.__all__), sorted(EXPECTED_PACKAGE_EXPORTS))
        for name in EXPECTED_PACKAGE_EXPORTS:
            with self.subTest(name=name):
                self.assertTrue(hasattr(beval, name))


if __name__ == "__main__":
    unittest.main()
