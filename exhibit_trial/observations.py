"""构建「家庭 × 场次」观测，并按规定口径判定成功、失败与未知。

观测单位：一个家庭在一场内的首次有效找寻机会。重复补记不是另一位访客；
同一家庭跨场出现则各自成为一场内的观测，户级关联留到不确定性估计时处理。
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Iterable

from .data import Packet, Session

SUCCESS = "success"
FAILURE = "failure"
UNKNOWN = "unknown"

# 未知原因
CANCELLED = "cancelled"
NO_RECORD = "no_record"
MISSING_RESULT = "missing_result"
TRUNCATED = "truncated"
AFTER_CLOSE = "after_close"
NO_WINDOW = "no_window"

# 已知原因
FOUND = "found"
FOUND_LATE = "found_late"
ASSISTED = "assisted"
NOT_FOUND = "not_found"


@dataclass(frozen=True)
class Observation:
    household: str
    session: str
    assigned: str
    hall: str
    pair: str
    volunteer: str
    horizon: int
    open_minutes: int
    visitor_profile: str
    prereg_profile: tuple[str, ...]
    source: str
    opportunity: int | None
    result: str
    minute: int | None
    outcome: str
    reason: str
    session_exposure: frozenset[str]
    batch1_exposure: frozenset[str]
    household_exposure: frozenset[str] = frozenset()

    @property
    def known(self) -> bool:
        return self.outcome in (SUCCESS, FAILURE)

    @property
    def contaminated(self) -> bool:
        """按实际接触判断：接触过非本场分配版次。"""
        return bool(self.session_exposure - {self.assigned})

    @property
    def pure(self) -> bool:
        """只接触过本场分配版次；没有接触记录不算纯版。"""
        return self.session_exposure == frozenset({self.assigned})

    @property
    def no_exposure(self) -> bool:
        return not self.session_exposure

    def exposed_only(self, edition: str) -> bool:
        return self.session_exposure == frozenset({edition})


@dataclass
class Frame:
    observations: list[Observation]
    excluded_not_entered: list[dict[str, str]] = field(default_factory=list)
    duplicate_visit_rows: int = 0
    cancelled_sessions: list[str] = field(default_factory=list)
    loan_batch: int = 2

    def arm(self, edition: str) -> list[Observation]:
        return [o for o in self.observations if o.assigned == edition]

    def subset(self, predicate) -> "Frame":
        return Frame(
            observations=[o for o in self.observations if predicate(o)],
            excluded_not_entered=self.excluded_not_entered,
            duplicate_visit_rows=self.duplicate_visit_rows,
            cancelled_sessions=self.cancelled_sessions,
            loan_batch=self.loan_batch,
        )


def classify_outcome(
    session: Session,
    result: str,
    opportunity: int | None,
    minute: int | None,
    horizon: int,
) -> tuple[str, str]:
    """返回 (outcome, reason)。

    - 窗口内独立找到 → 成功；
    - 先求助 → 失败（求助本身已表明未能独立找到）；
    - 足窗仍未找到 → 失败；
    - 关厅、窗口不足等提前截尾、缺交班结果、未到场 → 未知。
    """
    if session.cancelled:
        return UNKNOWN, CANCELLED
    if result == "no-visit":
        return UNKNOWN, NO_RECORD
    if opportunity is None:
        return UNKNOWN, NO_RECORD
    if result == "missing":
        return UNKNOWN, MISSING_RESULT
    if minute is None:
        return UNKNOWN, MISSING_RESULT

    available = session.open_minutes - opportunity
    if available <= 0:
        return UNKNOWN, NO_WINDOW
    effective = min(available, horizon)
    elapsed = minute - opportunity

    if result == "assisted":
        # 求助发生在观测到的时候；即便窗口被截短，也已知“没有自己找到”。
        return FAILURE, ASSISTED

    if result == "independent":
        if minute > session.open_minutes:
            # 记录时间超过实开时长，窗口被截短，不能据此判定成功或失败。
            return UNKNOWN, AFTER_CLOSE
        if elapsed <= effective:
            return SUCCESS, FOUND
        return FAILURE, FOUND_LATE

    if result == "not-found":
        if available >= horizon:
            return FAILURE, NOT_FOUND
        return UNKNOWN, TRUNCATED

    raise ValueError(f"未知的 result: {result}")


def _first_opportunity(rows: list[dict[str, str]]) -> tuple[dict[str, str], int]:
    """同一家庭同一场次只保留首次有效机会；重复补记只计一次。"""
    ordered = sorted(rows, key=lambda row: (int(row["opportunity"]), row["visit"]))
    return ordered[0], len(ordered) - 1


def _prereg_labels(packet: Packet) -> dict[str, tuple[str, ...]]:
    labels: dict[str, set[str]] = defaultdict(set)
    for row in packet.invitations:
        for label in row["prereg_profile"].split(","):
            if label.strip():
                labels[row["household"]].add(label.strip())
    return {household: tuple(sorted(values)) for household, values in labels.items()}


def build_frame(packet: Packet, loan_batch: int = 2) -> Frame:
    """构建观测框。loan_batch 控制纳入到第几批借阅补记。"""
    prereg = _prereg_labels(packet)
    grouped: dict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)
    for row in packet.visits:
        grouped[(row["household"], row["session"])].append(row)

    # 每场全部接触记录（含重复补记），用于串版识别；观测本身只看首次机会。
    visit_versions: dict[tuple[str, str], set[str]] = defaultdict(set)
    for row in packet.visits:
        if row["actual"] in ("A", "B"):
            visit_versions[(row["household"], row["session"])].add(row["actual"])
    loan_versions: dict[tuple[str, str], set[str]] = defaultdict(set)
    batch1_versions: dict[tuple[str, str], set[str]] = defaultdict(set)
    for row in packet.loans:
        key = (row["household"], row["session"])
        if int(row["received_batch"]) <= loan_batch:
            loan_versions[key].add(row["version"])
        if int(row["received_batch"]) <= 1:
            batch1_versions[key].add(row["version"])

    observations: list[Observation] = []
    duplicates = 0
    observed_keys: set[tuple[str, str]] = set()

    for key, rows in grouped.items():
        first, extra = _first_opportunity(rows)
        duplicates += extra
        household, session_name = key
        observed_keys.add(key)
        session = packet.sessions[session_name]
        outcome, reason = classify_outcome(
            session,
            first["result"],
            int(first["opportunity"]),
            int(first["minute"]),
            packet.horizon[session_name],
        )
        observations.append(
            Observation(
                household=household,
                session=session_name,
                assigned=packet.assigned[session_name],
                hall=packet.hall[session_name],
                pair=packet.pair[session_name],
                volunteer=session.volunteer,
                horizon=packet.horizon[session_name],
                open_minutes=session.open_minutes,
                visitor_profile=first["profile"],
                prereg_profile=prereg.get(household, ()),
                source="visit",
                opportunity=int(first["opportunity"]),
                result=first["result"],
                minute=int(first["minute"]),
                outcome=outcome,
                reason=reason,
                session_exposure=frozenset(visit_versions[key] | loan_versions[key]),
                batch1_exposure=frozenset(visit_versions[key] | batch1_versions[key]),
            )
        )

    excluded: list[dict[str, str]] = []
    for row in packet.invitations:
        key = (row["household"], row["session"])
        if row["entered"] != "yes":
            # 未实到不属于有效邀请，排除而不是当成找错。
            excluded.append(row)
            continue
        if key in observed_keys:
            continue
        # 有效邀请但现场没有记录：保留为未知，不当成找错。
        session = packet.sessions[row["session"]]
        observations.append(
            Observation(
                household=row["household"],
                session=row["session"],
                assigned=packet.assigned[row["session"]],
                hall=packet.hall[row["session"]],
                pair=packet.pair[row["session"]],
                volunteer=session.volunteer,
                horizon=packet.horizon[row["session"]],
                open_minutes=session.open_minutes,
                visitor_profile=row["prereg_profile"],
                prereg_profile=prereg.get(row["household"], ()),
                source="invitation",
                opportunity=None,
                result="no-visit",
                minute=None,
                outcome=UNKNOWN,
                reason=NO_RECORD,
                session_exposure=frozenset(loan_versions[key]),
                batch1_exposure=frozenset(batch1_versions[key]),
            )
        )

    # 户级接触：跨场 + 全部借阅补记。用于实接触比较与「删串版家庭」情景。
    household_versions: dict[str, set[str]] = defaultdict(set)
    for (household, _session), versions in visit_versions.items():
        household_versions[household].update(versions)
    for row in packet.loans:
        if int(row["received_batch"]) <= loan_batch:
            household_versions[row["household"]].add(row["version"])
    observations = [
        Observation(**{**obs.__dict__, "household_exposure": frozenset(household_versions[obs.household])})
        for obs in observations
    ]

    cancelled = [name for name, session in packet.sessions.items() if session.cancelled]
    return Frame(
        observations=sorted(observations, key=lambda o: (o.pair, o.session, o.household)),
        excluded_not_entered=excluded,
        duplicate_visit_rows=duplicates,
        cancelled_sessions=sorted(cancelled),
        loan_batch=loan_batch,
    )


def counts_by(observations: Iterable[Observation], key) -> dict[str, int]:
    result: dict[str, int] = defaultdict(int)
    for obs in observations:
        result[key(obs)] += 1
    return dict(result)
