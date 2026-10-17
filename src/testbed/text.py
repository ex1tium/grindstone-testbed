"""Small text utilities.

`word_count` satisfies REQ-0102. `slugify` (REQ-0101) is not implemented yet:
ticket TKT-0001 in `intent/` asks for it.
"""


def word_count(text: str) -> int:
    """Count whitespace-separated words."""
    return len(text.split())
