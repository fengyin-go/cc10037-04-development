"""分区可读明细：按扫描修订保存每个上游分区的实测结果，并供依赖调度消费。

关键语义：
- 一次扫描产生一个「扫描修订」，明细只认同一修订内的实测；
- 「确认零行」是有效证据，「未实测」不是；
- 补报扫描只能补进对应修订，其他修订的空分区结果不能替代缺失数据；
- 销售日加工批次在生成时固化计划数据源，运行中的批次仍可追溯原计划。
"""
from __future__ import annotations

import threading
from copy import deepcopy
from datetime import date as date_cls
from datetime import datetime
from typing import Any, Iterable

SALES_PIPELINE = "销售日加工"

# 销售日加工实际读取的两份上游输入。
SALES_INPUTS: tuple[dict[str, str], ...] = (
    {"role": "order", "label": "订单", "table": "ods.ods_order_info"},
    {"role": "region", "label": "地区", "table": "ods.ods_region_info"},
)

STATE_READABLE = "可读有行"
STATE_ZERO = "确认零行"
STATE_UNMEASURED = "未实测"
STATE_ORDER = (STATE_ZERO, STATE_READABLE, STATE_UNMEASURED)

DEFAULT_REVISION = "REV-2026-11-20-A"


def _now() -> str:
    return datetime.now().replace(microsecond=0).isoformat()


def _normalise_date(value: Any, field: str = "分区") -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field}不能为空")
    try:
        return date_cls.fromisoformat(text).isoformat()
    except ValueError as exc:
        raise ValueError(f"{field}「{text}」不是合法的 YYYY-MM-DD 日期") from exc


def _normalise_dates(values: Iterable[Any] | None) -> list[str]:
    if values is None:
        return []
    if isinstance(values, str):
        values = [item for item in values.replace("，", ",").split(",")]
    result: list[str] = []
    for value in values:
        text = _normalise_date(value, "加工日期")
        if text not in result:
            result.append(text)
    return sorted(result)


def _parse_rows(value: Any) -> int:
    if value is None or str(value).strip() == "":
        raise ValueError("补报扫描必须提供实测行数；零行请明确填写 0")
    try:
        rows = int(str(value).strip())
    except (TypeError, ValueError) as exc:
        raise ValueError(f"实测行数「{value}」不是整数") from exc
    if rows < 0:
        raise ValueError("实测行数不能为负数")
    return rows


def _input_by_table(table: str | None, role: str | None = None) -> dict[str, str]:
    for item in SALES_INPUTS:
        if table and item["table"] == str(table).strip():
            return item
        if role and item["role"] == str(role).strip():
            return item
    allowed = "、".join(f"{item['label']}({item['table']})" for item in SALES_INPUTS)
    raise ValueError(f"上游输入必须是 {allowed} 之一")


def _input_by_role(role: str) -> dict[str, str]:
    for item in SALES_INPUTS:
        if item["role"] == role:
            return item
    raise ValueError(f"未知上游角色 {role}")


