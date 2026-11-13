"""Dependency-free binomial bounds and encounter bootstrap."""

import math
import random
from collections.abc import Callable, Sequence


def _binomial_cdf(k: int, n: int, p: float) -> float:
    if p <= 0:
        return 1.0
    if p >= 1:
        return float(k == n)
    logs = [
        math.lgamma(n + 1)
        - math.lgamma(i + 1)
        - math.lgamma(n - i + 1)
        + i * math.log(p)
        + (n - i) * math.log1p(-p)
        for i in range(k + 1)
    ]
    largest = max(logs)
    return math.exp(largest) * math.fsum(math.exp(x - largest) for x in logs)


def clopper_pearson_upper(k: int, n: int, confidence: float = 0.95) -> float | None:
    """Exact one-sided upper confidence bound, not a two-sided interval's upper end."""
    if not 0 <= k <= n or not 0 < confidence < 1:
        raise ValueError("invalid binomial counts or confidence")
    if n == 0:
        return None
    if k == n:
        return 1.0
    low, high = 0.0, 1.0
    for _ in range(80):
        middle = (low + high) / 2
        if _binomial_cdf(k, n, middle) > 1 - confidence:
            low = middle
        else:
            high = middle
    return (low + high) / 2


def percentile(values: Sequence[float], q: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * q
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    return ordered[lower] + (position - lower) * (ordered[upper] - ordered[lower])


def bootstrap[T](
    items: Sequence[T], statistic: Callable[[Sequence[T]], float], *, seed: int, samples: int = 2000
) -> dict[str, float | int | str]:
    if not items or samples < 2:
        raise ValueError("bootstrap requires encounters and at least two replicates")
    rng = random.Random(seed)
    values = [statistic([items[rng.randrange(len(items))] for _ in items]) for _ in range(samples)]
    low, high = percentile(values, 0.025), percentile(values, 0.975)
    return {
        "low": low,
        "high": high,
        "half_width": (high - low) / 2,
        "seed": seed,
        "samples": samples,
        "method": "encounter percentile bootstrap 95%",
    }
