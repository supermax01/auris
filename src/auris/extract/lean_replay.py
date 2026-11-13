"""Offline serialization-only cost estimate of a recorded successful response.

No quote shortening, model inference, gold annotation or quality estimate. The
unchanged quotes keep negation/family/time context and make the estimate a
conservative measure of null-key and whitespace savings alone.
"""

import argparse
import copy
import hashlib
import json
import re
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

from auris.assertions import OPTIONAL_DETAIL_FIELDS, extraction_json_schema
from auris.extract.backend import FakeBackend
from auris.extract.pipeline import parse_completion


def lean_response(response: dict[str, Any]) -> dict[str, Any]:
    """Re-serialize only schema-v1-valid, normally completed JSON."""
    parse_completion(response)
    result = copy.deepcopy(response)
    body = json.loads(response["choices"][0]["message"]["content"])
    for assertion in body["assertions"]:
        for key in OPTIONAL_DETAIL_FIELDS & assertion.keys():
            if assertion[key] is None:
                del assertion[key]
    result["choices"][0]["message"]["content"] = json.dumps(
        body, ensure_ascii=False, separators=(",", ":")
    )
    # Existing usage describes the recorded generation, not this counterfactual.
    result.pop("usage", None)
    return result


def estimate_response(response: dict[str, Any], count: Callable[[str], int]) -> dict[str, Any]:
    lean = lean_response(response)
    parsed = []
    counts = []
    for version, answer in (("1", response), ("2", lean)):
        backend = FakeBackend([answer], tokenizer=count)
        replayed = backend.complete(
            [], extraction_json_schema(version), temperature=0, max_tokens=65536
        )
        parsed.append(parse_completion(replayed, schema_version=version))
        counts.append(count(replayed["choices"][0]["message"]["content"]))
    if parsed[0] != parsed[1]:
        raise ValueError("serialization replay changed assertions")
    n = len(parsed[0].assertions)
    return {
        "assertion_count": n,
        "full_output_tokens": counts[0],
        "lean_output_tokens": counts[1],
        "full_output_tokens_per_assertion": counts[0] / n if n else None,
        "lean_output_tokens_per_assertion": counts[1] / n if n else None,
        "saving_fraction": 1 - counts[1] / counts[0] if counts[0] else None,
        "canonical_assertions_identical": True,
        "quote_shortening": False,
        "scope": (
            "FakeBackend serialization replay; excludes discarded calls; "
            "not fresh inference or quality evidence"
        ),
    }


def offline_token_counter(model: Path) -> Callable[[str], int]:
    binary = shutil.which("llama-tokenize")
    if binary is None:
        raise ValueError("offline replay requires the existing llama-tokenize executable")

    def count(text: str) -> int:
        result = subprocess.run(
            [binary, "--offline", "-m", str(model), "--stdin", "--no-bos", "--ids", "--show-count"],
            input=text,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        match = re.search(r"Total number of tokens:\s*(\d+)", result.stdout)
        if result.returncode or match is None:
            raise ValueError("offline tokenizer failed; no response text logged")
        return int(match[1])

    return count


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("extraction", type=Path)
    parser.add_argument(
        "--model", type=Path, required=True, help="Existing local GGUF for tokenization only"
    )
    parser.add_argument(
        "--call", type=int, default=None, help="Zero-based completed call; default first"
    )
    args = parser.parse_args()
    source = args.extraction.read_bytes()
    document = json.loads(source)
    if document["metadata"]["prompt_version"] != "extraction_v3":
        raise ValueError("expected a recorded v3 extraction")
    indices = [i for i, c in enumerate(document["calls"]) if "raw_response" in c]
    index = args.call if args.call is not None else indices[0]
    report = estimate_response(
        document["calls"][index]["raw_response"], offline_token_counter(args.model)
    )
    print(
        json.dumps(
            {
                "source_sha256": hashlib.sha256(source).hexdigest(),
                "call_index": index,
                "token_count_method": "llama-tokenize offline, no BOS, pinned local GGUF",
                **report,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
