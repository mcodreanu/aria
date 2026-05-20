"""Small deterministic long-term memory extraction helpers."""

from __future__ import annotations

import re
from typing import Iterable

from memory import Memory


_FACT_PATTERNS = [
    (re.compile(r"\bmy preferred ([a-z0-9 _-]{2,40}) is (.+)", re.I), "preference_{key}"),
    (re.compile(r"\bi prefer ([a-z0-9 _-]{2,80})", re.I), "preference_general"),
    (re.compile(r"\bremember that (.+)", re.I), "note"),
    (re.compile(r"\bkeep in mind that (.+)", re.I), "note"),
]


def extract_user_facts(user_text: str) -> list[tuple[str, str]]:
    facts: list[tuple[str, str]] = []
    for pattern, key_template in _FACT_PATTERNS:
        match = pattern.search(user_text)
        if not match:
            continue
        if "{key}" in key_template:
            key = re.sub(r"\W+", "_", match.group(1).strip().lower()).strip("_")
            value = match.group(2).strip()
            facts.append((key_template.format(key=key), value))
        else:
            value = match.group(1).strip()
            facts.append((f"{key_template}_{abs(hash(value)) % 100000}", value))
    return facts


def store_explicit_facts(memory: Memory, user_text: str) -> None:
    for key, value in extract_user_facts(user_text):
        if value:
            memory.remember(key, value[:500])


def relevant_facts(memory: Memory, user_text: str, limit: int = 8) -> list[tuple[str, str]]:
    words = {w for w in re.findall(r"[a-z0-9]{3,}", user_text.lower())}
    scored: list[tuple[int, str, str]] = []
    for key, value in memory.facts.items():
        if key.startswith("_"):
            continue
        haystack = f"{key} {value}".lower()
        score = sum(1 for w in words if w in haystack)
        if score:
            scored.append((score, key, value))
    scored.sort(reverse=True)
    if scored:
        return [(key, value) for _score, key, value in scored[:limit]]
    return [(k, v) for k, v in memory.facts.items() if not k.startswith("_")][:limit]
