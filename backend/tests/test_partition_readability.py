"""分区可读明细与销售日加工批次的行为测试。

运行：backend/.venv/bin/python -m unittest discover -s backend/tests -v
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.partition_readability import PartitionReadabilityState  # noqa: E402

REV_A = "REV-2026-11-20-A"
REV_B = "REV-2026-11-20-B"
MONDAY = "2026-11-16"
TUESDAY = "2026-11-17"


class PartitionReadabilityTest(unittest.TestCase):
    def setUp(self) -> None:
        self.state = PartitionReadabilityState()

    def _region_reading(self, revision: str, date: str):
        detail = self.state.get_readability(revision, [date])
        return next(row for row in detail["rows"] if row["role"] == "region")

    def test_summary_distinguishes_zero_from_unmeasured(self) -> None:
        detail = self.state.get_readability(REV_A, [MONDAY])
        self.assertEqual(detail["summary"]["确认零行"], 1)
        self.assertEqual(detail["summary"]["可读有行"], 1)
        self.assertEqual(detail["summary"]["未实测"], 0)
        self.assertTrue(detail["decision"]["can_submit"])

    def test_other_revision_empty_result_does_not_fill_gap(self) -> None:
        # 修订 B 有周二地区的空分区，但修订 A 仍是未实测。
        detail = self.state.get_readability(REV_A, [TUESDAY])
        self.assertTrue(detail["rows"][1]["other_revision_evidence"])
        self.assertEqual(self._region_reading(REV_A, TUESDAY)["reading_state"], "未实测")
        self.assertFalse(detail["decision"]["can_submit"])
        self.assertIn(TUESDAY, detail["decision"]["waiting_dates"])

    def test_reporting_requires_rows_and_revision_scope(self) -> None:
        with self.assertRaisesRegex(ValueError, "行数"):
            self.state.record_reading(
                revision=REV_A, role="region", partition=TUESDAY, rows="")
        with self.assertRaisesRegex(ValueError, "扫描范围"):
            self.state.record_reading(
                revision=REV_A, role="region", partition="2026-11-18", rows=0)

    def test_report_to_other_revision_does_not_release_batch(self) -> None:
        self.state.generate_batches(revision=REV_A, dates=[MONDAY, TUESDAY])
        self.state.record_reading(
            revision=REV_B, role="region", partition=TUESDAY, rows=0)
        result = self.state.refresh_batches(revision=REV_A)
        self.assertEqual(result["released_batches"], [])
        self.assertEqual(len(result["waiting_batches"]), 1)

    def test_late_report_and_refresh_releases_tuesday_batch(self) -> None:
        created = self.state.generate_batches(revision=REV_A, dates=[MONDAY, TUESDAY])
        self.assertEqual(len(created["submitted_batches"]), 1)
        self.assertEqual(len(created["waiting_batches"]), 1)
        self.assertEqual(created["batches"][0]["status"], "已提交")
        self.assertEqual(created["batches"][1]["status"], "等待证据")

        report = self.state.record_reading(
            revision=REV_A, role="region", partition=TUESDAY, rows=8231)
        self.assertTrue(report["refresh_required"])

        refreshed = self.state.refresh_batches(revision=REV_A)
        self.assertEqual(len(refreshed["released_batches"]), 1)
        batches = self.state.list_batches(revision=REV_A)
        self.assertEqual([b["status"] for b in batches], ["已提交", "已提交"])
        self.assertEqual(
            [b["region_source"].split(" · ")[1] for b in batches],
            ["确认零行", "可读有行"],
        )

    def test_batches_carry_order_and_region_partitions(self) -> None:
        created = self.state.generate_batches(revision=REV_A, dates=[MONDAY])
        batch = created["batches"][0]
        self.assertEqual(batch["order_partition"], MONDAY)
        self.assertEqual(batch["region_partition"], MONDAY)
        roles = {source["role"]: source for source in batch["sources"]}
        self.assertEqual(roles["order"]["table"], "ods.ods_order_info")
        self.assertEqual(roles["region"]["table"], "ods.ods_region_info")

    def test_scope_change_recalculates_open_batches_and_freezes_running(self) -> None:
        self.state.record_reading(
            revision=REV_A, role="region", partition=TUESDAY, rows=100)
        created = self.state.generate_batches(revision=REV_A, dates=[MONDAY, TUESDAY])
        monday_batch = created["batches"][0]["batch_id"]
        self.state.transition_batch(monday_batch, "开始加工")

        result = self.state.update_scope(
            revision=REV_A, dates=[MONDAY, "2026-11-18"], reason="扩展范围")

        frozen_ids = [batch["batch_id"] for batch in result["frozen_batches"]]
        self.assertIn(monday_batch, frozen_ids)
        running = next(
            batch for batch in self.state.list_batches(revision=REV_A)
            if batch["batch_id"] == monday_batch)
        # 运行中的批次保留计划数据源，周二仍在范围内但不在本次重算范围。
        self.assertEqual(running["status"], "运行中")
        self.assertEqual(
            [(s["role"], s["partition"]) for s in running["planned_sources"]],
            [("order", MONDAY), ("region", MONDAY)],
        )

    def test_scope_change_recalculates_unscheduled_date(self) -> None:
        self.state.generate_batches(revision=REV_A, dates=[MONDAY, TUESDAY])
        result = self.state.update_scope(
            revision=REV_A, dates=[MONDAY, TUESDAY], reason="确认周二范围")
        # 周二批次尚未提交，重算后仍因缺地区实测而等待证据。
        recalculated = {batch["batch_id"]: batch for batch in result["recalculated_batches"]}
        self.assertTrue(recalculated)
        self.assertTrue(all(b["status"] == "等待证据" for b in recalculated.values()))


if __name__ == "__main__":
    unittest.main()
