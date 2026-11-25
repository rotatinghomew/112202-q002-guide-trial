"""两版效果：按随机分组解释的意向效应，与实际接触下的可比比较。

随机分配效果保留有效邀请中的未知，并按原随机分组和预约时的分层解释；
实际接触比较还要处理家庭跨场和志愿者共同协助的关联，只在重叠可比条件下
单独说明，不把「接触了某版」当随机。
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from .data import Packet
from .observations import Frame, Observation, build_frame
from .stats import (
    Cell,
    cell,
    cluster_bootstrap_rd,
    miettinen_nurminen,
    missing_bounds,
    newcombe,
    pair_cells,
    permutation_p,
    wilson,
)

HALL_ORDER = ("bronze", "clay")


@dataclass
class ArmSummary:
    edition: str
    assigned: Cell
    assigned_ci: tuple[float, float]
    known_bounds: tuple[float, float]


@dataclass
class StratumRow:
    key: str
    a: Cell
    b: Cell
    a_ci: tuple[float, float]
    b_ci: tuple[float, float]
    diff: float
    diff_ci: tuple[float, float, float]
    bounds_a: tuple[float, float]
    bounds_b: tuple[float, float]


def arm_summary(frame: Frame, edition: str) -> ArmSummary:
    rows = frame.arm(edition)
    c = cell(rows)
    return ArmSummary(
        edition=edition,
        assigned=c,
        assigned_ci=wilson(c.successes, c.known),
        known_bounds=missing_bounds(rows),
    )


def _diff_ci(a: Cell, b: Cell) -> tuple[float, float, float]:
    return newcombe(a.successes, a.known, b.successes, b.known)


def stratum(frame: Frame, key: str, predicate) -> StratumRow:
    rows = [row for row in frame.observations if predicate(row)]
    a_rows = [row for row in rows if row.assigned == "A"]
    b_rows = [row for row in rows if row.assigned == "B"]
    a = cell(a_rows)
    b = cell(b_rows)
    return StratumRow(
        key=key,
        a=a,
        b=b,
        a_ci=wilson(a.successes, a.known),
        b_ci=wilson(b.successes, b.known),
        diff=_diff_ci(a, b)[0],
        diff_ci=_diff_ci(a, b),
        bounds_a=missing_bounds(a_rows),
        bounds_b=missing_bounds(b_rows),
    )


def pair_detail(frame: Frame) -> list[dict]:
    rows = []
    for pair in sorted({row.pair for row in frame.observations}):
        pair_rows = [row for row in frame.observations if row.pair == pair]
        a_rows = [row for row in pair_rows if row.assigned == "A"]
        b_rows = [row for row in pair_rows if row.assigned == "B"]
        a = cell(a_rows)
        b = cell(b_rows)
        rows.append(
            {
                "pair": pair,
                "hall": pair_rows[0].hall,
                "volunteer": pair_rows[0].volunteer,
                "a": a,
                "b": b,
                "diff": _diff_ci(a, b)[0],
                "diff_ci": _diff_ci(a, b),
                "a_bounds": missing_bounds(a_rows),
                "b_bounds": missing_bounds(b_rows),
            }
        )
    return rows


def itt_effect(frame: Frame) -> dict:
    pairs = pair_cells(frame.observations)
    estimate = miettinen_nurminen([(a, b) for _, a, b in pairs])
    point, lower, upper = cluster_bootstrap_rd(
        frame.observations, cluster_of=lambda row: row.household
    )
    return {
        "estimate": estimate,
        "bootstrap_point": point,
        "interval": (lower, upper),
        "permutation_p": permutation_p(pairs),
        "pairs": pairs,
        "n_pairs": len(pairs),
    }


def sensitivity(frame: Frame, packet: Packet) -> list[dict]:
    """改口径后的效应，用来说明缺测与串版各占多大分量。"""
    variants = [
        ("全部有效邀请（主口径）", list(frame.observations)),
        ("剔除有串版接触的观测", [row for row in frame.observations if not row.contaminated]),
        ("仅保留只接触本场分配版", [row for row in frame.observations if row.pure]),
        ("剔除无接触记录的观测", [row for row in frame.observations if not row.no_exposure]),
        ("剔除整户跨版家庭", [row for row in frame.observations if len(row.household_exposure) < 2]),
        (
            "仅用补录前（第 1 批）接触证据",
            list(build_frame(packet, loan_batch=1).observations),
        ),
    ]
    out = []
    for label, rows in variants:
        sub = Frame(observations=rows)
        pairs = pair_cells(rows)
        a = cell(row for row in rows if row.assigned == "A")
        b = cell(row for row in rows if row.assigned == "B")
        out.append(
            {
                "label": label,
                "a": a,
                "b": b,
                "estimate": miettinen_nurminen([(x, y) for _, x, y in pairs]),
                "diff_ci": _diff_ci(a, b),
                "contaminated": sum(row.contaminated for row in rows),
                "unknown": sum(not row.known for row in rows),
                "n": len(rows),
            }
        )
    return out


def late_receipt_impact(packet: Packet) -> dict[int, dict]:
    """补录借阅单前后：串版如何变化、纯版比较会不会改变结论。"""
    results = {}
    for batch in (1, 2):
        built = build_frame(packet, loan_batch=batch)
        pairs = pair_cells(built.observations)
        pure = [row for row in built.observations if row.pure]
        pure_pairs = pair_cells(pure)
        results[batch] = {
            "contaminated": sum(row.contaminated for row in built.observations),
            "no_exposure": sum(row.no_exposure for row in built.observations),
            "effect": miettinen_nurminen([(a, b) for _, a, b in pairs]),
            "pure_effect": miettinen_nurminen([(a, b) for _, a, b in pure_pairs]),
            "pure_a": cell(row for row in pure if row.assigned == "A"),
            "pure_b": cell(row for row in pure if row.assigned == "B"),
            "pure_a_ci": _diff_ci(
                cell(row for row in pure if row.assigned == "A"),
                cell(row for row in pure if row.assigned == "B"),
            ),
        }
    return results


def as_treated(frame: Frame, packet: Packet) -> dict:
    """按实际接触版次比较，仅限只接触单一版次、且场次对可比的条件。

    不把选择某版当随机；按场次对与志愿者分层，按户给区间，
    并列出被排除的场次对与原因。场次对的「可比」由两场实际开放窗口决定，
    不看该场碰巧有多少条记录。
    """
    comparable_pairs = _comparable_pairs(frame, packet)
    excluded = sorted({row.pair for row in frame.observations} - comparable_pairs)
    pool = [
        row
        for row in frame.observations
        if len(row.session_exposure) == 1 and row.pair in comparable_pairs
    ]
    overlap = _overlap(frame, pool, comparable_pairs)

    def arm_of(row: Observation) -> str:
        return next(iter(row.session_exposure))

    pairs = pair_cells(pool, arm_of=arm_of)
    point = miettinen_nurminen([(a, b) for _, a, b in pairs])
    clustered = cluster_bootstrap_rd(pool, cluster_of=lambda row: row.household, arm_of=arm_of)

    detail = []
    for pair in sorted({row.pair for row in pool}):
        a_rows = [row for row in pool if row.pair == pair and arm_of(row) == "A"]
        b_rows = [row for row in pool if row.pair == pair and arm_of(row) == "B"]
        a = cell(a_rows)
        b = cell(b_rows)
        detail.append(
            {
                "pair": pair,
                "a": a,
                "b": b,
                "diff": _diff_ci(a, b)[0],
                "diff_ci": _diff_ci(a, b),
                "volunteer": (a_rows or b_rows)[0].volunteer,
                "overlap": overlap.get(pair, (0, 0)),
            }
        )

    by_volunteer = {}
    for volunteer in sorted({row.volunteer for row in pool}):
        rows = [row for row in pool if row.volunteer == volunteer]
        a = cell(row for row in rows if arm_of(row) == "A")
        b = cell(row for row in rows if arm_of(row) == "B")
        by_volunteer[volunteer] = {
            "a": a,
            "b": b,
            "diff_ci": _diff_ci(a, b),
        }

    return {
        "estimate": point,
        "interval": (clustered[1], clustered[2]),
        "detail": detail,
        "by_volunteer": by_volunteer,
        "excluded_pairs": excluded,
        "excluded_reasons": _exclusion_reasons(frame, packet, excluded),
        "overlap": overlap,
        "n": len(pool),
        "households": len({row.household for row in pool}),
        "note": "按实际接触版次比较，只在非截短、非取消的场次对且只接触单一版次时进行；"
        "同一家庭跨场、同一志愿者跨厅的关联按户聚类；不能当作随机分配效果。",
    }


def _comparable_pairs(frame: Frame, packet: Packet) -> set[str]:
    """两场都有完整观测窗口的场次对，才允许做实际接触比较。"""
    result = set()
    for pair in {row.pair for row in frame.observations}:
        sessions = {
            row.session for row in frame.observations if row.pair == pair
        }
        if not sessions:
            continue
        usable = True
        for session_name in sessions:
            session = packet.sessions[session_name]
            if session.cancelled or session.open_minutes < packet.horizon[session_name]:
                usable = False
        # 被取消的场次即使没有访客记录，也不能算可比。
        for session_name, assigned_pair in packet.pair.items():
            if assigned_pair == pair and packet.sessions[session_name].cancelled:
                usable = False
        if usable and len(sessions) == 2:
            result.add(pair)
    return result


def _exclusion_reasons(frame: Frame, packet: Packet, excluded: list[str]) -> dict[str, list[str]]:
    reasons = {}
    for pair in excluded:
        bits = []
        for session_name, assigned_pair in packet.pair.items():
            if assigned_pair != pair:
                continue
            session = packet.sessions[session_name]
            if session.cancelled:
                bits.append(f"{session_name} 关厅")
            elif session.open_minutes < packet.horizon[session_name]:
                bits.append(
                    f"{session_name} 实开 {session.open_minutes} 分钟 < 观测窗 {packet.horizon[session_name]}"
                )
        reasons[pair] = bits
    return reasons


def _overlap(frame: Frame, pool: list[Observation], pairs: set[str]) -> dict[str, tuple[int, int]]:
    """每个场次对里两版接触者各多少，说明重叠是否足以比较。"""
    out = {}
    for pair in pairs:
        rows = [row for row in pool if row.pair == pair]
        a = sum(1 for row in rows if row.session_exposure == frozenset({"A"}))
        b = sum(1 for row in rows if row.session_exposure == frozenset({"B"}))
        out[pair] = (a, b)
    return out


def protected_layers(frame: Frame, packet: Packet) -> dict[str, dict]:
    """保护层：少儿与不熟悉场馆分别受保护，一家可同时符合两层。

    用预约时的分层标识，不用现场 visitor_profile 替代；不向上合并成馆级。
    实测只能覆盖到有邀请记录的家庭，覆盖不足时明说。
    """
    output = {}
    invited_by_layer: dict[str, set[str]] = defaultdict(set)
    for row in packet.invitations:
        for label in row["prereg_profile"].split(","):
            if label:
                invited_by_layer[label].add(row["household"])

    for layer in ("minor", "unfamiliar"):
        rows = [row for row in frame.observations if layer in row.prereg_profile]
        a_rows = [row for row in rows if row.assigned == "A"]
        b_rows = [row for row in rows if row.assigned == "B"]
        a = cell(a_rows)
        b = cell(b_rows)
        output[layer] = {
            "a": a,
            "b": b,
            "a_ci": wilson(a.successes, a.known),
            "b_ci": wilson(b.successes, b.known),
            "diff_ci": _diff_ci(a, b),
            "bounds_a": missing_bounds(a_rows),
            "bounds_b": missing_bounds(b_rows),
            "households": sorted({row.household for row in rows}),
            "invited_households": sorted(invited_by_layer[layer]),
            "both_layers": sorted(
                {
                    row.household
                    for row in rows
                    if {"minor", "unfamiliar"} <= set(row.prereg_profile)
                }
            ),
        }
    return output
