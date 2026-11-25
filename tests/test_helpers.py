"""构造小型试印包，供口径测试使用。"""
import csv
import json
from pathlib import Path

TABLES = {
    "sessions.tsv": ["session", "start", "open_minutes", "volunteer", "status"],
    "assignment.tsv": ["pair", "hall", "left", "right", "left_edition", "right_edition", "horizon"],
    "visits.tsv": ["visit", "household", "session", "profile", "actual", "opportunity", "result", "minute"],
    "printing.tsv": ["hall", "min_gain", "group", "max_loss", "contamination_max"],
    "invitations.tsv": ["invitation", "household", "session", "prereg_profile", "language", "entered", "offered"],
    "loans.tsv": ["receipt", "household", "session", "desk", "version", "minute", "received_batch"],
    "edition_inventory.tsv": ["edition", "language", "ready_day", "stock", "pack_size", "lock_day", "unit_cost"],
    "dispatch.tsv": ["option", "languages", "profiles", "expected", "desk", "routes", "old_on_desk"],
    "dispatch_profiles.tsv": ["option", "language", "profiles", "households"],
    "print_orders.tsv": ["order", "option", "edition", "language", "qty", "approved_day", "state"],
    "guardrails.tsv": ["hall", "layer", "min_gain", "max_loss", "mixed_ceiling", "budget"],
    "next_sessions.tsv": ["option", "hall", "day", "start", "length", "volunteer", "fixed", "flush"],
}


def _write(root: Path, name: str, rows: list[list[object]]) -> None:
    with (root / name).open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle, delimiter="\t", lineterminator="\n")
        writer.writerow(TABLES[name])
        writer.writerows(rows)


def make_packet(
    extra_visits: bool = False,
    late_loan: bool = False,
    cross_session: bool = False,
    with_layers: bool = True,
):
    """写出一份最小可用记录，返回已加载的 Packet。"""
    import tempfile

    from exhibit_trial.data import load_packet

    root = Path(tempfile.mkdtemp(prefix="exhibit-"))
    with (root / "layout.json").open("w", encoding="utf-8") as handle:
        json.dump(TABLES, handle, ensure_ascii=False)

    _write(
        root,
        "sessions.tsv",
        [
            ["S1", 60, 30, "V1", "observed"],
            ["S2", 85, 30, "V1", "observed"],
            ["SHORT", 120, 10, "V2", "observed"],
            ["SCAN", 150, 30, "V2", "cancelled"],
        ],
    )
    _write(
        root,
        "assignment.tsv",
        [
            ["P1", "bronze", "S1", "S2", "A", "B", 20],
            ["P2", "bronze", "SHORT", "SCAN", "A", "B", 20],
        ],
    )
    visits = [
        ["U1", "H1", "S1", "family", "A", 0, "independent", 15],
        ["U2", "H2", "S2", "family", "B", 0, "independent", 15],
        ["U3", "H3", "SHORT", "adult", "A", 0, "not-found", 15],
        ["U4", "H4", "S1", "adult", "A", 0, "missing", 15],
    ]
    if extra_visits:
        visits.append(["U5", "H1", "S1", "family", "A", 3, "not-found", 20])
    if cross_session:
        visits.append(["U6", "H-X", "S1", "family", "A", 0, "independent", 15])
        visits.append(["U7", "H-X", "S2", "adult", "B", 0, "independent", 15])
    _write(root, "visits.tsv", visits)
    _write(root, "printing.tsv", [["bronze", "0.08", "family", "0.04", "0.25"]])
    _write(
        root,
        "invitations.tsv",
        [
            ["I1", "H-NO", "S1", "minor", "zh", "no", "A"],
            ["I2", "H-SILENT", "S1", "unfamiliar", "zh", "yes", "A"],
            ["I3", "H2", "S2", "minor,unfamiliar", "zh", "yes", "B"],
        ],
    )
    loans = []
    if late_loan:
        loans = [
            ["R1", "H2", "S2", "D", "B", 0, 1],
            ["R2", "H2", "S2", "D", "A", 3, 2],
        ]
    _write(root, "loans.tsv", loans)
    _write(
        root,
        "edition_inventory.tsv",
        [
            ["A", "zh", 1, 10, 10, 1, 100],
            ["A", "en", 1, 5, 10, 2, 150],
            ["B", "zh", 1, 2, 5, 3, 200],
            ["B", "en", 1, 1, 5, 2, 250],
        ],
    )
    _write(
        root,
        "dispatch.tsv",
        [
            ["S1", "zh", "minor", 10, "D", "G1", "yes"],
            ["S2", "zh", "minor,unfamiliar", 10, "D", "G2", "no"],
        ],
    )
    if with_layers:
        _write(
            root,
            "dispatch_profiles.tsv",
            [
                ["S1", "zh", "minor", 6],
                ["S1", "zh", "all", 4],
                ["S2", "zh", "minor,unfamiliar", 4],
                ["S2", "zh", "minor", 2],
                ["S2", "en", "unfamiliar", 4],
            ],
        )
    else:
        _write(
            root,
            "dispatch_profiles.tsv",
            [["S1", "zh", "minor", 10], ["S2", "zh", "minor,unfamiliar", 10]],
        )
    _write(
        root,
        "print_orders.tsv",
        [
            ["O1", "S1", "A", "zh", 10, 1, "notified"],
            ["O2", "S2", "B", "zh", 1, 1, "notified"],
        ],
    )
    _write(
        root,
        "guardrails.tsv",
        [
            ["bronze", "minor", 0.05, 0.05, 0.2, 5000],
            ["bronze", "unfamiliar", 0.05, 0.05, 0.2, 5000],
        ],
    )
    _write(
        root,
        "next_sessions.tsv",
        [
            ["S1", "bronze", 2, 60, 30, "V1", "A", 10],
            ["S2", "bronze", 2, 85, 30, "V1", "-", 10],
        ],
    )
    return load_packet(root)
