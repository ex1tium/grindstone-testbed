import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from testbed.text import word_count  # noqa: E402


class WordCount(unittest.TestCase):
    def test_counts_whitespace_separated_words(self) -> None:
        """@verifies AC-0102-1"""
        self.assertEqual(word_count("grind  the\tstone\n"), 3)
        self.assertEqual(word_count(""), 0)


if __name__ == "__main__":
    unittest.main()
