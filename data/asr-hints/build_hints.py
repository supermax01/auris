"""Print the [asr.hints] words for config/frozen.toml from top_drugs.tsv.

    .venv/bin/python data/asr-hints/build_hints.py data/asr-hints/top_drugs.tsv \
        cache/models/asr/<model>/source/tokenizer.json

Counts tokens with the pinned Whisper tokenizer the way faster-whisper does.
"""

import csv
import sys

from tokenizers import Tokenizer

from auris.speech.asr import MAX_HINT_TOKENS
from auris.speech.hints import hint_words

BUDGET = MAX_HINT_TOKENS  # what faster-whisper keeps


def main(ranking: str, tokenizer_path: str) -> None:
    tokenizer = Tokenizer.from_file(tokenizer_path)
    with open(ranking, encoding="utf-8") as handle:
        rows = sorted(csv.DictReader(handle, delimiter="\t"), key=lambda r: int(r["rank"]))
    words = hint_words(
        (row["generic_name"] for row in rows),
        lambda text: len(tokenizer.encode(text, add_special_tokens=False).ids),
        BUDGET,
    )
    text = " " + ", ".join(words)
    print(f"# {len(words)} names, {len(tokenizer.encode(text, add_special_tokens=False).ids)} tokens")
    print("words = [")
    for word in words:
        print(f'    "{word}",')
    print("]")


if __name__ == "__main__":
    main(*sys.argv[1:])