class PartitionReadabilityState:
    """扫描修订、分区实测与销售日加工批次的内存仓库。"""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._revisions: dict[str, dict[str, Any]] = {}
        self._readings: list[dict[str, Any]] = []
        self._batches: list[dict[str, Any]] = []
        self._requests: list[dict[str, Any]] = []
        self._seq = 100
        self._load_seed()

    # ------------------------------------------------------------------ seed
    def _load_seed(self) -> None:
        revisions = [
            {
                "revision": "REV-2026-11-20-A",
                "scanned_at": "2026-11-20T08:00:00",
                "source": "常规夜间扫描",
                "scope": ["2026-11-16", "2026-11-17"],
                "note": "订单表两日均有实测；地区表本轮只回报周一",
                "status": "已发布",
            },
            {
                "revision": "REV-2026-11-20-B",
                "scanned_at": "2026-11-20T09:30:00",
                "source": "地区表补充轮次",
                "scope": ["2026-11-17"],
                "note": "另一轮扫描报回周二地区空分区，不能替代修订 A 的缺失实测",
                "status": "已发布",
            },
        ]
        for item in revisions:
            item["scope"] = _normalise_dates(item["scope"])
            self._revisions[item["revision"]] = item

        readings = [
            {
                "revision": "REV-2026-11-20-A",
                "table": "ods.ods_order_info",
                "partition": "2026-11-16",
                "rows": 152300,
                "source": "常规夜间扫描",
                "measured_at": "2026-11-20T08:00:00",
                "note": "订单周一分区有数据",
            },
            {
                "revision": "REV-2026-11-20-A",
                "table": "ods.ods_order_info",
                "partition": "2026-11-17",
                "rows": 149800,
                "source": "常规夜间扫描",
                "measured_at": "2026-11-20T08:00:00",
                "note": "订单周二分区有数据",
            },
            {
                "revision": "REV-2026-11-20-A",
                "table": "ods.ods_region_info",
                "partition": "2026-11-16",
                "rows": 0,
                "source": "常规夜间扫描",
                "measured_at": "2026-11-20T08:00:00",
                "note": "地区周一分区已实测，确认零行",
            },
            {
                "revision": "REV-2026-11-20-B",
                "table": "ods.ods_region_info",
                "partition": "2026-11-17",
                "rows": 0,
                "source": "地区表补充轮次",
                "measured_at": "2026-11-20T09:30:00",
                "note": "另一轮扫描的空分区结果，仅属于修订 B",
            },
        ]
        self._readings = [self._materialise_reading(item) for item in readings]

    def _materialise_reading(self, item: dict[str, Any]) -> dict[str, Any]:
        self._seq += 1
        result = deepcopy(item)
        result["id"] = self._seq
        result["partition"] = _normalise_date(result["partition"])
        result["rows"] = int(result["rows"])
        result["state"] = STATE_ZERO if result["rows"] == 0 else STATE_READABLE
        return result

    # --------------------------------------------------------------- helpers
    def _next_id(self) -> int:
        self._seq += 1
        return self._seq

    def _revision_or_raise(self, revision: str | None) -> dict[str, Any]:
        name = str(revision or "").strip() or DEFAULT_REVISION
        target = self._revisions.get(name)
        if target is None:
            raise ValueError(f"扫描修订「{name}」不存在")
        return target

    def _reading_for(self, revision: str, table: str, partition: str) -> dict[str, Any] | None:
        matches = [
            item for item in self._readings
            if item["revision"] == revision
            and item["table"] == table
            and item["partition"] == partition
        ]
        if not matches:
            return None
        return max(matches, key=lambda item: str(item.get("measured_at", "")))

    def _other_revision_evidence(self, revision: str, table: str, partition: str) -> list[dict[str, Any]]:
        result = []
        for item in self._readings:
            if (
                item["revision"] != revision
                and item["table"] == table
                and item["partition"] == partition
            ):
                result.append({
                    "revision": item["revision"],
                    "state": item["state"],
                    "rows": item["rows"],
                    "measured_at": item.get("measured_at"),
                    "note": "其他扫描修订的结果不计入当前修订",
                })
        return result

    def _evaluate_date(self, revision_name: str, partition: str) -> dict[str, Any]:
        revision = self._revision_or_raise(revision_name)
        in_scope = partition in revision["scope"]
        sources: list[dict[str, Any]] = []
        blockers: list[dict[str, Any]] = []
        opened: list[dict[str, Any]] = []

        for input_item in SALES_INPUTS:
            reading = self._reading_for(revision_name, input_item["table"], partition)
            if not in_scope:
                # 不在扫描范围内：即使此前有读数也不作为本次覆盖证据，展示实际读数便于排查。
                sources.append({
                    "role": input_item["role"],
                    "role_label": input_item["label"],
                    "table": input_item["table"],
                    "partition": partition,
                    "reading_state": STATE_UNMEASURED,
                    "rows": None,
                    "measured": False,
                    "out_of_scope": True,
                    "revision": revision_name,
                    "measured_at": None,
                    "stale_reading": (
                        {
                            "state": reading["state"],
                            "rows": reading["rows"],
                            "measured_at": reading.get("measured_at"),
                        }
                        if reading
                        else None
                    ),
                    "other_revision_evidence": self._other_revision_evidence(
                        revision_name, input_item["table"], partition),
                })
                blockers.append({
                    "role": input_item["role"],
                    "role_label": input_item["label"],
                    "table": input_item["table"],
                    "partition": partition,
                    "state": STATE_UNMEASURED,
                    "reason": (
                        f"分区 {partition} 不在扫描修订 {revision_name} 的扫描范围内；"
                        "需先调整扫描范围，再补入同修订实测"
                    ),
                })
                continue

            if reading is None:
                sources.append({
                    "role": input_item["role"],
                    "role_label": input_item["label"],
                    "table": input_item["table"],
                    "partition": partition,
                    "reading_state": STATE_UNMEASURED,
                    "rows": None,
                    "measured": False,
                    "revision": revision_name,
                    "measured_at": None,
                    "other_revision_evidence": self._other_revision_evidence(
                        revision_name, input_item["table"], partition),
                })
                blockers.append({
                    "role": input_item["role"],
                    "role_label": input_item["label"],
                    "table": input_item["table"],
                    "partition": partition,
                    "state": STATE_UNMEASURED,
                    "reason": (
                        f"{input_item['label']}表分区 {partition} 在修订 {revision_name} 中未实测；"
                        "补报必须写入同一修订，其他修订的空分区结果不能替代"
                    ),
                })
                continue

            source = {
                "role": input_item["role"],
                "role_label": input_item["label"],
                "table": input_item["table"],
                "partition": partition,
                "reading_state": reading["state"],
                "rows": reading["rows"],
                "measured": True,
                "revision": reading["revision"],
                "measured_at": reading.get("measured_at"),
                "source": reading.get("source"),
                "note": reading.get("note", ""),
            }
            sources.append(source)
            opened.append({
                **source,
                "display": (
                    f"{input_item['label']} {partition} {reading['state']}"
                    f"（{reading['rows']} 行，修订 {reading['revision']}）"
                ),
            })

        return {
            "partition": partition,
            "in_scope": in_scope,
            "ready": in_scope and not blockers,
            "sources": sources,
            "blockers": blockers,
            "open_readings": opened,
        }

    def _build_detail(
        self,
        revision_name: str,
        dates: Iterable[Any] | None = None,
    ) -> dict[str, Any]:
        revision = self._revision_or_raise(revision_name)
        requested = _normalise_dates(dates)
        if not requested:
            requested = list(revision["scope"])

        rows: list[dict[str, Any]] = []
        evaluations = [self._evaluate_date(revision_name, item) for item in requested]
        for input_item in SALES_INPUTS:
            for evaluation in evaluations:
                source = next(
                    item for item in evaluation["sources"] if item["role"] == input_item["role"])
                rows.append({**source, "partition_date": evaluation["partition"]})

        counts = {state: 0 for state in STATE_ORDER}
        for row in rows:
            counts[row["reading_state"]] += 1

        ready_dates = [item["partition"] for item in evaluations if item["ready"]]
        waiting_dates = [item["partition"] for item in evaluations if not item["ready"]]
        coverage_complete = not waiting_dates

        missing: list[dict[str, Any]] = []
        for evaluation in evaluations:
            missing.extend(evaluation["blockers"])

        all_open = [
            reading for evaluation in evaluations for reading in evaluation["open_readings"]
        ]
        ready_open = [
            reading
            for evaluation in evaluations if evaluation["ready"]
            for reading in evaluation["open_readings"]
        ]

        options = [
            {
                "key": "submit_ready_dates",
                "label": "立即提交已覆盖日期的批次",
                "can_submit": bool(ready_dates),
                "dates": ready_dates,
                "open_readings": ready_open,
                "missing": [],
                "note": (
                    "确认零行是已实测证据，可以进入批次；未实测日期不会被带入。"
                ),
            },
            {
                "key": "wait_full_coverage",
                "label": "等到所有日期都有实测后提交完整批次",
                "can_submit": coverage_complete,
                "dates": requested if coverage_complete else [],
                "open_readings": all_open,
                "missing": missing,
                "note": (
                    "补报扫描必须补入同一扫描修订；另一轮扫描的空分区结果不能替代缺失数据。"
                ),
            },
        ]

        if coverage_complete:
            decision_text = f"{'、'.join(requested)} 均已按修订 {revision_name} 覆盖，可提交完整批次"
        elif ready_dates:
            decision_text = (
                f"可先提交 {'、'.join(ready_dates)} 批次；"
                f"{'、'.join(waiting_dates)} 仍缺实测证据，需继续等待"
            )
        else:
            decision_text = (
                f"尚无可提交日期；{'、'.join(waiting_dates)} 仍缺实测证据，需继续等待"
            )

        return {
            "revision": deepcopy(revision),
            "revision_name": revision_name,
            "dates": requested,
            "rows": rows,
            "summary": {
                **counts,
                "total": len(rows),
                "coverage_complete": coverage_complete,
                "ready_dates": ready_dates,
                "waiting_dates": waiting_dates,
            },
            "decision": {
                "can_submit": bool(ready_dates),
                "ready_dates": ready_dates,
                "waiting_dates": waiting_dates,
                "coverage_complete": coverage_complete,
                "missing": missing,
                "text": decision_text,
            },
            "options": options,
        }

    # ------------------------------------------------------------- revisions
    def list_revisions(self) -> list[dict[str, Any]]:
        with self._lock:
            result = []
            for item in sorted(
                self._revisions.values(), key=lambda row: str(row.get("scanned_at", ""))):
                readings = [r for r in self._readings if r["revision"] == item["revision"]]
                detail = self._build_detail(item["revision"], item["scope"])
                result.append({
                    **deepcopy(item),
                    "reading_count": len(readings),
                    "coverage_complete": detail["summary"]["coverage_complete"],
                    "ready_dates": detail["summary"]["ready_dates"],
                    "waiting_dates": detail["summary"]["waiting_dates"],
                })
            return result

    # -------------------------------------------------------------- readings
    def get_readability(self, revision: str | None = None, dates: Iterable[Any] | None = None) -> dict[str, Any]:
        with self._lock:
            return self._build_detail(str(revision or DEFAULT_REVISION), dates)

    def record_reading(
        self,
        *,
        revision: str | None,
        table: str | None = None,
        role: str | None = None,
        partition: Any,
        rows: Any,
        source: str = "补报扫描",
        note: str = "",
    ) -> dict[str, Any]:
        """补报一条实测；只写入指定修订，不做跨修订覆盖。"""
        with self._lock:
            revision_name = str(revision or "").strip()
            if not revision_name:
                raise ValueError("补报扫描必须指定扫描修订，不能补到临时轮次")
            rev = self._revision_or_raise(revision_name)
            input_item = _input_by_table(table, role)
            partition_text = _normalise_date(partition)
            if partition_text not in rev["scope"]:
                raise ValueError(
                    f"分区 {partition_text} 不在扫描修订 {revision_name} 的扫描范围内；"
                    "请先调整该修订的扫描范围"
                )
            measured_rows = _parse_rows(rows)
            measured_at = _now()
            existing = None
            for item in self._readings:
                if (
                    item["revision"] == revision_name
                    and item["table"] == input_item["table"]
                    and item["partition"] == partition_text
                ):
                    existing = item
                    break

            if existing is not None:
                existing.update({
                    "rows": measured_rows,
                    "source": source or "补报扫描",
                    "measured_at": measured_at,
                    "note": note,
                    "state": STATE_ZERO if measured_rows == 0 else STATE_READABLE,
                })
                reading = existing
                action = "更新"
            else:
                reading = self._materialise_reading({
                    "revision": revision_name,
                    "table": input_item["table"],
                    "partition": partition_text,
                    "rows": measured_rows,
                    "source": source or "补报扫描",
                    "measured_at": measured_at,
                    "note": note,
                })
                self._readings.append(reading)
                action = "补入"

            waiting = [
                self._batch_summary(batch)
                for batch in self._batches
                if batch["revision"] == revision_name
                and batch["partition_date"] == partition_text
                and batch["status"] == "等待证据"
            ]
            detail = self._build_detail(revision_name, rev["scope"])
            return {
                "ok": True,
                "message": (
                    f"{input_item['label']}表分区 {partition_text} 的实测已{action}修订 "
                    f"{revision_name}（{reading['state']}）"
                ),
                "reading": deepcopy(reading),
                "readability": detail,
                "waiting_batches": waiting,
                "refresh_required": bool(waiting),
            }

    def update_scope(
        self,
        *,
        revision: str,
        dates: Iterable[Any],
        reason: str = "",
    ) -> dict[str, Any]:
        """调整扫描范围，并重算落在变更覆盖范围内的未运行批次。

        运行中/已完成的批次保留原计划数据源，不被重算覆盖。
        """
        with self._lock:
            revision_name = str(revision or "").strip()
            rev = self._revision_or_raise(revision_name)
            new_scope = _normalise_dates(dates)
            if not new_scope:
                raise ValueError("扫描范围至少需要一个日期")
            old_scope = list(rev["scope"])
            affected_dates = sorted(set(old_scope) | set(new_scope))
            changed_at = _now()
            rev["scope"] = new_scope
            rev["scope_updated_at"] = changed_at
            rev["scope_note"] = reason

            recalculated: list[dict[str, Any]] = []
            frozen: list[dict[str, Any]] = []
            for batch in self._batches:
                if batch["revision"] != revision_name:
                    continue
                if batch["partition_date"] not in affected_dates:
                    continue
                if batch["status"] != "等待证据":
                    batch["scope_change_seen_at"] = changed_at
                    frozen.append({
                        **self._batch_summary(batch),
                        "reason": (
                            "批次已提交或已开始，计划数据源已固化，不参与重算，保留原计划以便追溯"
                        ),
                    })
                    continue

                evaluation = self._evaluate_date(revision_name, batch["partition_date"])
                batch["sources"] = deepcopy(evaluation["sources"])
                batch["blockers"] = deepcopy(evaluation["blockers"])
                batch["opened_readings"] = deepcopy(evaluation["open_readings"])
                batch["status"] = "已提交" if evaluation["ready"] else "等待证据"
                batch["submitted_at"] = changed_at if evaluation["ready"] else None
                if evaluation["ready"]:
                    batch["planned_sources"] = deepcopy(evaluation["sources"])
                else:
                    batch["planned_sources"] = []
                batch["recalculated_at"] = changed_at
                recalculated.append(self._batch_summary(batch))

            detail = self._build_detail(revision_name, new_scope)
            return {
                "ok": True,
                "message": (
                    f"修订 {revision_name} 扫描范围已更新为 {'、'.join(new_scope)}；"
                    f"重算 {len(recalculated)} 个未提交批次，冻结 {len(frozen)} 个已提交/已运行批次"
                ),
                "revision": deepcopy(rev),
                "recalculated_batches": recalculated,
                "frozen_batches": frozen,
                "readability": detail,
            }

    # --------------------------------------------------------------- batches
    def _batch_summary(self, batch: dict[str, Any]) -> dict[str, Any]:
        sources = batch.get("sources", [])
        by_role = {item["role"]: item for item in sources}
        order = by_role.get("order", {})
        region = by_role.get("region", {})
        return {
            "id": batch["id"],
            "batch_id": batch["batch_id"],
            "pipeline": batch["pipeline"],
            "partition_date": batch["partition_date"],
            "status": batch["status"],
            "revision": batch["revision"],
            "order_partition": batch.get("order_partition"),
            "region_partition": batch.get("region_partition"),
            "order_source": self._source_text(order),
            "region_source": self._source_text(region),
            "sources": deepcopy(sources),
            "planned_sources": deepcopy(batch.get("planned_sources", [])),
            "opened_readings": deepcopy(batch.get("opened_readings", [])),
            "blockers": deepcopy(batch.get("blockers", [])),
            "planned_at": batch.get("planned_at"),
            "submitted_at": batch.get("submitted_at"),
            "started_at": batch.get("started_at"),
            "completed_at": batch.get("completed_at"),
            "recalculated_at": batch.get("recalculated_at"),
            "scope_change_seen_at": batch.get("scope_change_seen_at"),
            "request_id": batch.get("request_id"),
        }

    @staticmethod
    def _source_text(source: dict[str, Any]) -> str:
        if not source:
            return "—"
        state = source.get("reading_state", STATE_UNMEASURED)
        rows = source.get("rows")
        rows_text = "—" if rows is None else f"{rows} 行"
        return f"{source.get('revision')} · {state} · {rows_text}"

    def list_batches(
        self,
        *,
        revision: str | None = None,
        date: str | None = None,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        with self._lock:
            rows = list(self._batches)
            if revision:
                rows = [row for row in rows if row["revision"] == revision]
            if date:
                target = _normalise_date(date, "加工日期")
                rows = [row for row in rows if row["partition_date"] == target]
            if status:
                rows = [row for row in rows if row["status"] == status]
            rows.sort(key=lambda row: (row["partition_date"], row["id"]))
            return [self._batch_summary(row) for row in rows]

    def generate_batches(
        self,
        *,
        revision: str | None,
        dates: Iterable[Any],
        note: str = "",
    ) -> dict[str, Any]:
        """按分区可读明细生成销售日加工批次。

        每个加工日期一个批次，批次固化订单分区与地区分区；
        证据齐全的批次直接提交，缺实测的批次进入「等待证据」。
        """
        with self._lock:
            revision_name = str(revision or "").strip()
            if not revision_name:
                raise ValueError("生成销售日加工批次必须指定扫描修订")
            rev = self._revision_or_raise(revision_name)
            requested = _normalise_dates(dates)
            if not requested:
                raise ValueError("加工请求至少需要一个日期")

            request_id = f"REQ-{datetime.now().strftime('%Y%m%d%H%M%S')}-{self._next_id()}"
            created_at = _now()
            new_batches: list[dict[str, Any]] = []
            submitted: list[dict[str, Any]] = []
            waiting: list[dict[str, Any]] = []

            for partition in requested:
                evaluation = self._evaluate_date(revision_name, partition)
                status = "已提交" if evaluation["ready"] else "等待证据"
                batch = {
                    "id": self._next_id(),
                    "batch_id": f"SD-{partition.replace('-', '')}-{len(self._batches) + 1:03d}",
                    "pipeline": SALES_PIPELINE,
                    "partition_date": partition,
                    "status": status,
                    "revision": revision_name,
                    "order_partition": partition,
                    "region_partition": partition,
                    "sources": deepcopy(evaluation["sources"]),
                    # 计划数据源在批次提交时固化；等待证据的批次尚未提交，暂无计划。
                    "planned_sources": deepcopy(evaluation["sources"]) if evaluation["ready"] else [],
                    "opened_readings": deepcopy(evaluation["open_readings"]),
                    "blockers": deepcopy(evaluation["blockers"]),
                    "planned_at": created_at,
                    "submitted_at": created_at if evaluation["ready"] else None,
                    "started_at": None,
                    "completed_at": None,
                    "recalculated_at": None,
                    "scope_change_seen_at": None,
                    "request_id": request_id,
                    "note": note,
                }
                self._batches.append(batch)
                new_batches.append(batch)
                summary = self._batch_summary(batch)
                (submitted if evaluation["ready"] else waiting).append(summary)

            self._requests.append({
                "request_id": request_id,
                "revision": revision_name,
                "dates": requested,
                "note": note,
                "created_at": created_at,
                "batch_count": len(new_batches),
                "submitted_count": len(submitted),
                "waiting_count": len(waiting),
            })
            detail = self._build_detail(revision_name, requested)
            return {
                "ok": True,
                "message": (
                    f"已生成 {len(new_batches)} 个{SALES_PIPELINE}批次："
                    f"提交 {len(submitted)} 个，等待证据 {len(waiting)} 个"
                ),
                "request_id": request_id,
                "pipeline": SALES_PIPELINE,
                "revision": revision_name,
                "dates": requested,
                "submitted_batches": submitted,
                "waiting_batches": waiting,
                "batches": [self._batch_summary(b) for b in new_batches],
                "readability": detail,
                "decision": detail["decision"],
            }

    def refresh_batches(
        self,
        *,
        revision: str | None = None,
        dates: Iterable[Any] | None = None,
    ) -> dict[str, Any]:
        """刷新可读明细：只释放同一修订下已具备实测证据的等待批次。"""
        with self._lock:
            revision_name = str(revision or DEFAULT_REVISION)
            rev = self._revision_or_raise(revision_name)
            requested = _normalise_dates(dates)
            released: list[dict[str, Any]] = []
            still_waiting: list[dict[str, Any]] = []
            now = _now()

            for batch in self._batches:
                if batch["revision"] != revision_name or batch["status"] != "等待证据":
                    continue
                if requested and batch["partition_date"] not in requested:
                    continue
                evaluation = self._evaluate_date(revision_name, batch["partition_date"])
                batch["sources"] = deepcopy(evaluation["sources"])
                batch["opened_readings"] = deepcopy(evaluation["open_readings"])
                batch["blockers"] = deepcopy(evaluation["blockers"])
                batch["recalculated_at"] = now
                if evaluation["ready"]:
                    batch["status"] = "已提交"
                    batch["submitted_at"] = now
                    batch["planned_sources"] = deepcopy(evaluation["sources"])
                    released.append(self._batch_summary(batch))
                else:
                    still_waiting.append(self._batch_summary(batch))

            detail = self._build_detail(revision_name, requested or rev["scope"])
            return {
                "ok": True,
                "message": (
                    f"刷新修订 {revision_name} 的可读明细："
                    f"释放 {len(released)} 个等待批次，仍有 {len(still_waiting)} 个等待证据"
                ),
                "released_batches": released,
                "waiting_batches": still_waiting,
                "readability": detail,
            }

    def transition_batch(self, batch_id: str, action: str) -> dict[str, Any]:
        """批次流转：开始加工时固化计划数据源，之后仍可追溯。"""
        with self._lock:
            batch = next((row for row in self._batches if row["batch_id"] == batch_id), None)
            if batch is None:
                raise ValueError(f"批次 {batch_id} 不存在")
            now = _now()
            if action == "开始加工":
                if batch["status"] != "已提交":
                    raise ValueError(f"批次 {batch_id} 当前为「{batch['status']}」，只有已提交批次可以开始加工")
                if not batch.get("planned_sources"):
                    batch["planned_sources"] = deepcopy(batch["sources"])
                # 计划数据源在提交时已固化；开始加工不再改写，保证运行中批次可追溯。
                batch["status"] = "运行中"
                batch["started_at"] = now
            elif action == "完成加工":
                if batch["status"] != "运行中":
                    raise ValueError(f"批次 {batch_id} 当前为「{batch['status']}」，只有运行中批次可以完成")
                batch["status"] = "已完成"
                batch["completed_at"] = now
            elif action == "取消":
                if batch["status"] not in ("等待证据", "已提交"):
                    raise ValueError(f"批次 {batch_id} 当前为「{batch['status']}」，不能取消")
                batch["status"] = "已取消"
            elif action == "重新校验":
                if batch["status"] != "等待证据":
                    raise ValueError(f"批次 {batch_id} 当前为「{batch['status']}」，无需重新校验")
                evaluation = self._evaluate_date(batch["revision"], batch["partition_date"])
                batch["sources"] = deepcopy(evaluation["sources"])
                batch["blockers"] = deepcopy(evaluation["blockers"])
                batch["opened_readings"] = deepcopy(evaluation["open_readings"])
                batch["recalculated_at"] = now
                if evaluation["ready"]:
                    batch["status"] = "已提交"
                    batch["submitted_at"] = now
            else:
                raise ValueError(f"未知批次动作「{action}」")
            batch["updated_at"] = now
            return {
                "ok": True,
                "message": f"批次 {batch_id} 已{action}，当前状态「{batch['status']}」",
                "batch": self._batch_summary(batch),
            }


readability_state = PartitionReadabilityState()
