"""Offline prompt/corpus overlap checks, independent of inference and scoring.

This lexical guard is not a semantic contamination detector. Evaluation callers
must supply the complete dev/test corpus and extend the clinical term list for
their data. Findings contain normalized text for private inspection; metadata
contains only hashes, counts and settings and is safe to record without quotes.
"""

import hashlib
import json
import re
import unicodedata
from collections import Counter
from collections.abc import Iterable
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

CHECK_VERSION = "sentence_overlap_v1"
JACCARD_THRESHOLD = 0.6
MIN_SENTENCE_TOKENS = 4
# Named clinical content in contrastive_cases.json, fhir/a3_golden.json and
# transcript_example.json. Generic schema words (allergy, condition, etc.) are
# deliberately excluded. These are lexical watchwords, not a coding lexicon.
FIXTURE_CLINICAL_TERMS = frozenset(
    {"amoxicillin", "asthma", "bronchitis", "cough", "diabetes", "penicillin", "pneumonia", "rash"}
)


class _LeakageModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SentenceOverlap(_LeakageModel):
    corpus_index: int
    prompt_sentence: str
    corpus_sentence: str
    jaccard: float


class ClinicalOverlap(_LeakageModel):
    corpus_index: int
    term: str


class PromptLeakageError(ValueError):
    """Evaluation must stop before inference when any overlap is detected."""


class LeakageReport(_LeakageModel):
    prompt_sha256: str
    corpus_sha256: str
    corpus_text_count: int
    clinical_terms: tuple[str, ...]
    exact_sentences: tuple[SentenceOverlap, ...]
    near_duplicates: tuple[SentenceOverlap, ...]
    clinical_content: tuple[ClinicalOverlap, ...]
    clinical_policy: Literal["refuse", "warn"] = "refuse"

    @property
    def passed(self) -> bool:
        return not (
            self.exact_sentences
            or self.near_duplicates
            or (self.clinical_content and self.clinical_policy == "refuse")
        )

    def raise_if_failed(self) -> None:
        if not self.passed:
            raise PromptLeakageError("prompt/corpus leakage detected; evaluation refused")

    def metadata(self) -> dict[str, Any]:
        """Quote-free metadata for a harness's run artifact, including failed runs."""
        return {
            "leakage_check": "pass" if self.passed else "fail",
            "leakage_check_details": {
                "version": CHECK_VERSION,
                "prompt_sha256": self.prompt_sha256,
                "corpus_sha256": self.corpus_sha256,
                "corpus_text_count": self.corpus_text_count,
                "jaccard_threshold": JACCARD_THRESHOLD,
                "min_sentence_tokens": MIN_SENTENCE_TOKENS,
                "clinical_terms_sha256": _hash_texts(self.clinical_terms),
                "exact_sentence_count": len(self.exact_sentences),
                "near_duplicate_count": len(self.near_duplicates),
                "clinical_content_count": len(self.clinical_content),
                "clinical_policy": self.clinical_policy,
                "clinical_warning_counts": dict(
                    sorted(Counter(hit.term for hit in self.clinical_content).items())
                )
                if self.clinical_policy == "warn"
                else {},
            },
        }


def _hash_texts(texts: Iterable[str]) -> str:
    return hashlib.sha256(
        json.dumps(tuple(texts), ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _tokens(text: str) -> tuple[str, ...]:
    text = unicodedata.normalize("NFKC", text).casefold().replace("’", "'")
    return tuple(
        token.replace("'", "") for token in re.findall(r"\d+(?:\.\d+)*|[^\W_]+(?:'[^\W_]+)*", text)
    )


def _sentences(text: str) -> tuple[tuple[str, ...], ...]:
    # Isolate quoted examples from speaker labels and explanatory arrows. Scan
    # the surrounding rules as well, not just the examples section. Newlines
    # and .?!; delimit sentences; a decimal point between digits stays intact.
    text = re.sub(r'["“]([^"“”\n]+)["”]', r"\n\1\n", text)
    parts = re.split(r"[!?;\n]+|(?<!\d)\.|\.(?!\d)", text)
    return tuple(dict.fromkeys(tokens for part in parts if (tokens := _tokens(part))))


def _contains(tokens: tuple[str, ...], term: tuple[str, ...]) -> bool:
    return any(tokens[i : i + len(term)] == term for i in range(len(tokens) - len(term) + 1))


def check_leakage(
    prompt: str,
    corpus: Iterable[str],
    *,
    clinical_terms: Iterable[str] = FIXTURE_CLINICAL_TERMS,
    clinical_policy: Literal["refuse", "warn"] = "refuse",
) -> LeakageReport:
    """Compare a prompt with evaluation texts before any model sees them.

    Exact normalized sentences have no length minimum. Nonidentical sentences
    with at least four tokens each are flagged at token-set Jaccard >= 0.6;
    stop words, numbers and negations count. Clinical terms match whole token
    sequences on both sides, including multiword names. No stemming, synonyms
    or clinical inference is performed. Empty/blank corpora are rejected so an
    accidentally unpopulated evaluation set cannot receive a passing check.
    """
    if isinstance(corpus, str) or isinstance(clinical_terms, str):
        raise TypeError("corpus and clinical_terms must be collections of texts, not a string")
    texts = tuple(corpus)
    corpus_sentences = tuple(_sentences(text) for text in texts)
    if not corpus_sentences or any(not sentences for sentences in corpus_sentences):
        raise ValueError("leakage check requires a nonempty corpus of nonblank texts")
    prompt_sentences = _sentences(prompt)
    if not prompt_sentences:
        raise ValueError("leakage check requires a nonblank prompt")
    terms = tuple(
        sorted({" ".join(tokens) for term in clinical_terms if (tokens := _tokens(term))})
    )
    prompt_tokens = _tokens(prompt)
    shared_candidates = tuple(term for term in terms if _contains(prompt_tokens, _tokens(term)))
    exact: list[SentenceOverlap] = []
    near: list[SentenceOverlap] = []
    clinical: list[ClinicalOverlap] = []
    for index, sentences in enumerate(corpus_sentences):
        for sentence in sentences:
            for example in prompt_sentences:
                identical = sentence == example
                if not identical and min(len(sentence), len(example)) < MIN_SENTENCE_TOKENS:
                    continue
                first, second = set(sentence), set(example)
                score = len(first & second) / len(first | second)
                if identical or score >= JACCARD_THRESHOLD:
                    hit = SentenceOverlap(
                        corpus_index=index,
                        prompt_sentence=" ".join(example),
                        corpus_sentence=" ".join(sentence),
                        jaccard=score,
                    )
                    (exact if identical else near).append(hit)
        tokens = _tokens(texts[index])
        clinical.extend(
            ClinicalOverlap(corpus_index=index, term=term)
            for term in shared_candidates
            if _contains(tokens, _tokens(term))
        )
    return LeakageReport(
        prompt_sha256=hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
        corpus_sha256=_hash_texts(texts),
        corpus_text_count=len(texts),
        clinical_terms=terms,
        exact_sentences=tuple(exact),
        near_duplicates=tuple(near),
        clinical_content=tuple(clinical),
        clinical_policy=clinical_policy,
    )
