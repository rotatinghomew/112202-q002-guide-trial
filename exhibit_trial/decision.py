"""续印核算与续印决策。

按拟发安排回答：库存、起印整批、锁期、混用上限、预算，
以及受保护访客「已有证据能保证什么」与「超出支持而须保留旧版什么」。
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from itertools import product

from .data import Packet
from .effects import StratumRow, stratum
from .observations import Frame

PROTECTED_LAYERS = ("minor", "unfamiliar")
OLD, NEW = "A", "B"

# 据以改用新版的证据门槛：每层每臂至少要有这么多「已知结果」。
# 最低增益 6–8 个百分点、最大损失 3–5 个百分点，样本更少时区间宽度
# 主要由小样本决定，不能当作达标或未达标的可靠判断。
MIN_KNOWN_PER_ARM = 10


@dataclass(frozen=True)
class Plan:
    """option -> {edition: households}；同一场次可拆成两版（混用）。"""

    assignment: dict[str, dict[str, int]]

    def primary(self, option: str) -> str | None:
        split = self.assignment.get(option, {})
        if not split:
            return None
        return max(sorted(split), key=lambda edition: split[edition])

    def editions(self, option: str) -> tuple[str, ...]:
        return tuple(sorted(self.assignment.get(option, {})))

    def mixed(self, option: str) -> bool:
        return len(self.assignment.get(option, {})) > 1

    def units(self, option: str) -> int:
        return sum(self.assignment.get(option, {}).values())


def option_totals(packet: Packet) -> dict[str, int]:
    totals: Counter[str] = Counter()
    for row in packet.dispatch_profiles:
        totals[row["option"]] += int(row["households"])
    return dict(totals)


def demand_split(packet: Packet, plan: Plan) -> dict[tuple[str, str], int]:
    """按拟发户数分配语言需求；混用场次把次版户数逐行分配。"""
    demand: Counter[tuple[str, str]] = Counter()
    for option, split in plan.assignment.items():
        rows = [row for row in packet.dispatch_profiles if row["option"] == option]
        remaining = dict(split)
        if sum(remaining.values()) != sum(int(row["households"]) for row in rows):
            raise ValueError(f"{option}: 拟发户数与需求合计不一致")
        ordered = sorted(split, key=lambda edition: (-split[edition], edition))
        for row in rows:
            language = row["language"]
            quantity = int(row["households"])
            for edition in ordered:
                if not quantity:
                    break
                take = min(quantity, remaining.get(edition, 0))
                if take:
                    demand[(edition, language)] += take
                    remaining[edition] -= take
                    quantity -= take
            if quantity:
                raise ValueError(f"{option}/{language}: 拟发户数不足")
    return dict(demand)


def stock_and_orders(packet: Packet) -> tuple[Counter, Counter, int]:
    """返回 (现货 + 已签印量, 已签印量, 已签成本)。

    库存表的 stock 是现货；印单里只有 state=approved 才是已签印量。
    state=notified 只是对外发放承诺，尚未签印，不当作已入库。
    """
    supply: Counter[tuple[str, str]] = Counter()
    signed: Counter[tuple[str, str]] = Counter()
    committed_cost = 0
    for key, row in packet.inventory.items():
        supply[key] += row["stock"]
    for order in packet.print_orders:
        if order["state"] != "approved":
            continue
        key = (order["edition"], order["language"])
        quantity = int(order["qty"])
        supply[key] += quantity
        signed[key] += quantity
        committed_cost += quantity * packet.inventory[key]["unit_cost"]
    return supply, signed, committed_cost


def procurement(packet: Packet, demand: dict[tuple[str, str], int]) -> dict:
    supply, signed, committed_cost = stock_and_orders(packet)
    lines = []
    new_cost = waste = 0
    for key in sorted(set(supply) | set(demand)):
        need = demand.get(key, 0)
        available = supply.get(key, 0)
        shortfall = max(0, need - available)
        info = packet.inventory[key]
        packs = (shortfall + info["pack_size"] - 1) // info["pack_size"] if shortfall else 0
        quantity = packs * info["pack_size"]
        cost = quantity * info["unit_cost"]
        new_cost += cost
        waste += max(0, quantity - shortfall)
        lines.append(
            {
                "key": key,
                "demand": need,
                "available": available,
                "shortfall": shortfall,
                "pack_size": info["pack_size"],
                "packs": packs,
                "order_qty": quantity,
                "unit_cost": info["unit_cost"],
                "cost": cost,
                "waste": max(0, quantity - shortfall),
                "lock_day": info["lock_day"],
                "ready_day": info["ready_day"],
            }
        )
    return {
        "lines": lines,
        "committed_cost": committed_cost,
        "new_cost": new_cost,
        "total_cost": committed_cost + new_cost,
        "waste": waste,
        "signed": {key: value for key, value in signed.items() if value},
        "supply": dict(supply),
    }


def option_layers(packet: Packet) -> dict[str, dict[str, int]]:
    """每个选项各保护层的家庭数；两层可重叠，但每层各自计数。"""
    result: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for row in packet.dispatch_profiles:
        labels = {part.strip() for part in row["profiles"].split(",") if part.strip()}
        for layer in PROTECTED_LAYERS:
            if layer in labels:
                result[row["option"]][layer] += int(row["households"])
        result[row["option"]]["total"] += int(row["households"])
    return {option: dict(values) for option, values in result.items()}


def layer_overlap(packet: Packet) -> dict[str, dict[str, int]]:
    """两层直接相加与去重后的并集，用来说明不能重复计需求。"""
    out = {}
    for option in packet.dispatch:
        rows = [row for row in packet.dispatch_profiles if row["option"] == option]
        minor = sum(int(r["households"]) for r in rows if "minor" in r["profiles"].split(","))
        unfamiliar = sum(
            int(r["households"]) for r in rows if "unfamiliar" in r["profiles"].split(",")
        )
        both = sum(
            int(r["households"])
            for r in rows
            if {"minor", "unfamiliar"} <= set(r["profiles"].split(","))
        )
        union = sum(
            int(r["households"]) for r in rows if set(r["profiles"].split(",")) - {"all"}
        )
        total = sum(int(r["households"]) for r in rows)
        out[option] = {
            "minor": minor,
            "unfamiliar": unfamiliar,
            "both": both,
            "naive_sum": minor + unfamiliar,
            "union": union,
            "other": total - union,
            "total": total,
            "double_counted": minor + unfamiliar - union,
        }
    return out


@dataclass
class CoverageRow:
    option: str
    hall: str
    edition: str
    mixed: bool
    layers: dict[str, int]
    guaranteed: tuple[str, ...]
    may_contact: tuple[str, ...]
    old_retained: bool
    reason: str


def coverage(packet: Packet, plan: Plan) -> list[CoverageRow]:
    """每个拟发场次下，受保护访客可能接触到的版次范围。

    合用服务台、相邻场次、同场混用都会扩大可能接触的版次；
    只有证据覆盖到的部分才写「保证」，超出部分须保留旧版。
    """
    options = packet.option_map()
    layers = option_layers(packet)
    by_desk: dict[str, list[str]] = defaultdict(list)
    for name, row in packet.dispatch.items():
        by_desk[row["desk"]].append(name)

    rows = []
    for option_name, split in plan.assignment.items():
        if option_name not in packet.dispatch:
            continue
        option = options[option_name]
        dispatch = packet.dispatch[option_name]
        editions = set(split)
        peers = [peer for peer in by_desk[dispatch["desk"]] if peer != option_name]
        peer_editions = set()
        for peer in peers:
            peer_split = plan.assignment.get(peer)
            if peer_split:
                peer_editions.update(peer_split)
            elif peer in options and options[peer].committed_edition:
                peer_editions.add(options[peer].committed_edition)
        old_on_desk = dispatch["old_on_desk"] == "yes"
        if len(editions) > 1:
            guaranteed: tuple[str, ...] = tuple()
            reason = "同一场次拆两版，受保护访客可能拿到任一版"
        elif editions == {OLD}:
            guaranteed = (OLD,)
            reason = "本场只发旧版"
        elif old_on_desk:
            guaranteed = tuple()
            reason = "本场发新版，但合用服务台仍摆旧版，可能拿错"
        elif OLD in peer_editions:
            guaranteed = tuple()
            reason = "本场发新版，但同一服务台其他场次发旧版，接触组合不纯"
        else:
            guaranteed = tuple(sorted(editions))
            reason = "本场发新版，服务台无旧版，且无相邻旧版"
        may_contact = set(editions)
        if old_on_desk or OLD in peer_editions:
            may_contact.add(OLD)
        rows.append(
            CoverageRow(
                option=option_name,
                hall=option.hall,
                edition=plan.primary(option_name) or "-",
                mixed=len(editions) > 1,
                layers=layers.get(option_name, {}),
                guaranteed=guaranteed,
                may_contact=tuple(sorted(may_contact)),
                old_retained=OLD in may_contact,
                reason=reason,
            )
        )
    return rows


def mixed_shares(packet: Packet, plan: Plan) -> dict[tuple[str, str], dict[str, float]]:
    """各馆各保护层中，混用场次涉及家庭的占比区间。

    最不利（upper）：混用场次里该层全部家庭都可能接触另一版；
    最有利（lower）：只有拿到少数版的那部分家庭算接触另一版。
    受保护家庭具体拿到哪一版无法预知，因此两种边界都要报。
    """
    layers = option_layers(packet)
    mixed_upper: Counter[tuple[str, str]] = Counter()
    mixed_lower: Counter[tuple[str, str]] = Counter()
    totals: Counter[tuple[str, str]] = Counter()
    for option, split in plan.assignment.items():
        if option not in packet.dispatch:
            continue
        hall = packet.option_map()[option].hall
        for layer in PROTECTED_LAYERS:
            totals[(hall, layer)] += layers.get(option, {}).get(layer, 0)
        if len(split) < 2:
            continue
        minority = min(split.values())
        for layer in PROTECTED_LAYERS:
            count = layers.get(option, {}).get(layer, 0)
            if count:
                mixed_upper[(hall, layer)] += count
                mixed_lower[(hall, layer)] += min(minority, count)
    return {
        key: {
            "upper": mixed_upper[key] / totals[key] if totals[key] else 0.0,
            "lower": mixed_lower[key] / totals[key] if totals[key] else 0.0,
            "units_upper": mixed_upper[key],
            "units_lower": mixed_lower[key],
            "total": totals[key],
        }
        for key in totals
    }


@dataclass
class LayerVerdict:
    hall: str
    layer: str
    min_gain: float | None
    max_loss: float | None
    evidence: StratumRow | None
    exposure: int
    total: int
    mixed_share: float | None
    mixed_ceiling: float | None
    mixed_share_lower: float | None = None
    problems: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def supported(self) -> bool:
        return not self.problems

    @property
    def newly_exposed(self) -> bool:
        return self.exposure > 0

    @property
    def evidence_strength(self) -> str:
        if self.evidence is None:
            return "无"
        known = self.evidence.a.known + self.evidence.b.known
        if known < 10:
            return "极薄"
        if known < 30:
            return "偏薄"
        return "可用"


def _layer_evidence(frame: Frame, hall: str, layer: str) -> StratumRow:
    return stratum(
        frame,
        f"{hall}/{layer}",
        lambda row, hall=hall, layer=layer: row.hall == hall and layer in row.prereg_profile,
    )


def _group_evidence(frame: Frame, hall: str, group: str) -> StratumRow:
    return stratum(
        frame,
        f"{hall}/{group}",
        lambda row, hall=hall, group=group: row.hall == hall and row.visitor_profile == group,
    )


def _check_gain_loss(verdict: LayerVerdict) -> None:
    evidence = verdict.evidence
    if evidence is None or evidence.a.known == 0 or evidence.b.known == 0:
        verdict.problems.append("两层没有可比较的已知结果，不能据以改用新版")
        return
    if min(evidence.a.known, evidence.b.known) < MIN_KNOWN_PER_ARM:
        verdict.problems.append(
            f"证据量不足：已知结果 A {evidence.a.known}、B {evidence.b.known}，"
            f"低于每臂 {MIN_KNOWN_PER_ARM} 条的证据门槛，不能据以改用新版"
        )
    _, lower, _ = evidence.diff_ci
    if verdict.min_gain is not None and lower < verdict.min_gain:
        verdict.problems.append(f"增益下限 {lower:+.1%} 未达最低增益 {verdict.min_gain:.0%}")
    if verdict.max_loss is not None and lower < -verdict.max_loss:
        verdict.problems.append(f"损失上限 {-lower:+.1%} 可能超过最大损失 {verdict.max_loss:.0%}")


def exposure_by_layer(packet: Packet, plan: Plan) -> dict[tuple[str, str], int]:
    """拟发安排下，各馆各保护层有多少家庭可能接触新版。

    合用服务台的场次只要有一场发新版，同台相邻场次的受保护家庭也可能
    拿到新版（与试印时不同的接触组合），因此按整张服务台计，而不是只看本场。
    """
    options = packet.option_map()
    layers = option_layers(packet)
    exposed: Counter[tuple[str, str]] = Counter()
    desk_new: dict[tuple[str, str], bool] = {}
    for option, split in plan.assignment.items():
        if option not in packet.dispatch:
            continue
        key = (options[option].hall, packet.dispatch[option]["desk"])
        desk_new[key] = desk_new.get(key, False) or NEW in split
    for option, split in plan.assignment.items():
        if option not in packet.dispatch:
            continue
        hall = options[option].hall
        key = (hall, packet.dispatch[option]["desk"])
        if not desk_new.get(key, False):
            continue
        for layer in PROTECTED_LAYERS:
            count = layers.get(option, {}).get(layer, 0)
            if count:
                exposed[(hall, layer)] += count
    return dict(exposed)


def hall_new_exposure(packet: Packet, plan: Plan) -> dict[str, int]:
    """各馆在拟发安排下可能接触新版的人数（按服务台传播）。"""
    exposed = exposure_by_layer(packet, plan)
    out: Counter[str] = Counter()
    for (hall, _layer), count in exposed.items():
        out[hall] = max(out[hall], count)
    return dict(out)


def evaluate_guardrails(packet: Packet, frame: Frame, plan: Plan) -> dict:
    """逐层评估护栏；只用该层自己的证据，不拿全厅区间替某一层盖章。"""
    shares = mixed_shares(packet, plan)
    exposed = exposure_by_layer(packet, plan)
    hall_exposure = hall_new_exposure(packet, plan)
    layers = option_layers(packet)

    verdicts: dict[tuple[str, str], LayerVerdict] = {}
    for row in packet.guardrails:
        hall, layer = row["hall"], row["layer"]
        total = sum(
            values.get(layer, 0)
            for option, values in layers.items()
            if option in packet.dispatch and packet.option_map()[option].hall == hall
        )
        share = shares.get((hall, layer), {"upper": 0.0, "lower": 0.0})
        verdict = LayerVerdict(
            hall=hall,
            layer=layer,
            min_gain=float(row["min_gain"]),
            max_loss=float(row["max_loss"]),
            evidence=_layer_evidence(frame, hall, layer),
            exposure=exposed.get((hall, layer), 0),
            total=total,
            mixed_share=share["upper"],
            mixed_ceiling=float(row["mixed_ceiling"]),
            mixed_share_lower=share["lower"],
        )
        if verdict.newly_exposed:
            _check_gain_loss(verdict)
        if verdict.mixed_ceiling is not None and verdict.mixed_share_lower > verdict.mixed_ceiling:
            verdict.problems.append(
                f"混用占比 {verdict.mixed_share_lower:.1%}–{verdict.mixed_share:.1%} "
                f"超过上限 {verdict.mixed_ceiling:.0%}"
            )
        elif verdict.mixed_ceiling is not None and verdict.mixed_share > verdict.mixed_ceiling:
            verdict.notes.append(
                f"混用占比在 {verdict.mixed_share_lower:.1%}–{verdict.mixed_share:.1%} 之间，"
                f"最不利情形越过上限 {verdict.mixed_ceiling:.0%}，须按最不利准备"
            )
        verdicts[(hall, layer)] = verdict

    groups: dict[tuple[str, str], LayerVerdict] = {}
    for row in packet.printing:
        hall, group = row["hall"], row["group"]
        pair_rows = [
            obs
            for obs in frame.observations
            if obs.hall == hall and obs.visitor_profile == group and not obs.no_exposure
        ]
        contamination = (
            sum(obs.contaminated for obs in pair_rows) / len(pair_rows) if pair_rows else 0.0
        )
        verdict = LayerVerdict(
            hall=hall,
            layer=group,
            min_gain=float(row["min_gain"]),
            max_loss=float(row["max_loss"]),
            evidence=_group_evidence(frame, hall, group),
            exposure=hall_exposure.get(hall, 0),
            total=len(pair_rows),
            mixed_share=contamination,
            mixed_ceiling=float(row["contamination_max"]),
        )
        if verdict.newly_exposed:
            _check_gain_loss(verdict)
        if verdict.mixed_ceiling is not None and contamination > verdict.mixed_ceiling:
            verdict.problems.append(
                f"实际串版比例 {contamination:.1%} 超过上限 {verdict.mixed_ceiling:.0%}"
            )
        groups[(hall, group)] = verdict

    # 串版上限要拿「有接触记录的观测」作分母；没有接触记录的不能算没串版。
    observed_contamination = {}
    for row in packet.printing:
        pair_rows = [
            obs
            for obs in frame.observations
            if obs.hall == row["hall"]
            and obs.visitor_profile == row["group"]
            and not obs.no_exposure
        ]
        contaminated = sum(obs.contaminated for obs in pair_rows)
        observed_contamination[(row["hall"], row["group"])] = (
            contaminated,
            len(pair_rows),
            contaminated / len(pair_rows) if pair_rows else 0.0,
        )
    return {
        "layers": verdicts,
        "groups": groups,
        "observed_contamination": observed_contamination,
    }


def adjacency(packet: Packet, plan: Plan) -> list[dict]:
    """同厅相邻两场异版时，检查间隔是否满足 flush。"""
    conflicts = []
    groups: dict[tuple[str, int], list] = defaultdict(list)
    for option in packet.next_sessions:
        groups[(option.hall, option.day)].append(option)
    def editions_of(option) -> set[str]:
        planned = set(plan.assignment.get(option.option, {}))
        if planned:
            return planned
        return {option.committed_edition} if option.committed_edition else set()

    for (hall, day), options in groups.items():
        ordered = sorted(options, key=lambda option: option.start)
        for left, right in zip(ordered, ordered[1:]):
            left_editions = editions_of(left)
            right_editions = editions_of(right)
            if not left_editions or not right_editions:
                continue
            if left_editions == right_editions:
                continue
            gap = right.start - left.end
            required = max(left.flush, right.flush)
            if gap < required:
                conflicts.append(
                    {
                        "hall": hall,
                        "day": day,
                        "left": left.option,
                        "right": right.option,
                        "left_edition": "+".join(sorted(left_editions)),
                        "right_edition": "+".join(sorted(right_editions)),
                        "gap": gap,
                        "required": required,
                    }
                )
    return conflicts


def commitment_check(packet: Packet, plan: Plan) -> list[str]:
    """已通知的发放承诺不能撤；检查拟发安排是否覆盖。"""
    problems = []
    for order in packet.print_orders:
        option, edition = order["option"], order["edition"]
        quantity = int(order["qty"])
        served = plan.assignment.get(option, {}).get(edition, 0)
        if order["state"] == "notified" and served < quantity:
            problems.append(
                f"{option} 已通知 {quantity} 户领取 {edition}（{order['order']}），拟发仅 {served} 户"
            )
    return problems


def lock_feasibility(packet: Packet, plan: Plan, today: int = 1) -> list[dict]:
    """锁期前可以比较的「实际可签印数」：整批追加必须在锁期当天或之前下单。

    若最低起印量超过后来还有依据的人数，允许不追加；
    但已通知的承诺不因锁期而撤销。
    """
    demand = demand_split(packet, plan)
    purchase = procurement(packet, demand)
    rows = []
    for line in purchase["lines"]:
        if not line["packs"]:
            continue
        lock_day = line["lock_day"]
        rows.append(
            {
                "key": line["key"],
                "shortfall": line["shortfall"],
                "order_qty": line["order_qty"],
                "packs": line["packs"],
                "cost": line["cost"],
                "lock_day": lock_day,
                "today": today,
                "placeable": today <= lock_day,
                "note": (
                    f"需在锁期第 {lock_day} 天前签 {line['packs']} 批；"
                    + ("可签" if today <= lock_day else "锁期已过，不能新签整批")
                ),
            }
        )
    return rows


def evaluate_plan(packet: Packet, frame: Frame, plan: Plan, today: int = 1) -> dict:
    demand = demand_split(packet, plan)
    purchase = procurement(packet, demand)
    # 已签印量在库的短缺不算「待签整批」；只有真正新下的整批才受锁期约束。
    lock_rows = lock_feasibility(packet, plan, today=today)
    guardrails = evaluate_guardrails(packet, frame, plan)
    conflicts = adjacency(packet, plan)
    commitments = commitment_check(packet, plan)
    budget = min(int(row["budget"]) for row in packet.guardrails)
    # budget 约束「整份追加采购」：只比新下的整批印单，已签印单是既有承诺。
    over_budget = purchase["new_cost"] > budget
    # 已签却用不上的整批不能从账上抹去，单独保留在 committed_cost / waste 里。
    unabsorbed = purchase["committed_cost"]
    layer_problems = {
        f"layer:{key[0]}/{key[1]}": verdict.problems
        for key, verdict in guardrails["layers"].items()
        if verdict.problems
    }
    group_problems = {
        f"group:{key[0]}/{key[1]}": verdict.problems
        for key, verdict in guardrails["groups"].items()
        if verdict.problems
    }
    layer_notes = {
        f"layer:{key[0]}/{key[1]}": verdict.notes
        for key, verdict in guardrails["layers"].items()
        if verdict.notes
    }
    return {
        "plan": plan,
        "demand": demand,
        "purchase": purchase,
        "coverage": coverage(packet, plan),
        "guardrails": guardrails,
        "layer_problems": layer_problems,
        "group_problems": group_problems,
        "layer_notes": layer_notes,
        "adjacency": conflicts,
        "commitments": commitments,
        "budget": budget,
        "over_budget": over_budget,
        "unabsorbed_cost": unabsorbed,
        "lock": lock_rows,
        "lock_blocked": [row for row in lock_rows if not row["placeable"]],
        "feasible": not conflicts
        and not commitments
        and not over_budget
        and not any(not row["placeable"] for row in lock_rows),
    }


def candidate_plans(packet: Packet) -> list[Plan]:
    """按已承诺版次与场次相邻关系生成候选。

    只有需求表里列出的场次才有户数可核；未列出的场次不能凭库存硬发。
    F1 固定 A；F1 结束即 F2 开始、间隔 0 < flush，故 F2 也须 A。
    F3、F4 可选；F4 有 6 户已通知领取 B，允许「6 户 B + 6 户 A」混用。
    """
    totals = option_totals(packet)
    plans = []
    for f3, f4 in product((OLD, NEW), (OLD, NEW, "mixed")):
        assignment = {
            "F1": {OLD: totals["F1"]},
            "F2": {OLD: totals["F2"]},
            "F3": {f3: totals["F3"]},
            "F4": {NEW: 6, OLD: totals["F4"] - 6} if f4 == "mixed" else {f4: totals["F4"]},
        }
        plans.append(Plan(assignment))
    return plans


@dataclass
class OrderAction:
    order: str
    option: str
    edition: str
    language: str
    qty: int
    state: str
    disposition: str
    destination: str
    lock_day: int
    note: str


def order_actions(packet: Packet, plan: Plan) -> list[OrderAction]:
    """每张已签印单在当前拟发安排下的去向。

    - 拟发仍要该版该场 → 兑现；
    - 该场改版或不再发 → 只能在有依据的场次改用途，否则沉没；
    - 已通知的承诺不能撤。
    """
    totals = option_totals(packet)
    demand = demand_split(packet, plan)
    supply, signed, _ = stock_and_orders(packet)
    actions = []
    for order in packet.print_orders:
        option, edition = order["option"], order["edition"]
        language = order["language"]
        quantity = int(order["qty"])
        key = (edition, language)
        lock_day = packet.inventory[key]["lock_day"]
        plan_split = plan.assignment.get(option, {})
        served = plan_split.get(edition, 0)
        if served >= quantity:
            actions.append(
                OrderAction(
                    order["order"], option, edition, language, quantity, order["state"],
                    "兑现", option, lock_day, "拟发安排仍在该场发放该版",
                )
            )
            continue
        # 还能吸收同版同语种的其他场次需求。
        other_need = sum(
            count
            for (candidate_edition, candidate_language), count in demand.items()
            if (candidate_edition, candidate_language) == key
        )
        spare = max(0, other_need - quantity)
        if order["state"] == "notified":
            actions.append(
                OrderAction(
                    order["order"], option, edition, language, quantity, order["state"],
                    "必须兑现", option, lock_day,
                    f"已通知 {quantity} 户，不能撤销；拟发仅 {served} 户，缺口 {quantity - served} 户",
                )
            )
        elif other_need >= quantity:
            actions.append(
                OrderAction(
                    order["order"], option, edition, language, quantity, order["state"],
                    "改去向", _destination_for(packet, plan, key),
                    lock_day,
                    f"原场拟发仅 {served} 户；同版同语种有依据需求 {other_need} 户，可在锁期 {lock_day} 前改配",
                )
            )
        elif other_need > 0:
            actions.append(
                OrderAction(
                    order["order"], option, edition, language, quantity, order["state"],
                    "部分改去向", _destination_for(packet, plan, key),
                    lock_day,
                    f"原场拟发仅 {served} 户；同版同语种有依据需求仅 {other_need} 户，"
                    f"其余 {quantity - other_need} 册无有依据去向",
                )
            )
        else:
            actions.append(
                OrderAction(
                    order["order"], option, edition, language, quantity, order["state"],
                    "沉没", "-", lock_day,
                    f"原场拟发仅 {served} 户，且无其他有依据的同版需求；成本不能从账上抹去",
                )
            )
    return actions


def _destination_for(packet: Packet, plan: Plan, key: tuple[str, str]) -> str:
    demand = demand_split(packet, plan)
    options = []
    for option, split in plan.assignment.items():
        if key[0] in split:
            options.append(option)
    return ",".join(sorted(options)) or "-"


def new_print_stop(packet: Packet, plan: Plan) -> list[dict]:
    """未签印数：按当前证据与预算应当停下的追加整批。"""
    demand = demand_split(packet, plan)
    purchase = procurement(packet, demand)
    budget = min(int(row["budget"]) for row in packet.guardrails)
    stops = []
    for line in purchase["lines"]:
        if not line["packs"]:
            continue
        over = purchase["new_cost"] > budget
        stops.append(
            {
                "key": line["key"],
                "shortfall": line["shortfall"],
                "pack_size": line["pack_size"],
                "packs": line["packs"],
                "cost": line["cost"],
                "lock_day": line["lock_day"],
                "reason": "整份追加采购成本已超预算" if over else "该版没有通过保护层护栏的证据支持",
            }
        )
    return stops


def issuance_actions(packet: Packet, frame: Frame, plan: Plan) -> dict:
    """补交一张借阅单后：存量、已下单、未签印数分别怎么处理。"""
    from .effects import late_receipt_impact

    supply, signed, committed_cost = stock_and_orders(packet)
    return {
        "supply": dict(supply),
        "signed": dict(signed),
        "committed_cost": committed_cost,
        "orders": order_actions(packet, plan),
        "stop_new": new_print_stop(packet, plan),
        "late": late_receipt_impact(packet),
        "mixed_households": sorted(
            {row.household for row in frame.observations if len(row.household_exposure) >= 2}
        ),
        "flipped_households": sorted(
            {
                row.household
                for row in frame.observations
                if row.batch1_exposure != row.session_exposure
            }
        ),
    }


def uncommitted_new_units(packet: Packet, plan: Plan) -> int:
    """超出已通知承诺、又没有证据支持的新版户数。"""
    notified: Counter[str] = Counter()
    for order in packet.print_orders:
        if order["state"] == "notified" and order["edition"] == NEW:
            notified[order["option"]] += int(order["qty"])
    total = 0
    for option, split in plan.assignment.items():
        total += max(0, split.get(NEW, 0) - notified.get(option, 0))
    return total


def select_plan(packet: Packet, frame: Frame, today: int = 1) -> dict:
    """在候选安排里按「先守承诺与印务约束，再少发无依据的新版」排序。

    现有证据不足以支持任何保护层改用新版，因此在不撤承诺的前提下，
    超出已通知承诺的新版户数应当最小。
    """
    evaluated = [
        evaluate_plan(packet, frame, plan, today=today) for plan in candidate_plans(packet)
    ]
    for result in evaluated:
        result["uncommitted_new"] = uncommitted_new_units(packet, result["plan"])
        result["score"] = (
            len(result["commitments"]),
            len(result["adjacency"]),
            result["over_budget"],
            result["uncommitted_new"],
            sum(len(problems) for problems in result["layer_problems"].values())
            + sum(len(problems) for problems in result["group_problems"].values()),
            result["purchase"]["new_cost"],
        )
    return {"candidates": evaluated, "best": min(evaluated, key=lambda item: item["score"])}
