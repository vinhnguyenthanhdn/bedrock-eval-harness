"""Evaluation harness for Amazon Bedrock models.

Re-exported here: the case suite format and its loader. The rest of the package
is imported from its own module — `beval.runner` to run or replay a suite,
`beval.ledger` to score one, `beval.compare` to diff two runs.

`__version__` is the released version and is checked against the newest entry in
CHANGELOG.md by the test suite.
"""

from .suite import Case, Suite, SuiteError, load_suite, load_suite_or_raise

__all__ = ["Case", "Suite", "SuiteError", "load_suite", "load_suite_or_raise"]
__version__ = "0.2.0"
