from __future__ import annotations

import re
from typing import Optional


def extract_subprogram(source_text: str, subprogram: str) -> str:
    """
    Extract the body of a named PROCEDURE or FUNCTION from a PL/SQL source text.

    Uses a line-scanning state machine:
      FIND_HEADER → FIND_IS → COUNT_BEGIN_END → DONE

    Falls back to the full source_text if the subprogram is not found or extraction
    fails (e.g. nested structures confuse the heuristic).

    If subprogram is empty/None, returns source_text unchanged.
    """
    if not subprogram:
        return source_text

    # Regex to detect the start of the target subprogram header.
    # Matches: PROCEDURE name  or  FUNCTION name
    # Allows optional whitespace and handles word boundaries.
    header_re = re.compile(
        r"^\s*(PROCEDURE|FUNCTION)\s+" + re.escape(subprogram.upper()) + r"\b",
        re.IGNORECASE,
    )

    lines = source_text.splitlines(keepends=True)

    # State machine states
    STATE_FIND_HEADER = 0
    STATE_FIND_IS = 1
    STATE_COUNT = 2

    state = STATE_FIND_HEADER
    start_line: Optional[int] = None
    depth = 0
    seen_begin = False  # True once the first BEGIN of the subprogram body is encountered

    # Token patterns used during counting
    # We strip single-line comments (--) and string literals before matching keywords
    begin_re = re.compile(r"\bBEGIN\b|\bCASE\b", re.IGNORECASE)
    end_re = re.compile(r"\bEND\b", re.IGNORECASE)
    is_as_re = re.compile(r"\b(IS|AS)\b", re.IGNORECASE)

    for i, raw_line in enumerate(lines):
        line = _strip_comment(raw_line).upper()

        if state == STATE_FIND_HEADER:
            if header_re.match(raw_line):
                start_line = i
                state = STATE_FIND_IS
                # The header line itself may also contain IS/AS
                if is_as_re.search(line):
                    state = STATE_COUNT

        elif state == STATE_FIND_IS:
            if is_as_re.search(line):
                state = STATE_COUNT

        elif state == STATE_COUNT:
            begins = len(begin_re.findall(line))
            ends = len(end_re.findall(line))
            depth += begins
            if begins > 0:
                seen_begin = True
            depth -= ends
            # Stop when we've seen at least one BEGIN and depth is back to 0
            if seen_begin and depth <= 0:
                end_line = i
                return "".join(lines[start_line : end_line + 1])

    # Fallback: extraction failed, return full source
    return source_text


def _strip_comment(line: str) -> str:
    """Remove inline -- comment from a line."""
    idx = line.find("--")
    if idx >= 0:
        return line[:idx]
    return line
