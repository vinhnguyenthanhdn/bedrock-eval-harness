"""Evaluation harness for Amazon Bedrock models.

Public surface today: the case suite format and its loader.
"""

from .suite import Case, Suite, SuiteError, load_suite, load_suite_or_raise

__all__ = ["Case", "Suite", "SuiteError", "load_suite", "load_suite_or_raise"]
__version__ = "0.0.1"
