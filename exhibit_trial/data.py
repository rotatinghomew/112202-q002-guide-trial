"""读取试印包内的业务记录，并保留表间引用关系。

所有表均为 UTF-8、Tab 分隔；``-`` 表示不适用，``?`` 表示未取得，均不当作零。
标识符只在各表声明的归属范围内有效。
"""
from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from pathlib import Path


def _validate(folder: Path, layout: dict[str, list[str]]) -> dict[str, int]:
    sizes: dict[str, int] = {}
    for filename, fields in layout.items():
        with (folder / filename).open(newline="", encoding="utf-8") as source:
            reader = csv.reader(source, delimiter="\t", strict=True)
            if next(reader, None) != fields:
                raise ValueError(f"{filename}: 表头不符")
            count = 0
            for line, values in enumerate(reader, start=2):
                if len(values) != len(fields) or any(not value.strip() for value in values):
                    raise ValueError(f"{filename}:{line}: 列数不符或有未标记的空值")
                count += 1
        sizes[filename] = count
    return sizes


def _read(folder: Path, filename: str) -> list[dict[str, str]]:
    with (folder / filename).open(newline="", encoding="utf-8") as source:
        return list(csv.DictReader(source, delimiter="\t"))


def _split(value: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in value.split(",") if part.strip())


@dataclass(frozen=True)
class Session:
    session: str
    start: int
    open_minutes: int
    volunteer: str
    status: str

    @property
    def cancelled(self) -> bool:
        return self.status == "cancelled"


@dataclass(frozen=True)
class Assignment:
    pair: str
    hall: str
    left: str
    right: str
    left_edition: str
    right_edition: str
    horizon: int

    def editions(self) -> dict[str, str]:
        return {self.left: self.left_edition, self.right: self.right_edition}


@dataclass(frozen=True)
class Option:
    option: str
    hall: str
    day: int
    start: int
    length: int
    volunteer: str
    fixed: str
    flush: int

    @property
    def end(self) -> int:
        return self.start + self.length

    @property
    def committed_edition(self) -> str | None:
        return None if self.fixed in ("-", "") else self.fixed


@dataclass
class Packet:
    folder: Path
    layout: dict[str, list[str]]
    sessions: dict[str, Session]
    assignments: list[Assignment]
    assigned: dict[str, str]
    hall: dict[str, str]
    pair: dict[str, str]
    horizon: dict[str, int]
    visits: list[dict[str, str]]
    printing: list[dict[str, str]]
    guardrails: list[dict[str, str]]
    invitations: list[dict[str, str]]
    loans: list[dict[str, str]]
    inventory: dict[tuple[str, str], dict[str, int]]
    dispatch: dict[str, dict[str, str]]
    dispatch_profiles: list[dict[str, str]]
    next_sessions: list[Option]
    print_orders: list[dict[str, str]]
    row_counts: dict[str, int] = field(default_factory=dict)

    def option(self, name: str) -> Option:
        for option in self.next_sessions:
            if option.option == name:
                return option
        raise KeyError(name)

    def option_map(self) -> dict[str, Option]:
        return {option.option: option for option in self.next_sessions}

    def guardrail_rows(self, hall: str) -> list[dict[str, str]]:
        return [row for row in self.guardrails if row["hall"] == hall]

    def guardrail(self, hall: str, layer: str) -> dict[str, str] | None:
        for row in self.guardrails:
            if row["hall"] == hall and row["layer"] == layer:
                return row
        return None

    def printing_row(self, hall: str) -> dict[str, str] | None:
        for row in self.printing:
            if row["hall"] == hall:
                return row
        return None

    def orders_for(self, option: str) -> list[dict[str, str]]:
        return [row for row in self.print_orders if row["option"] == option]


