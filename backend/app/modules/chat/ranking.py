"""Hybrid schema retrieval: keyword scoring, rank fusion and join paths.

Pure functions over documents the caller has already restricted to the user's visible
scope. Nothing here reads the database or calls a model, so retrieval can be tuned and
tested without either.
"""

import math
import re
from collections import Counter, deque
from collections.abc import Iterable
from dataclasses import dataclass, field

# (kind, ref_id): the same identity the embedding index uses.
DocKey = tuple[str, int]

_ASCII_WORD = re.compile(r"[a-z0-9]+")
_CJK_RUN = re.compile(r"[\u3400-\u9fff]+")


@dataclass(frozen=True)
class Doc:
    kind: str  # table, column, semantic, metric, example
    ref_id: int
    # Exact names and aliases: one found literally in the question is a strong signal.
    names: tuple[str, ...]
    # Free text (comments, descriptions, enum labels); matched by overlapping grams.
    text: str = ""
    # The qualified table a table or column document belongs to.
    table: str = ""
    column: str = ""
    # Tables and columns an item (semantic, metric, example) uses.
    tables: dict[str, frozenset[str]] = field(default_factory=dict)

    @property
    def key(self) -> DocKey:
        return (self.kind, self.ref_id)


def grams(text: str) -> set[str]:
    """Words for matching: ASCII words, and character bigrams of Chinese runs."""
    folded = text.casefold()
    found = {word for word in _ASCII_WORD.findall(folded.replace("_", " ")) if len(word) >= 2}
    for run in _CJK_RUN.findall(folded):
        found.update(run[index : index + 2] for index in range(len(run) - 1))
    return found


def keyword_scores(question: str, docs: list[Doc]) -> dict[DocKey, float]:
    """IDF-weighted gram overlap with the question, plus a bonus for a literal name.

    A gram most documents share (订单 in an order schema) says little; a rare one (退款)
    says a lot. Scores are comparable across documents of one question only.
    """
    asked = grams(question)
    folded = question.casefold()
    if not docs:
        return {}
    texts = [grams(" ".join(doc.names) + " " + doc.text) for doc in docs]
    frequency = Counter(gram for doc_grams in texts for gram in doc_grams)
    weight = {
        gram: math.log(1 + len(docs) / frequency[gram]) for gram in asked if gram in frequency
    }
    total = sum(weight.values())
    scores: dict[DocKey, float] = {}
    for doc, doc_grams in zip(docs, texts, strict=True):
        score = sum(weight[gram] for gram in asked & doc_grams) / total if total else 0.0
        literal = [name for name in doc.names if len(name) >= 2 and name.casefold() in folded]
        if literal:
            score += 1.0 + min(max(len(name) for name in literal), 10) / 20
        if score > 0:
            scores[doc.key] = score
    return scores


def ranked(scores: dict[DocKey, float], keep: float = 0.0) -> list[DocKey]:
    """Keys by descending score, keeping those within `keep` of the best (0 keeps all).

    The best is capped at 1: a literal-name bonus must not push every gram match out.
    """
    if not scores:
        return []
    best = min(max(scores.values()), 1.0)
    return [
        key
        for key, score in sorted(scores.items(), key=lambda pair: (-pair[1], pair[0]))
        if score >= best * keep
    ]


def fuse(rankings: Iterable[list[DocKey]], k: int = 60) -> dict[DocKey, float]:
    """Reciprocal rank fusion: robust to keyword and vector scores on different scales."""
    fused: dict[DocKey, float] = {}
    for ranking in rankings:
        for position, key in enumerate(ranking):
            fused[key] = fused.get(key, 0.0) + 1.0 / (k + position + 1)
    return fused


def join_paths(selected: set[str], edges: Iterable[tuple[str, str]], max_hops: int = 3) -> set[str]:
    """Tables on the shortest join path between any two selected tables.

    Only bridges are added (orders between order_item and customer); a table merely
    next to one selected table is not, so a small question keeps a small context.
    """
    graph: dict[str, set[str]] = {}
    for left, right in edges:
        if left != right:
            graph.setdefault(left, set()).add(right)
            graph.setdefault(right, set()).add(left)
    added: set[str] = set()
    targets = sorted(selected)
    for index, start in enumerate(targets):
        if start not in graph:
            continue
        parents: dict[str, str | None] = {start: None}
        queue = deque([(start, 0)])
        while queue:
            node, depth = queue.popleft()
            if depth >= max_hops:
                continue
            for neighbor in sorted(graph.get(node, ())):
                if neighbor not in parents:
                    parents[neighbor] = node
                    queue.append((neighbor, depth + 1))
        for goal in targets[index + 1 :]:
            if goal not in parents:
                continue
            step = parents[goal]
            while step is not None and step != start:
                if step not in selected:
                    added.add(step)
                step = parents[step]
    return added
