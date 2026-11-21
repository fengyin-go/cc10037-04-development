"""分区可读明细与销售日加工批次接口。

依赖调度读取分区可读明细来生成销售日加工批次；批次固化实际会读取的
订单/地区分区，并在扫描范围变化时保留已运行批次的原计划数据源。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.services.partition_readability import readability_state

router = APIRouter(prefix="/api/readability", tags=["分区可读明细"])


def _fail(message: str) -> HTTPException:
    return HTTPException(status_code=400, detail=message)


@router.get("/revisions")
def list_revisions() -> dict[str, Any]:
    """列出扫描修订及其覆盖情况。"""
    return {"items": readability_state.list_revisions()}


@router.get("/readings")
def get_readings(
    revision: str | None = Query(default=None),
    dates: str | None = Query(default=None, description="逗号分隔的加工日期"),
) -> dict[str, Any]:
    """读取指定修订的分区可读明细：确认零行、可读有行、未实测。"""
    try:
        return readability_state.get_readability(revision, dates.split(",") if dates else None)
    except ValueError as exc:
        raise _fail(str(exc)) from exc


@router.post("/readings")
def record_reading(body: dict[str, Any]) -> dict[str, Any]:
    """补报实测；必须写入对应扫描修订，不能替代其他修订的缺失数据。"""
    try:
        return readability_state.record_reading(
            revision=body.get("revision"),
            table=body.get("table"),
            role=body.get("role"),
            partition=body.get("partition"),
            rows=body.get("rows"),
            source=str(body.get("source") or "补报扫描"),
            note=str(body.get("note") or ""),
        )
    except ValueError as exc:
        raise _fail(str(exc)) from exc


@router.post("/scope")
def update_scope(body: dict[str, Any]) -> dict[str, Any]:
    """调整扫描修订的扫描范围，并重算关联日期上的未运行批次。"""
    try:
        return readability_state.update_scope(
            revision=str(body.get("revision") or ""),
            dates=body.get("dates") or [],
            reason=str(body.get("reason") or ""),
        )
    except ValueError as exc:
        raise _fail(str(exc)) from exc


@router.get("/batches")
def list_batches(
    revision: str | None = Query(default=None),
    date: str | None = Query(default=None),
    status: str | None = Query(default=None),
) -> dict[str, Any]:
    """列出销售日加工批次及其计划/实际数据源。"""
    try:
        return {"items": readability_state.list_batches(
            revision=revision, date=date, status=status)}
    except ValueError as exc:
        raise _fail(str(exc)) from exc


@router.post("/sales-batches")
def generate_sales_batches(body: dict[str, Any]) -> dict[str, Any]:
    """按分区可读明细生成销售日加工批次。"""
    try:
        return readability_state.generate_batches(
            revision=str(body.get("revision") or ""),
            dates=body.get("dates") or [],
            note=str(body.get("note") or ""),
        )
    except ValueError as exc:
        raise _fail(str(exc)) from exc


@router.post("/sales-batches/refresh")
def refresh_sales_batches(body: dict[str, Any]) -> dict[str, Any]:
    """刷新可读明细，释放同一修订下已具备实测证据的等待批次。"""
    try:
        return readability_state.refresh_batches(
            revision=body.get("revision"),
            dates=body.get("dates") or None,
        )
    except ValueError as exc:
        raise _fail(str(exc)) from exc


@router.post("/batches/{batch_id}/actions")
def run_batch_action(batch_id: str, body: dict[str, Any]) -> dict[str, Any]:
    """批次流转：开始加工会固化计划数据源。"""
    try:
        return readability_state.transition_batch(batch_id, str(body.get("action") or "").strip())
    except ValueError as exc:
        raise _fail(str(exc)) from exc
