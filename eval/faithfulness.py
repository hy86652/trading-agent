"""Deterministic faithfulness check: every number in the final answer must come from the question or a tool result.

This does not use a model, so it cannot be fooled by a confident-sounding judge. It catches invented
prices, indicators and positions (brief section 10, risk 3). It does not check the meaning of the answer;
that is left to the LLM judge.
"""
import re
from typing import Any

# A comma-grouped number ("8,807,415", "99,819.36") read as one token, or else a plain run of digits not
# preceded by a letter, underscore, digit, dot or hyphen. The exclusion keeps labels such as "MA5" and
# "RSI14" out, and keeps the parts of dates such as "2026-01-02" from being read as separate numbers. Without
# the comma alternative, answers that format tool numbers with thousands separators (e.g. "$99,819.36") would
# split into several small numbers ("99", "819.36") that look ungrounded even though the value is correct.
NUMBER = re.compile(r"(?<![A-Za-z_\d.\-])\d{1,3}(?:,\d{3})+(?:\.\d+)?(?!\d)|(?<![A-Za-z_\d.\-])\d+(?:\.\d+)?")
# Same comma-aware matching, with no exclusions. Used only to build the set of "known" numbers from the
# question and tool results: a source date "2026-01-02" must also ground an answer that reformats it as
# "January 2, 2026".
ANY_NUMBER = re.compile(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?(?!\d)|\d+(?:\.\d+)?")
TOLERANCE = 0.005  # tool values are rounded to 2 decimals; answers may round further


def numbers_in(text: str) -> list[float]:
    return [float(match.replace(",", "")) for match in NUMBER.findall(text)]


def _known_numbers_in(text: str) -> list[float]:
    return [float(match.replace(",", "")) for match in ANY_NUMBER.findall(text)]


def final_answer(transcript: list[dict[str, Any]]) -> str:
    """The last assistant message that is a plain answer, not a tool call."""
    for row in reversed(transcript):
        if row["role"] == "ai" and not row.get("tool_calls"):
            return row["content"]
    return ""


def check_faithfulness(trace: dict[str, Any]) -> dict[str, Any]:
    """Compare the numbers in the final answer with the numbers the conversation actually contained."""
    sources = [trace["question"]] + [row["content"] for row in trace["transcript"] if row["role"] in ("human", "tool")]
    known = [n for text in sources for n in _known_numbers_in(text)]
    answer_numbers = numbers_in(final_answer(trace["transcript"]))
    ungrounded = sorted({n for n in answer_numbers if not any(abs(n - k) <= TOLERANCE for k in known)})
    return {
        "checked": len(answer_numbers),
        "ungrounded": ungrounded,
        "passed": not ungrounded,
    }
