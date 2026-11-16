"""Build the drug-name hint list from a ranking of commonly prescribed drugs.

The ranking comes from a source independent of every evaluation set (see
``data/asr-hints/SOURCE.md``). This turns it into what people say: one entry
per ingredient, without salt or release-form words ("metoprolol succinate
er" is said "metoprolol"), everyday words left out, in rank order, until the
token budget is used. The result is frozen in ``config/frozen.toml``.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable

# Words that name a salt, an ester or a release form, not the medicine people say.
SALT_AND_FORM_WORDS = frozenset(
    {
        "acetate", "besylate", "bitartrate", "bromide", "calcium", "carbonate",
        "chloride", "citrate", "cr", "delayed", "dihydrate", "dipropionate", "disodium",
        "dr", "er", "esylate", "ethinyl", "extended", "fumarate", "furoate",
        "gluconate", "hcl", "hyclate", "hydrobromide", "hydrochloride", "inhalation",
        "la", "magnesium", "maleate", "mesylate", "monohydrate", "nasal", "ophthalmic",
        "oral", "phosphate", "potassium", "propionate", "release", "sodium", "sr",
        "succinate", "sulfate", "tartrate", "topical", "trihydrate", "xl",
    }
)  # fmt: skip
# Never a hint: an everyday word, or a name so short it matches ordinary speech.
NOT_HINTS = frozenset(
    {"water", "air", "oxygen", "glucose", "caffeine", "honey", "milk", "iron", "zinc"}
)
# Combination separators; Multum (the ranking's names) joins two ingredients with "-".
_SPLIT = re.compile(r"\s*(?:;|/|,|\+|-|\bwith\b|\band\b)\s*")


def ingredients(name: str) -> list[str]:
    """The spoken ingredient names in one ranked entry, in order."""
    spoken = []
    for part in _SPLIT.split(name.lower()):
        words = [w for w in re.findall(r"[a-z]+", part) if w not in SALT_AND_FORM_WORDS]
        if words:
            spoken.append(" ".join(words))
    return spoken


def hint_words(
    ranked: Iterable[str], count_tokens: Callable[[str], int], budget: int
) -> tuple[str, ...]:
    """Ingredients in rank order, each once, until the hint text would exceed ``budget``
    tokens (counted on the text Whisper is given: names joined by ", ")."""
    chosen: list[str] = []
    for name in ranked:
        for ingredient in ingredients(name):
            if ingredient in chosen or ingredient in NOT_HINTS or len(ingredient) < 5:
                continue
            if count_tokens(" " + ", ".join([*chosen, ingredient])) > budget:
                return tuple(chosen)
            chosen.append(ingredient)
    return tuple(chosen)
