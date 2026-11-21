# ETL编排与数据质量中心

面向数据仓库ETL作业场景，提供流水线编排、质量规则、依赖调度和回刷补偿的ETL治理平台

这是一个前后端分离的管理平台：前端 Vue 3 + Vite + TypeScript，后端 FastAPI（Python）。
两边各自独立启动，前端 dev server 已关掉自动打开页面，启动后按终端打印的地址手工打开。

## 目录结构

```text
.
├── frontend/                 Vue 3 + Vite + TypeScript 前端
│   ├── src/views/WorkbenchView.vue|tsx  全部模块共享工作台
│   ├── src/data/modules.ts              18个业务模块配置
│   ├── src/api/              统一请求封装
│   └── vite.config.ts        dev server 配置（open: false）
├── backend/                  FastAPI（Python） 后端
│   ├── app/routers/resource.py  通用查询/导出/登记/动作接口
│   ├── app/services/<key>.py    每个模块独立领域逻辑
│   ├── app/services/base.py     共享服务骨架
│   └── app/store.py          内存数据仓库与示例数据
├── .gitignore
└── docker-compose.yml
```

## 启动

### 后端

```bash
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
./run.sh
```

健康检查：`curl http://127.0.0.1:8000/api/health`

### 前端

```bash
cd frontend
npm ci
npm run dev
```

前端默认监听 `http://127.0.0.1:5173/`，dev server 不会自动打开浏览器，
需要自己访问。`/api` 由 vite 代理到后端 `http://127.0.0.1:8000`。

## 业务模块

| 模块 | 目录 | 业务对象 | 主要字段 |
| --- | --- | --- | --- |
| 流水线定义 | `pipeline_def` | ETL流水线 | 流水线名称、处理阶段、输入源表 |
| 任务节点 | `task_node` | 处理节点 | 节点名称、所属管线、节点类型 |
| 质量规则 | `quality_rule` | 校验规则 | 规则名称、目标表、校验字段 |
| 质量执行 | `validation_run` | 校验批次 | 批次标识、目标表、校验分区 |
| 依赖调度 | `depend_scheduler` | 调度配置 | 配置名称、目标管线、触发方式 |
| 滑动窗口 | `sliding_window` | 窗口配置 | 窗口名称、目标表、窗口字段 |
| 回刷补偿 | `rerun_compensation` | 重跑任务 | 任务名称、目标管线、重跑范围 |
| 水位追踪 | `watermark_tracker` | 水位记录 | 管线标识、分区字段、当前水位 |
| 资源池 | `resource_pool` | 计算资源 | 池名称、总核数、总内存 |
| 血缘图谱 | `lineage_graph` | 血缘边 | 源节点、目标节点、传递字段 |
| 异常捕获 | `exception_capture` | 异常记录 | 发生时间、所属管线、异常类型 |
| 模板仓库 | `template_store` | 处理模板 | 模板名称、处理类型、输入Schema |
| 分区扫描 | `partition_scan` | 扫描报告 | 报告时间、目标表、分区总数 |
| 配置迁移 | `config_migration` | 迁移任务 | 任务名称、源环境、目标环境 |
| Schema演进 | `schema_evolution` | 变更记录 | 变更时间、目标表、变更类型 |
| 回填追踪 | `backfill_tracker` | 回填进度 | 追踪ID、回填任务、当前分区 |
| 成本分析 | `cost_analysis` | 成本快照 | 快照日期、管线标识、计算时长 |
| 告警策略 | `alert_policy` | 告警规则 | 规则名称、监控对象、告警条件 |

## 分区可读明细

分区扫描报告只把空分区折成一个汇总数，无法判断销售日加工的两份输入
（`ods.ods_order_info` 订单、`ods.ods_region_info` 地区）是否可读。
`/partition_readability` 页面和 `/api/readability/*` 接口提供按扫描修订组织的明细视图：

- 每个上游分区落在「确认零行」「可读有行」「未实测」三者之一；
  确认零行是有效证据，未实测不是，且不能按零行处理。
- 明细只认同一扫描修订内的实测；补报必须指定修订并写入该修订，
  其他轮次的空分区结果不会替代缺失数据。
- 视图同时给出本次能否提交的判断：可先提交哪些日期、还要等哪些日期，
  以及每种选择会开放哪些读数。
- 调整扫描范围会重算关联日期上尚未提交的批次；已提交/已运行批次
  保留提交时固化的计划数据源，仍可追溯。

依赖调度读取该明细生成「销售日加工」批次，每个加工日期一个批次，
带实际会读取的订单分区和地区分区；证据齐全的批次直接提交，其余进入
「等待证据」，补报后刷新明细即可释放。

```bash
# 查看修订与可读明细
curl 'http://127.0.0.1:8000/api/readability/revisions'
curl 'http://127.0.0.1:8000/api/readability/readings?revision=REV-2026-11-20-A&dates=2026-11-16,2026-11-17'
# 补报实测（零行必须显式 rows=0）
curl -X POST http://127.0.0.1:8000/api/readability/readings \
  -H 'Content-Type: application/json' \
  -d '{"revision":"REV-2026-11-20-A","table":"ods.ods_region_info","partition":"2026-11-16","rows":0}'
# 生成销售日加工批次
curl -X POST http://127.0.0.1:8000/api/readability/sales-batches \
  -H 'Content-Type: application/json' \
  -d '{"revision":"REV-2026-11-20-A","dates":["2026-11-16","2026-11-17"]}'
# 补报后刷新明细，释放等待批次
curl -X POST http://127.0.0.1:8000/api/readability/sales-batches/refresh \
  -H 'Content-Type: application/json' -d '{"revision":"REV-2026-11-20-A"}'
```

## 约定

- 全部模块共享`frontend/src/views/WorkbenchView.vue|tsx`和一套资源路由。
- 每个模块仍保留独立`backend/app/services/<模块>.py`；纯前端保留
  `frontend/src/domain/<模块>.ts`，用于承载真实业务差异并维持36题位容量。
- 列表接口统一返回 `{ items, total, page, size }`，动作接口统一返回 `{ ok, message }`。
- 状态流转只允许在 `app/services` 里改，路由层不做业务判断。

领域：数据工程
子方向：数仓与数据湖
