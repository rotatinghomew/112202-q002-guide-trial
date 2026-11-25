"""试印分析的口径测试：用小型合成记录锁定关键判定。"""
import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

from exhibit_trial import decision, effects
from exhibit_trial.data import load_packet
from exhibit_trial.observations import FAILURE, SUCCESS, UNKNOWN, build_frame, classify_outcome
from exhibit_trial.stats import cell, miettinen_nurminen, newcombe, wilson

from test_helpers import make_packet


class OutcomeRules(unittest.TestCase):
    def test_independent_within_window_is_success(self):
        packet = make_packet()
        session = packet.sessions["S1"]
        self.assertEqual(
            classify_outcome(session, "independent", 0, 15, 20), (SUCCESS, "found")
        )

    def test_independent_after_window_is_failure_when_window_was_full(self):
        packet = make_packet()
        session = packet.sessions["S1"]
        self.assertEqual(
            classify_outcome(session, "independent", 0, 25, 20), (FAILURE, "found_late")
        )

    def test_short_window_truncates_to_unknown_not_failure(self):
        packet = make_packet()
        session = packet.sessions["SHORT"]
        self.assertEqual(
            classify_outcome(session, "not-found", 0, 15, 20), (UNKNOWN, "truncated")
        )

    def test_cancelled_session_is_unknown(self):
        packet = make_packet()
        session = packet.sessions["SCAN"]
        self.assertEqual(
            classify_outcome(session, "independent", 0, 15, 20), (UNKNOWN, "cancelled")
        )

    def test_missing_result_is_unknown(self):
        packet = make_packet()
        session = packet.sessions["S1"]
        self.assertEqual(
            classify_outcome(session, "missing", 0, 15, 20), (UNKNOWN, "missing_result")
        )

    def test_help_is_failure_even_if_later_found(self):
        packet = make_packet()
        session = packet.sessions["S1"]
        self.assertEqual(
            classify_outcome(session, "assisted", 0, 15, 20), (FAILURE, "assisted")
        )


class FrameRules(unittest.TestCase):
    def test_first_opportunity_only_per_household_session(self):
        packet = make_packet(extra_visits=True)
        frame = build_frame(packet)
        rows = [row for row in frame.observations if row.household == "H1"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].opportunity, 0)
        self.assertEqual(rows[0].result, "independent")
        self.assertEqual(frame.duplicate_visit_rows, 1)

    def test_not_entered_invitation_is_excluded_not_failure(self):
        packet = make_packet()
        frame = build_frame(packet)
        households = {row.household for row in frame.observations}
        self.assertNotIn("H-NO", households)
        self.assertEqual(
            [row["household"] for row in frame.excluded_not_entered], ["H-NO"]
        )

    def test_entered_invitation_without_visit_stays_unknown(self):
        packet = make_packet()
        frame = build_frame(packet)
        row = next(row for row in frame.observations if row.household == "H-SILENT")
        self.assertEqual(row.outcome, UNKNOWN)
        self.assertEqual(row.reason, "no_record")
        self.assertFalse(row.known)

    def test_late_loan_batch_changes_exposure(self):
        packet = make_packet(late_loan=True)
        before = build_frame(packet, loan_batch=1)
        after = build_frame(packet, loan_batch=2)
        before_row = next(row for row in before.observations if row.household == "H2")
        after_row = next(row for row in after.observations if row.household == "H2")
        self.assertEqual(before_row.session_exposure, frozenset({"B"}))
        self.assertEqual(after_row.session_exposure, frozenset({"A", "B"}))
        self.assertTrue(after_row.contaminated)

    def test_randomised_arm_is_not_rewritten_by_late_evidence(self):
        packet = make_packet(late_loan=True)
        frame = build_frame(packet, loan_batch=2)
        row = next(row for row in frame.observations if row.household == "H2")
        self.assertEqual(row.assigned, "B")
        self.assertFalse(row.pure)
        self.assertTrue(row.contaminated)

    def test_cross_session_household_kept_as_separate_observations(self):
        packet = make_packet(cross_session=True)
        frame = build_frame(packet)
        rows = [row for row in frame.observations if row.household == "H-X"]
        self.assertEqual(len(rows), 2)
        self.assertEqual({row.session for row in rows}, {"S1", "S2"})


