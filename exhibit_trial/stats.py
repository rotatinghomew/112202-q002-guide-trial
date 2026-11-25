"""按家庭归并的统计工具：比例差、区间、置换检验与聚类自助。

分析单位是家庭 × 场次的观测；同一家庭跨场出现时按户聚类，
避免把一个家庭当成几位访客。
"""
from __future__ import annotations

import itertools
import math
import random
from dataclasses import dataclass
from typing import Callable, Iterable, Sequence

import numpy as np

from .observations import FAILURE, SUCCESS, Observation

TWO_SIDED = 0.975


@dataclass(frozen=True)
class Cell:
    n: int
    known: int
    successes: int

    @property
    def unknown(self) -> int:
        return self.n - self.known

    @property
    def rate(self) -> float | None:
        return self.successes / self.known if self.known else None

    def as_tuple(self) -> tuple[int, int]:
        return self.successes, self.known


def cell(observations: Iterable[Observation]) -> Cell:
    rows = list(observations)
    known = [row for row in rows if row.outcome in (SUCCESS, FAILURE)]
    return Cell(
        n=len(rows),
        known=len(known),
        successes=sum(row.outcome == SUCCESS for row in known),
    )


def wilson(successes: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (float("nan"), float("nan"))
    p = successes / n
    denominator = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denominator
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denominator
    return max(0.0, centre - half), min(1.0, centre + half)


def newcombe(
    successes_a: int, n_a: int, successes_b: int, n_b: int, z: float = 1.96
) -> tuple[float, float, float]:
    """两独立比例差 b - a 的 Newcombe 混合区间。"""
    if not n_a or not n_b:
        return float("nan"), float("nan"), float("nan")
    p_a = successes_a / n_a
    p_b = successes_b / n_b
    lo_a, hi_a = wilson(successes_a, n_a, z)
    lo_b, hi_b = wilson(successes_b, n_b, z)
    diff = p_b - p_a
    lower = diff - math.sqrt((p_a - lo_a) ** 2 + (hi_b - p_b) ** 2)
    upper = diff + math.sqrt((hi_a - p_a) ** 2 + (p_b - lo_b) ** 2)
    # 比例差必在 [-1, 1] 内；小样本时混合区间可能越界，按定义截断。
    return diff, max(-1.0, lower), min(1.0, upper)


def missing_bounds(group: Sequence[Observation]) -> tuple[float, float]:
    """未知全部计失败 / 全部计成功时的成功率范围。"""
    rows = list(group)
    if not rows:
        return float("nan"), float("nan")
    known = [row for row in rows if row.outcome in (SUCCESS, FAILURE)]
    successes = sum(row.outcome == SUCCESS for row in known)
    unknown = len(rows) - len(known)
    return successes / len(rows), (successes + unknown) / len(rows)


def miettinen_nurminen(
    pairs: Sequence[tuple[tuple[int, int], tuple[int, int]]], weight: str = "cmh"
) -> float | None:
    """按配对（场次对）分层的比例差 b - a。

    pairs 里每项是 ((a 成功, a 已知), (b 成功, b 已知))。
    """
    usable = [(a, b) for a, b in pairs if a[1] and b[1]]
    if not usable:
        return None
    if weight == "cmh":
        weights = [na * nb / (na + nb) for (_, na), (_, nb) in usable]
    elif weight == "inverse_variance":
        weights = []
        for (sa, na), (sb, nb) in usable:
            pa, pb = sa / na, sb / nb
            variance = pa * (1 - pa) / na + pb * (1 - pb) / nb
            weights.append(1 / variance if variance > 0 else 0.0)
    else:
        raise ValueError(weight)
    total = sum(weights)
    if not total:
        return None
    return sum(
        w * (b[0] / b[1] - a[0] / a[1]) for w, (a, b) in zip(weights, usable)
    ) / total


def pair_cells(
    observations: Sequence[Observation],
    arm_of: Callable[[Observation], str] = lambda o: o.assigned,
) -> list[tuple[str, tuple[int, int], tuple[int, int]]]:
    """按场次对分组，返回 (pair, (A 成功, A 已知), (B 成功, B 已知))。"""
    result = []
    for pair in sorted({row.pair for row in observations}):
        arm_a = cell(row for row in observations if row.pair == pair and arm_of(row) == "A")
        arm_b = cell(row for row in observations if row.pair == pair and arm_of(row) == "B")
        if arm_a.known and arm_b.known:
            result.append((pair, arm_a.as_tuple(), arm_b.as_tuple()))
    return result


def permutation_p(
    pairs: Sequence[tuple[str, tuple[int, int], tuple[int, int]]],
    seed: int = 0,
    reps: int = 200_000,
) -> float | None:
    """在每一对内部交换两场标签的精确随机化检验。

    对 16 对以内精确枚举 2^k 种分配；更多时用固定种子的重抽样。
    两侧都取绝对值，对应双侧检验。
    """
    if not pairs:
        return None

    def statistic(signs: Sequence[int]) -> float:
        return sum(
            sign * (b[0] / b[1] - a[0] / a[1]) for sign, (_, a, b) in zip(signs, pairs)
        )

    observed = abs(statistic([1] * len(pairs)))
    if len(pairs) <= 16:
        extreme = 0
        total = 0
        for signs in itertools.product((1, -1), repeat=len(pairs)):
            total += 1
            if abs(statistic(signs)) >= observed - 1e-12:
                extreme += 1
        return extreme / total
    rng = random.Random(seed)
    extreme = 0
    for _ in range(reps):
        if abs(statistic([rng.choice((1, -1)) for _ in pairs])) >= observed - 1e-12:
            extreme += 1
    return (extreme + 1) / (reps + 1)


def cluster_bootstrap_rd(
    observations: Sequence[Observation],
    cluster_of: Callable[[Observation], str],
    arm_of: Callable[[Observation], str] = lambda o: o.assigned,
    seed: int = 0,
    reps: int = 20_000,
) -> tuple[float, float, float]:
    """按户重抽、按场次对分层的比例差区间；返回 (点估计, 下限, 上限)。"""
    point = miettinen_nurminen(
        [(a, b) for _, a, b in pair_cells(observations, arm_of)]
    )
    if point is None:
        return float("nan"), float("nan"), float("nan")

    pairs = sorted({row.pair for row in observations})
    rows_by_pair = {pair: [row for row in observations if row.pair == pair] for pair in pairs}
    clusters_by_pair = {
        pair: sorted({cluster_of(row) for row in rows_by_pair[pair]}) for pair in pairs
    }
    rng = np.random.default_rng(seed)
    draws: list[float] = []
    for _ in range(reps):
        sampled_pairs = rng.choice(pairs, size=len(pairs), replace=True)
        cells = []
        for pair in sampled_pairs:
            clusters = clusters_by_pair[pair]
            if not clusters:
                continue
            picked = rng.choice(clusters, size=len(clusters), replace=True)
            group = []
            for cluster in picked:
                group.extend(row for row in rows_by_pair[pair] if cluster_of(row) == cluster)
            arm_a = cell(row for row in group if arm_of(row) == "A")
            arm_b = cell(row for row in group if arm_of(row) == "B")
            if arm_a.known and arm_b.known:
                cells.append((pair, arm_a.as_tuple(), arm_b.as_tuple()))
        value = miettinen_nurminen([(a, b) for _, a, b in cells])
        if value is not None:
            draws.append(value)
    if not draws:
        return point, float("nan"), float("nan")
    lower, upper = np.percentile(draws, [2.5, 97.5])
    return point, float(lower), float(upper)


def worst_case_rd(group_a: Sequence[Observation], group_b: Sequence[Observation]) -> dict:
    """缺测的最坏情形边界。

    A 的未知全算成功、B 的未知全算失败（最不利 B），以及反过来（最有利 B）。
    两端都在「观测量」上计算，不把未知从分母里丢掉。
    """
    def _range(rows: Sequence[Observation]) -> tuple[float, float]:
        if not rows:
            return float("nan"), float("nan")
        successes = sum(1 for row in rows if row.outcome == SUCCESS)
        unknown = sum(1 for row in rows if row.outcome not in (SUCCESS, FAILURE))
        return successes / len(rows), (successes + unknown) / len(rows)

    a_low, a_high = _range(group_a)
    b_low, b_high = _range(group_b)
    return {
        "a_bounds": (a_low, a_high),
        "b_bounds": (b_low, b_high),
        "rd_min": b_low - a_high,
        "rd_max": b_high - a_low,
    }
