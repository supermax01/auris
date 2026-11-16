"""Exact drug-name lexicon from the RxNorm index: longest token match, no fuzzy coding."""

from collections.abc import Iterable

from auris.terminology.index import TerminologyIndex
from auris.terminology.text import RXNORM, UK_US_NAMES, normalise


class DrugLexicon:
    """Longest exact token sequence at each position; no fuzzy coding or inference."""

    def __init__(self, names: Iterable[str]) -> None:
        terms = {normalise(name) for name in names} - {""}
        terms.update(alias for alias, target in UK_US_NAMES.items() if normalise(target) in terms)
        self.names = tuple(sorted(terms))
        self.by_first: dict[str, list[tuple[str, ...]]] = {}
        for name in self.names:
            tokens = tuple(name.split())
            self.by_first.setdefault(tokens[0], []).append(tokens)
        for values in self.by_first.values():
            values.sort(key=lambda value: (-len(value), value))

    @classmethod
    def from_index(cls, index: TerminologyIndex) -> "DrugLexicon":
        return cls(
            row[0]
            for row in index.connection.execute(
                "SELECT DISTINCT normalised FROM terms WHERE system=?", (RXNORM,)
            )
        )

    def count(self, text: str) -> int:
        tokens = tuple(normalise(text).split())
        position = count = 0
        while position < len(tokens):
            match = next(
                (
                    name
                    for name in self.by_first.get(tokens[position], ())
                    if tokens[position : position + len(name)] == name
                ),
                None,
            )
            count += int(match is not None)
            position += len(match) if match else 1
        return count
