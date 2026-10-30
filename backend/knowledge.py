"""Packaged, authored reference library. No network or model is consulted."""
import copy
import json
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=1)
def _cards() -> list[dict]:
    path = Path(__file__).resolve().parent.parent / "data" / "knowledge.json"
    with path.open(encoding="utf-8") as stream:
        return json.load(stream)


def list_cards(query: str = "", language: str = "") -> list[dict]:
    terms = query.casefold().split()
    result = []
    for card in _cards():
        if language and card["language"] != language:
            continue
        text = " ".join(str(card.get(key, "")) for key in ("title", "summary", "body", "code", "tags", "pitfalls")).casefold()
        if all(term in text for term in terms):
            result.append(copy.deepcopy(card))
    return result
