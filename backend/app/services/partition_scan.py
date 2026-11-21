"""分区扫描领域服务：管理数据表分区扫描、倾斜检测和合并建议。

上游：sliding_window 产出窗口配置 → partition_scan 扫描分区
消费：quality_rule 根据扫描结果调整数据质量校验策略
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from app.services.base import BaseModuleService
from app.store import store


class PartitionScanService(BaseModuleService):
    MODULE = "partition_scan"
    ENTITY = "扫描报告"
    OPERATION_TERM = "归档"
    LIST_FIELDS = ["报告时间", "目标表", "分区总数", "空分区数", "分区倾斜度", "小分区数", "扫描建议"]
    REQUIRED_FIELDS = ["报告时间", "目标表", "分区总数"]
    STATUS_ORDER = ["扫描中", "已生成", "已发布", "已归档"]
    ACTION_RULES = {"生成报告": "扫描中", "发布报告": "已生成", "归档报告": "已发布"}
    NEGATIVE_ACTIONS = []

    def query_by_table(self, table_name: str) -> list[dict[str, Any]]:
        """按目标表查询分区扫描报告。

        从 store 中筛选指定表的分区扫描记录，
        返回报告列表供分区管理页面消费。
        """
        result: list[dict[str, Any]] = []
        for row in store.rows(self.MODULE):
            if table_name.casefold() in str(row.get("目标表", "")).casefold():
                result.append(dict(row))
        return result

    def detect_skew(self, table_name: str) -> dict[str, Any]:
        """检测分区数据倾斜。

        根据分区倾斜度字段，判断表中分区分布是否均匀，
        当倾斜度过高时产出重分区建议。
        """
        reports = self.query_by_table(table_name)
        if not reports:
            return {"table": table_name, "reports": 0, "message": "无扫描报告"}

        latest = reports[-1]
        try:
            skew = float(latest.get("分区倾斜度", "0"))
            small_part = int(latest.get("小分区数", "0"))
            total_part = int(latest.get("分区总数", "0"))
        except ValueError:
            skew = 0
            small_part = 0
            total_part = 0

        is_skewed = skew > 30 or (small_part > 0 and small_part / max(total_part, 1) > 0.3)
        recommendation = (
            "建议对高倾斜分区进行重分区操作" if is_skewed
            else "分区分布正常"
        )

        return {
            "table": table_name,
            "total_partitions": total_part,
            "small_partitions": small_part,
            "skew_score": skew,
            "is_skewed": is_skewed,
            "recommendation": recommendation,
            "detected_at": datetime.now().isoformat(),
        }

    def find_empty_partitions(self, table_name: str) -> list[dict[str, Any]]:
        """查找空分区。

        筛选空分区数量大于零的扫描报告，
        返回空分区详情供清理和优化建议。

        注意：这里只给出按报告折叠后的空分区总数，无法判断哪些上游输入
        可读。依赖调度请改用 partition_readability 的分区可读明细。
        """
        reports = self.query_by_table(table_name)
        empty_list: list[dict[str, Any]] = []
        for report in reports:
            empty_count = int(report.get("空分区数", "0"))
            if empty_count > 0:
                empty_list.append({
                    "report_time": report.get("报告时间"),
                    "empty_count": empty_count,
                    "total_partitions": report.get("分区总数"),
                    "suggestion": "建议清理空分区或调整分区策略",
                    "detail_endpoint": "/api/readability/readings",
                })

        return empty_list

    def compute_partition_health(self) -> dict[str, Any]:
        """计算全局分区健康度。

        扫描 store 中所有表的分区报告，统计倾斜表、
        空分区表、小分区表的比例，产出健康分。
        """
        rows = store.rows(self.MODULE)
        tables: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            table = str(row.get("目标表", ""))
            tables.setdefault(table, []).append(row)

        total_tables = len(tables)
        healthy = 0
        skewed = 0
        has_empty = 0

        for table, reports in tables.items():
            latest = reports[-1]
            try:
                skew = float(latest.get("分区倾斜度", "0"))
                empty = int(latest.get("空分区数", "0"))
            except ValueError:
                skew = 0
                empty = 0

            if skew <= 15 and empty == 0:
                healthy += 1
            else:
                if skew > 15:
                    skewed += 1
                if empty > 0:
                    has_empty += 1

        health_score = round(healthy / max(total_tables, 1) * 100, 1)
        return {
            "total_tables_scanned": total_tables,
            "healthy_tables": healthy,
            "skewed_tables": skewed,
            "tables_with_empty_partitions": has_empty,
            "health_score_pct": health_score,
            "snapshot_at": datetime.now().isoformat(),
        }

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry, msg = super().run_action(entry_id, action)
        if entry:
            entry["last_operated_at"] = datetime.now().isoformat()
        return entry, msg