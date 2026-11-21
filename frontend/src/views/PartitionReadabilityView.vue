<template>
  <section class="page readability-page">
    <header class="page-head">
      <div>
        <h2>分区可读明细</h2>
        <p class="page-desc">
          按扫描修订校验订单与地区两份输入的分区覆盖。确认零行是有效证据，
          未实测不是；补报只写入对应修订，其他轮次的空分区结果不能替代缺失数据。
        </p>
      </div>
      <div class="page-actions">
        <select v-model="revision" data-testid="revision-select" @change="reloadAll">
          <option v-for="item in revisions" :key="item.revision" :value="item.revision">
            {{ item.revision }}（{{ item.source }}）
          </option>
        </select>
        <button class="btn" type="button" data-testid="reload" @click="reloadAll">刷新</button>
      </div>
    </header>

    <p v-if="errorMessage" class="error-text" data-testid="error">{{ errorMessage }}</p>
    <p v-if="notice" class="notice-text" data-testid="notice">{{ notice }}</p>

    <div class="stat-row">
      <article class="stat-card">
        <span class="stat-label">可读有行</span>
        <strong class="stat-value">{{ detail?.summary['可读有行'] ?? 0 }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">确认零行</span>
        <strong class="stat-value">{{ detail?.summary['确认零行'] ?? 0 }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">未实测</span>
        <strong class="stat-value">{{ detail?.summary['未实测'] ?? 0 }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">本次能否提交</span>
        <strong class="stat-value">{{ detail?.decision.can_submit ? '可以' : '需等待' }}</strong>
      </article>
    </div>

    <form class="filter-bar" @submit.prevent="reloadReading">
      <label class="filter-item">
        <span>加工日期（逗号分隔）</span>
        <input v-model="dateInput" data-testid="date-input" placeholder="2026-11-16,2026-11-17" />
      </label>
      <button class="btn primary" type="submit" data-testid="load-readability">重算可读明细</button>
    </form>

    <article v-if="detail" class="decision-card" data-testid="decision">
      <p class="decision-text">{{ detail.decision.text }}</p>
      <p class="decision-meta">
        修订：{{ detail.revision_name }} · 扫描范围：{{ detail.revision.scope.join('、') }} ·
        已覆盖：{{ detail.decision.ready_dates.join('、') || '无' }} ·
        待补证据：{{ detail.decision.waiting_dates.join('、') || '无' }}
      </p>
      <div class="option-grid">
        <div v-for="option in detail.options" :key="option.key" class="option-card">
          <h4>{{ option.label }}</h4>
          <p :class="option.can_submit ? 'ok-text' : 'wait-text'">
            {{ option.can_submit ? '可提交' : '不可提交' }}：{{ option.dates.join('、') || '—' }}
          </p>
          <p class="option-note">{{ option.note }}</p>
          <p v-if="option.open_readings.length" class="option-readings">
            会开放的读数：
            <span v-for="reading in option.open_readings" :key="reading.display" class="reading-chip">
              {{ reading.display }}
            </span>
          </p>
          <ul v-if="option.missing.length" class="missing-list">
            <li v-for="item in option.missing" :key="item.table + item.partition">
              {{ item.reason }}
            </li>
          </ul>
        </div>
      </div>
    </article>

    <table class="data-table" data-testid="readability-table">
      <thead>
        <tr>
          <th>加工日期</th>
          <th>上游输入</th>
          <th>表</th>
          <th>分区</th>
          <th>实测状态</th>
          <th>行数</th>
          <th>证据修订</th>
          <th>实测时间</th>
          <th>说明</th>
        </tr>
      </thead>
      <tbody>
        <tr
          v-for="row in detail?.rows ?? []"
          :key="row.table + row.partition"
          :data-testid="`reading-${row.role}-${row.partition}`"
          :data-state="row.reading_state"
        >
          <td>{{ row.partition_date }}</td>
          <td>{{ row.role_label }}</td>
          <td>{{ row.table }}</td>
          <td>{{ row.partition }}</td>
          <td>
            <span class="state-badge" :class="badgeClass(row.reading_state)">{{ row.reading_state }}</span>
          </td>
          <td>{{ row.rows ?? '—' }}</td>
          <td>{{ row.measured ? row.revision : '—' }}</td>
          <td>{{ row.measured_at ?? '—' }}</td>
          <td>
            {{ row.note || '—' }}
            <span v-if="row.out_of_scope" class="other-evidence">
              当前日期不在扫描范围内，已有读数（{{ row.stale_reading?.state }}，{{ row.stale_reading?.rows }} 行）不计为覆盖
            </span>
            <span
              v-for="other in row.other_revision_evidence ?? []"
              :key="other.revision"
              class="other-evidence"
            >
              另见 {{ other.revision }}：{{ other.state }}（{{ other.rows }} 行），不计入当前修订
            </span>
          </td>
        </tr>
        <tr v-if="detail && !detail.rows.length">
          <td colspan="9" class="empty-state">当前修订没有可展示的分区实测</td>
        </tr>
      </tbody>
    </table>

    <div class="panel-grid">
      <form class="panel" data-testid="report-form" @submit.prevent="submitReading">
        <h3>补报实测</h3>
        <p class="panel-hint">补报只能写入所选扫描修订；零行必须显式填写 0。</p>
        <label class="create-field">
          <span>上游输入</span>
          <select v-model="reportForm.table" data-testid="report-table">
            <option value="ods.ods_region_info">地区（ods.ods_region_info）</option>
            <option value="ods.ods_order_info">订单（ods.ods_order_info）</option>
          </select>
        </label>
        <label class="create-field">
          <span>分区日期</span>
          <input v-model="reportForm.partition" data-testid="report-partition" placeholder="2026-11-16" required />
        </label>
        <label class="create-field">
          <span>实测行数（零行填 0）</span>
          <input v-model="reportForm.rows" data-testid="report-rows" placeholder="0" required />
        </label>
        <label class="create-field">
          <span>来源说明</span>
          <input v-model="reportForm.note" data-testid="report-note" placeholder="如：地区表补报" />
        </label>
        <button class="btn primary" type="submit" data-testid="submit-reading">补入实测</button>
      </form>

      <form class="panel" data-testid="scope-form" @submit.prevent="submitScope">
        <h3>调整扫描范围</h3>
        <p class="panel-hint">
          范围变化会重算关联日期的未运行批次；已运行批次保留原计划数据源。
        </p>
        <label class="create-field">
          <span>新的扫描范围（逗号分隔）</span>
          <input v-model="scopeInput" data-testid="scope-dates" placeholder="2026-11-16,2026-11-17" required />
        </label>
        <label class="create-field">
          <span>变更原因</span>
          <input v-model="scopeReason" data-testid="scope-reason" placeholder="补齐周二的扫描范围" />
        </label>
        <button class="btn" type="submit" data-testid="submit-scope">更新范围并重算</button>
      </form>

      <form class="panel" data-testid="batch-form" @submit.prevent="submitBatches">
        <h3>生成销售日加工批次</h3>
        <p class="panel-hint">
          批次带实际会读取的订单分区与地区分区；证据不全的日期进入等待证据。
        </p>
        <label class="create-field">
          <span>加工日期（逗号分隔）</span>
          <input v-model="batchDates" data-testid="batch-dates" placeholder="2026-11-16,2026-11-17" required />
        </label>
        <label class="create-field">
          <span>备注</span>
          <input v-model="batchNote" data-testid="batch-note" placeholder="周一、周二加工请求" />
        </label>
        <button class="btn primary" type="submit" data-testid="submit-batches">提交加工请求</button>
        <button class="btn" type="button" data-testid="refresh-batches" @click="submitRefresh">
          刷新可读明细并释放等待批次
        </button>
      </form>
    </div>

    <h3 class="section-title">销售日加工批次</h3>
    <table class="data-table" data-testid="batch-table">
      <thead>
        <tr>
          <th>批次</th>
          <th>加工日期</th>
          <th>状态</th>
          <th>订单分区</th>
          <th>订单来源</th>
          <th>地区分区</th>
          <th>地区来源</th>
          <th>计划数据源（可追溯）</th>
          <th>动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="batch in batches" :key="batch.batch_id" :data-testid="`batch-${batch.batch_id}`">
          <td>{{ batch.batch_id }}</td>
          <td>{{ batch.partition_date }}</td>
          <td>{{ batch.status }}</td>
          <td>{{ batch.order_partition }}</td>
          <td>{{ batch.order_source }}</td>
          <td>{{ batch.region_partition }}</td>
          <td>{{ batch.region_source }}</td>
          <td class="planned-cell">
            <span v-for="source in batch.planned_sources" :key="source.role">
              {{ source.role_label }} {{ source.partition }}：{{ source.revision }} · {{ source.reading_state }}
            </span>
            <span v-if="!batch.planned_sources.length" class="muted">—</span>
          </td>
          <td class="row-actions">
            <button
              v-for="action in actionsFor(batch.status)"
              :key="action"
              class="link"
              type="button"
              :data-testid="`batch-action-${batch.batch_id}-${action}`"
              @click="runAction(batch.batch_id, action)"
            >
              {{ action }}
            </button>
          </td>
        </tr>
        <tr v-if="!batches.length">
          <td colspan="9" class="empty-state">尚无销售日加工批次</td>
        </tr>
      </tbody>
    </table>
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'

import {
  generateBatches,
  listBatches,
  listRevisions,
  loadReadability,
  refreshBatches,
  reportReading,
  runBatchAction,
  updateScope,
} from '@/api/readability'
import type { Batch, BatchResult, Readability, Revision } from '@/api/readability'

const revisions = ref<Revision[]>([])
const revision = ref('REV-2026-11-20-A')
const detail = ref<Readability | null>(null)
const batches = ref<Batch[]>([])
const dateInput = ref('2026-11-16,2026-11-17')
const batchDates = ref('2026-11-16,2026-11-17')
const batchNote = ref('周一、周二加工请求')
const scopeInput = ref('2026-11-16,2026-11-17')
const scopeReason = ref('')
const reportForm = ref({ table: 'ods.ods_region_info', partition: '2026-11-16', rows: '0', note: '' })
const errorMessage = ref('')
const notice = ref('')

function parseDates(value: string): string[] {
  return value
    .split(/[,，]/)
    .map((item) => item.trim())
    .filter(Boolean)
}

function badgeClass(state: string): string {
  if (state === '可读有行') return 'badge-readable'
  if (state === '确认零行') return 'badge-zero'
  return 'badge-unmeasured'
}

function actionsFor(status: string): string[] {
  if (status === '等待证据') return ['重新校验', '取消']
  if (status === '已提交') return ['开始加工', '取消']
  if (status === '运行中') return ['完成加工']
  return []
}

function applyResult(result: BatchResult) {
  notice.value = result.message
}

async function reloadAll() {
  errorMessage.value = ''
  try {
    if (!revisions.value.length) revisions.value = await listRevisions()
    if (!parseDates(dateInput.value).length) {
      const current = revisions.value.find((item) => item.revision === revision.value)
      dateInput.value = (current?.scope ?? []).join(',')
    }
    await Promise.all([reloadReading(), reloadBatches()])
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '读取失败'
  }
}

async function reloadReading() {
  errorMessage.value = ''
  try {
    detail.value = await loadReadability(revision.value, parseDates(dateInput.value))
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '可读明细读取失败'
  }
}

async function reloadBatches() {
  try {
    batches.value = await listBatches(revision.value)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '批次读取失败'
  }
}

async function submitReading() {
  errorMessage.value = ''
  try {
    const result = await reportReading({ revision: revision.value, ...reportForm.value })
    applyResult(result)
    await Promise.all([reloadReading(), reloadBatches(), loadRevisions()])
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '补报失败'
  }
}

async function loadRevisions() {
  revisions.value = await listRevisions()
}

async function submitScope() {
  errorMessage.value = ''
  try {
    const result = await updateScope({
      revision: revision.value,
      dates: parseDates(scopeInput.value),
      reason: scopeReason.value,
    })
    applyResult(result)
    dateInput.value = scopeInput.value
    await Promise.all([reloadReading(), reloadBatches(), loadRevisions()])
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '范围更新失败'
  }
}

async function submitBatches() {
  errorMessage.value = ''
  try {
    const result = await generateBatches({
      revision: revision.value,
      dates: parseDates(batchDates.value),
      note: batchNote.value,
    })
    applyResult(result)
    await Promise.all([reloadReading(), reloadBatches()])
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '加工请求提交失败'
  }
}

async function submitRefresh() {
  errorMessage.value = ''
  try {
    const result = await refreshBatches({ revision: revision.value, dates: parseDates(batchDates.value) })
    applyResult(result)
    await Promise.all([reloadReading(), reloadBatches()])
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '刷新失败'
  }
}

async function runAction(batchId: string, action: string) {
  errorMessage.value = ''
  try {
    const result = await runBatchAction(batchId, action)
    applyResult(result)
    await reloadBatches()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '批次操作失败'
  }
}

onMounted(reloadAll)
</script>

<style scoped>
.readability-page select {
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 6px 10px;
  background: #fff;
}
.notice-text {
  color: #1d4ed8;
  font-size: 13px;
}
.decision-card {
  background: #fff;
  border: 1px solid var(--border);
  border-left: 4px solid var(--brand);
  border-radius: 8px;
  padding: 12px 16px;
  margin-bottom: 12px;
}
.decision-text {
  font-size: 14px;
  font-weight: 600;
  margin: 0 0 6px;
}
.decision-meta {
  color: var(--muted);
  font-size: 12px;
  margin: 0 0 10px;
}
.option-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
  gap: 12px;
}
.option-card {
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 10px 12px;
  background: #fafbfc;
}
.option-card h4 {
  margin: 0 0 6px;
  font-size: 13px;
}
.ok-text { color: #15803d; font-size: 12px; margin: 0 0 6px; }
.wait-text { color: #b45309; font-size: 12px; margin: 0 0 6px; }
.option-note { color: var(--muted); font-size: 12px; margin: 0 0 6px; }
.option-readings { font-size: 12px; margin: 0 0 6px; }
.reading-chip {
  display: block;
  color: #1f2937;
  background: #eef2ff;
  border-radius: 4px;
  padding: 2px 6px;
  margin: 3px 0;
}
.missing-list {
  margin: 0;
  padding-left: 18px;
  color: #b42318;
  font-size: 12px;
}
.state-badge {
  border-radius: 999px;
  padding: 2px 8px;
  font-size: 12px;
  white-space: nowrap;
}
.badge-readable { background: #dcfce7; color: #166534; }
.badge-zero { background: #e2e8f0; color: #334155; }
.badge-unmeasured { background: #fee2e2; color: #991b1b; }
.other-evidence {
  display: block;
  color: var(--muted);
  font-size: 12px;
  margin-top: 2px;
}
.panel-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
  gap: 12px;
  margin: 14px 0;
}
.panel {
  background: #fff;
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 12px 16px;
}
.panel h3 { margin: 0 0 4px; font-size: 14px; }
.panel-hint { color: var(--muted); font-size: 12px; margin: 0 0 10px; }
.panel select { width: 100%; box-sizing: border-box; }
.panel .btn { margin-right: 8px; }
.section-title { font-size: 15px; margin: 18px 0 8px; }
.planned-cell span { display: block; font-size: 12px; }
.muted { color: var(--muted); }
</style>