def load_packet(folder: Path | str = Path("data")) -> Packet:
    folder = Path(folder)
    with (folder / "layout.json").open(encoding="utf-8") as source:
        layout = json.load(source)
    row_counts = _validate(folder, layout)

    sessions = {
        row["session"]: Session(
            session=row["session"],
            start=int(row["start"]),
            open_minutes=int(row["open_minutes"]),
            volunteer=row["volunteer"],
            status=row["status"],
        )
        for row in _read(folder, "sessions.tsv")
    }
    assignments = [
        Assignment(
            pair=row["pair"],
            hall=row["hall"],
            left=row["left"],
            right=row["right"],
            left_edition=row["left_edition"],
            right_edition=row["right_edition"],
            horizon=int(row["horizon"]),
        )
        for row in _read(folder, "assignment.tsv")
    ]
    assigned: dict[str, str] = {}
    hall: dict[str, str] = {}
    pair: dict[str, str] = {}
    horizon: dict[str, int] = {}
    for assignment in assignments:
        for session, edition in assignment.editions().items():
            if session not in sessions:
                raise ValueError(f"assignment.tsv 引用了不存在的场次 {session}")
            assigned[session] = edition
            hall[session] = assignment.hall
            pair[session] = assignment.pair
            horizon[session] = assignment.horizon

    visits = _read(folder, "visits.tsv")
    for row in visits:
        if row["session"] not in sessions:
            raise ValueError(f"visits.tsv 引用了不存在的场次 {row['session']}")
    invitations = _read(folder, "invitations.tsv")
    for row in invitations:
        if row["session"] not in sessions:
            raise ValueError(f"invitations.tsv 引用了不存在的场次 {row['session']}")
    loans = _read(folder, "loans.tsv")
    for row in loans:
        if row["session"] not in sessions:
            raise ValueError(f"loans.tsv 引用了不存在的场次 {row['session']}")

    inventory = {
        (row["edition"], row["language"]): {
            "ready_day": int(row["ready_day"]),
            "stock": int(row["stock"]),
            "pack_size": int(row["pack_size"]),
            "lock_day": int(row["lock_day"]),
            "unit_cost": int(row["unit_cost"]),
        }
        for row in _read(folder, "edition_inventory.tsv")
    }
    dispatch = {row["option"]: row for row in _read(folder, "dispatch.tsv")}
    next_sessions = [
        Option(
            option=row["option"],
            hall=row["hall"],
            day=int(row["day"]),
            start=int(row["start"]),
            length=int(row["length"]),
            volunteer=row["volunteer"],
            fixed=row["fixed"],
            flush=int(row["flush"]),
        )
        for row in _read(folder, "next_sessions.tsv")
    ]
    option_names = {option.option for option in next_sessions}
    for name in dispatch:
        if name not in option_names:
            raise ValueError(f"dispatch.tsv 引用了不存在的场次选项 {name}")
    for row in _read(folder, "dispatch_profiles.tsv"):
        if row["option"] not in dispatch:
            raise ValueError(f"dispatch_profiles.tsv 引用了没有发放资料的选项 {row['option']}")
    for row in _read(folder, "print_orders.tsv"):
        if row["option"] not in dispatch:
            raise ValueError(f"print_orders.tsv 引用了不存在的场次选项 {row['option']}")
        if (row["edition"], row["language"]) not in inventory:
            raise ValueError(f"print_orders.tsv 引用了没有库存记录的版次 {row['edition']}/{row['language']}")

    return Packet(
        folder=folder,
        layout=layout,
        sessions=sessions,
        assignments=assignments,
        assigned=assigned,
        hall=hall,
        pair=pair,
        horizon=horizon,
        visits=visits,
        printing=_read(folder, "printing.tsv"),
        guardrails=_read(folder, "guardrails.tsv"),
        invitations=invitations,
        loans=loans,
        inventory=inventory,
        dispatch=dispatch,
        dispatch_profiles=_read(folder, "dispatch_profiles.tsv"),
        next_sessions=next_sessions,
        print_orders=_read(folder, "print_orders.tsv"),
        row_counts=row_counts,
    )