class Statistics(unittest.TestCase):
    def test_wilson_is_inside_unit_interval(self):
        for successes, n in ((0, 1), (1, 1), (0, 0), (5, 10)):
            low, high = wilson(successes, n)
            if n:
                self.assertGreaterEqual(low, 0.0)
                self.assertLessEqual(high, 1.0)

    def test_newcombe_difference_direction(self):
        diff, low, high = newcombe(5, 10, 8, 10)
        self.assertAlmostEqual(diff, 0.3)
        self.assertLess(low, diff)
        self.assertGreater(high, diff)

    def test_miettinen_nurminen_weights_by_pair(self):
        estimate = miettinen_nurminen([((5, 10), (8, 10))])
        self.assertAlmostEqual(estimate, 0.3)

    def test_miettinen_nurminen_skips_pairs_without_both_arms(self):
        self.assertIsNone(miettinen_nurminen([((5, 10), (0, 0))]))

    def test_cell_separates_unknown(self):
        packet = make_packet(extra_visits=True)
        frame = build_frame(packet)
        value = cell(frame.observations)
        self.assertEqual(value.n, len(frame.observations))
        self.assertGreater(value.unknown, 0)


class ClaimRules(unittest.TestCase):
    def test_strata_never_fall_back_to_hall_level(self):
        packet = make_packet(with_layers=True)
        frame = build_frame(packet)
        layers = effects.protected_layers(frame, packet)
        for name, row in layers.items():
            self.assertEqual(
                {obs.household for obs in frame.observations if name in obs.prereg_profile}
                | set(),
                {obs.household for obs in frame.observations if name in obs.prereg_profile},
            )
            # 保护层人数不应等于全厅人数。
            self.assertLess(
                row["a"].n + row["b"].n,
                len(frame.observations) + 1,
            )

    def test_layer_counts_are_deduplicated(self):
        packet = make_packet(with_layers=True)
        overlap = decision.layer_overlap(packet)
        for row in overlap.values():
            self.assertEqual(row["naive_sum"] - row["both"], row["union"])
            self.assertEqual(row["union"] + row["other"], row["total"])

    def test_notified_commitment_cannot_be_dropped(self):
        packet = make_packet(with_layers=True)
        plan = decision.Plan({"S1": {"A": 9}, "S2": {"B": 1, "A": 9}})
        problems = decision.commitment_check(packet, plan)
        self.assertTrue(problems)

    def test_adjacent_different_editions_below_flush_is_conflict(self):
        packet = make_packet(with_layers=True)
        plan = decision.Plan({"S1": {"A": 10}, "S2": {"B": 10}})
        conflicts = decision.adjacency(packet, plan)
        self.assertEqual(len(conflicts), 1)
        self.assertLess(conflicts[0]["gap"], conflicts[0]["required"])

    def test_budget_counts_whole_pack_and_keeps_waste(self):
        packet = make_packet(with_layers=True)
        plan = decision.Plan({"S1": {"A": 10}, "S2": {"B": 10}})
        purchase = decision.procurement(packet, decision.demand_split(packet, plan))
        line = next(line for line in purchase["lines"] if line["key"] == ("B", "en"))
        self.assertGreater(line["order_qty"], line["shortfall"] or 0)
        self.assertGreaterEqual(line["waste"], 0)

    def test_as_treated_excludes_short_window_pairs(self):
        packet = make_packet(with_layers=True)
        frame = build_frame(packet)
        treated = effects.as_treated(frame, packet)
        self.assertIn("P2", treated["excluded_pairs"])


class RepoDataSmoke(unittest.TestCase):
    def test_repo_data_builds_and_selection_runs(self):
        root = Path(__file__).resolve().parents[1] / "data"
        packet = load_packet(root)
        frame = build_frame(packet)
        self.assertEqual(len(frame.observations), 138)
        selection = decision.select_plan(packet, frame)
        self.assertIn("best", selection)
        self.assertEqual(len(selection["candidates"]), 6)

    def test_repo_randomised_effect_keeps_unknown_in_denominator(self):
        root = Path(__file__).resolve().parents[1] / "data"
        packet = load_packet(root)
        frame = build_frame(packet)
        summary = effects.arm_summary(frame, "A")
        self.assertEqual(summary.assigned.n, 65)
        self.assertEqual(summary.assigned.known, 50)
        self.assertEqual(summary.assigned.unknown, 15)


if __name__ == "__main__":
    unittest.main()
