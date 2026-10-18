from __future__ import annotations

from dataclasses import dataclass

from .models import JudgingError, Reference


@dataclass(frozen=True)
class Calibration:
    score: int
    relative_percentile: float
    rank: int
    references_easier: int
    raw_ranks: tuple[int, ...]
    anchor_inversion_rate: float
    stability: str
    probabilities: tuple[float, ...]
    rank_interval: tuple[int, int]
    rating_neighbors: tuple[int | None, int | None]
    estimated_rating_interval: tuple[int, int]


def _round_cf_rating(value: float) -> int:
    return max(800, int((value + 50) // 100) * 100)


def _rating_at_insertion(ratings: list[int], position: int) -> int:
    """Map an insertion slot in the sorted CF ladder to a CF-style rating."""
    if position == 0:
        value = ratings[0] - (ratings[1] - ratings[0]) / 2
    elif position == len(ratings):
        value = ratings[-1] + (ratings[-1] - ratings[-2]) / 2
    else:
        value = (ratings[position - 1] + ratings[position]) / 2
    return _round_cf_rating(value)


def _monotone_probabilities(ratings: list[int], observations: list[float]) -> list[float]:
    """Pooled adjacent violators: P(target harder) cannot rise with anchor rating."""
    groups: list[dict] = []
    for index, (rating, value) in enumerate(zip(ratings, observations)):
        if groups and groups[-1]["rating"] == rating:
            groups[-1]["end"] = index + 1
            groups[-1]["sum"] += value
            groups[-1]["count"] += 1
        else:
            groups.append({"rating": rating, "start": index, "end": index + 1,
                           "sum": value, "count": 1})
    blocks: list[dict] = []
    for group in groups:
        blocks.append(group)
        while len(blocks) > 1 and blocks[-2]["sum"] / blocks[-2]["count"] < blocks[-1]["sum"] / blocks[-1]["count"]:
            right = blocks.pop()
            blocks[-1]["end"] = right["end"]
            blocks[-1]["sum"] += right["sum"]
            blocks[-1]["count"] += right["count"]
    fitted = [0.0] * len(ratings)
    for block in blocks:
        value = block["sum"] / block["count"]
        for index in range(block["start"], block["end"]):
            fitted[index] = value
    return fitted


def calibrate(target_id: str, reference_ids: dict[str, Reference],
              orders: list[list[str]]) -> Calibration:
    if not orders:
        raise JudgingError("No blind rankings to calibrate")
    anchors = sorted(reference_ids, key=lambda ident: (reference_ids[ident].rating,
                                                           reference_ids[ident].contest_id,
                                                           reference_ids[ident].index))
    n = len(anchors)
    expected = set(anchors) | {target_id}
    if not 5 <= n <= 20 or any(len(order) != n + 1 or set(order) != expected for order in orders):
        raise JudgingError("Invalid blind ranking in calibration")
    raw_ranks = tuple(order.index(target_id) + 1 for order in orders)
    observations = [sum(order.index(target_id) > order.index(ident) for order in orders) / len(orders)
                    for ident in anchors]
    ratings = [reference_ids[ident].rating for ident in anchors]
    fitted = _monotone_probabilities(ratings, observations)
    # Under the CF rating ladder, insertion after the first k anchors means
    # "target is harder" for those k and "easier" for the rest. Choose the
    # position that minimizes expected pairwise disagreement with the blind
    # votes. This is a constrained rank aggregation, not a raw average rank.
    costs = [sum(1 - p for p in fitted[:k]) + sum(fitted[k:])
             for k in range(n + 1)]
    minimum = min(costs)
    best = [k for k, cost in enumerate(costs) if abs(cost - minimum) < 1e-9]
    k = best[len(best) // 2]
    score = _rating_at_insertion(ratings, k)
    relative_percentile = round(100 * k / n, 1)
    rank = k + 1
    rating_neighbors = (ratings[k - 1] if k > 0 else None,
                        ratings[k] if k < n else None)
    rating_interval = (_rating_at_insertion(ratings, min(best)),
                       _rating_at_insertion(ratings, max(best)))

    comparable = [(a, b) for a in range(n) for b in range(a + 1, n) if ratings[a] < ratings[b]]
    inversions = 0
    for order in orders:
        positions = {ident: i for i, ident in enumerate(order)}
        inversions += sum(positions[anchors[a]] > positions[anchors[b]] for a, b in comparable)
    inversion_rate = round(inversions / (len(orders) * len(comparable)), 3) if comparable else 0.0
    spread = max(raw_ranks) - min(raw_ranks)
    ambiguity = max(best) - min(best)
    stability = "high" if spread <= 2 and inversion_rate <= 0.1 and ambiguity <= 1 else (
        "medium" if spread <= 4 and inversion_rate <= 0.25 and ambiguity <= 3 else "low")
    return Calibration(score, relative_percentile, rank, k, raw_ranks, inversion_rate,
                       stability, tuple(fitted), (min(best) + 1, max(best) + 1),
                       rating_neighbors, rating_interval)
