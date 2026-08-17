"""Tie ``beval.__version__`` to the changelog.

Nothing in the package read ``__version__``, so it sat at ``0.0.1`` through two
releases while the tags said otherwise. A version string nobody checks is a claim
nobody verified, which is the one thing this project is not supposed to ship.

The changelog is the source of truth: the newest released heading in
``CHANGELOG.md`` and ``__version__`` have to agree.
"""

from __future__ import annotations

import pathlib
import re
import unittest

import beval

CHANGELOG = pathlib.Path(__file__).resolve().parent.parent / "CHANGELOG.md"
RELEASE_HEADING = re.compile(r"^## \[(\d+\.\d+\.\d+)\]", re.MULTILINE)


def released_versions() -> list[str]:
    return RELEASE_HEADING.findall(CHANGELOG.read_text(encoding="utf-8"))


class TestVersion(unittest.TestCase):
    def test_changelog_has_at_least_one_release(self) -> None:
        self.assertTrue(
            released_versions(),
            "CHANGELOG.md has no `## [x.y.z]` heading; this test cannot check anything",
        )

    def test_version_matches_the_newest_release(self) -> None:
        self.assertEqual(beval.__version__, released_versions()[0])


if __name__ == "__main__":
    unittest.main()
