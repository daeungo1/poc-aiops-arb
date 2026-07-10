// ─── 대시보드 팝업 컴포넌트 모음 ────────────────────────────────────────────
// ScoreRangePopup / AvgScorePopup / TrendDatePopup 세 팝업을 독립 컴포넌트로 제공.
// DashboardPage.tsx에서 import 하여 사용.

import type { ReactNode } from 'react';
import { useEffect, useRef, useState } from 'react';

// ─── 공유 타입 ────────────────────────────────────────────────────────────────

export interface ResourceRow {
  resource_name: string;
  resource_type: string;
  resource_group: string;
  assessment_time: string;
  overall_score: number;
  total_checks: number;
  passed: number;
  failed: number;
  warnings: number;
  assessment_filename?: string;
  report_id?: number | null;
  no_checklist?: boolean;
  subscription_name?: string;
}

export interface CheckResult {
  status: string;
  severity: string;
  question: string;
  finding: string;
  recommendation: string;
  evidence_property: string | null;
  evidence_actual: string | null;
  evidence_expected: string | null;
  checklist_name: string | null;
}

export interface ResourceDetailData {
  resource_name: string;
  resource_type: string;
  resource_group: string;
  overall_score: number;
  total_checks: number;
  passed: number;
  failed: number;
  warnings: number;
  assessment_time: string;
  subscription_id: string;
  subscription_name: string;
  check_results: CheckResult[];
}

// ─── 점수 색상 유틸 ──────────────────────────────────────────────────────────

function scoreColor(score: number): string {
  if (score <= 20) return '#ef4444';
  if (score <= 40) return '#f97316';
  if (score <= 60) return '#f59e0b';
  if (score <= 80) return '#84cc16';
  return '#10b981';
}

// ─── 공유 상수 ────────────────────────────────────────────────────────────────

export const SCORE_RANGE_COLORS = ['#ef4444', '#f97316', '#f59e0b', '#84cc16', '#10b981'];

export const SCORE_RANGES: { label: string; filter: (s: number) => boolean }[] = [
  { label: '0-20',   filter: (s) => s <= 20 },
  { label: '21-40',  filter: (s) => s > 20 && s <= 40 },
  { label: '41-60',  filter: (s) => s > 40 && s <= 60 },
  { label: '61-80',  filter: (s) => s > 60 && s <= 80 },
  { label: '81-100', filter: (s) => s > 80 },
];

// ─── 팝업 데이터 타입 ─────────────────────────────────────────────────────────

export interface ScoreRangePopupData {
  label: string;
  colorIndex: number;
  resources: ResourceRow[];
  loading?: boolean;
}

export interface TrendDatePopupData {
  date: string;
  score: number;
  resources: ResourceRow[];
  loading: boolean;
}

// ─── 내부 공유 컴포넌트 ───────────────────────────────────────────────────────

function PopupShell({ onClose, header, children, maxWidth = 'max-w-2xl' }: {
  onClose: () => void;
  header: ReactNode;
  children: ReactNode;
  maxWidth?: string;
}) {
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 backdrop-blur-sm"
      onClick={onClose}
    >
      <div
        className={`bg-white rounded-2xl shadow-2xl w-full ${maxWidth} mx-4 flex flex-col max-h-[85vh]`}
        onClick={e => e.stopPropagation()}
      >
        <div className="flex items-start justify-between px-6 py-4 border-b border-slate-100 gap-4">
          <div className="flex-1 min-w-0">{header}</div>
          <button
            className="shrink-0 p-1.5 text-slate-400 hover:text-slate-600 hover:bg-slate-100 rounded-lg transition-colors mt-0.5"
            onClick={onClose}
          >
            ✕
          </button>
        </div>
        <div className="overflow-y-auto flex-1 px-6 py-4">{children}</div>
      </div>
    </div>
  );
}

// ─── 리사이즈 가능한 공용 리소스 테이블 ─────────────────────────────────────

const RESOURCE_COL_DEFS = [
  { key: 'subscription_name', label: '구독',          defaultW: 160 },
  { key: 'resource_group',    label: '그룹',          defaultW: 130 },
  { key: 'resource_name',     label: '리소스명',      defaultW: 200 },
  { key: 'resource_type',     label: '유형',          defaultW: 120 },
  { key: 'overall_score',     label: '점수',          defaultW: 140 },
  { key: 'pass_fail',         label: '성공/실패/경고', defaultW: 130 },
] as const;

function ResourceResizableTable({ rows }: { rows: ResourceRow[] }) {
  const [widths, setWidths] = useState<number[]>(RESOURCE_COL_DEFS.map(c => c.defaultW));
  const tableRef = useRef<HTMLTableElement>(null);

  const startResize = (idx: number) => (e: React.MouseEvent) => {
    e.preventDefault();
    const startX = e.clientX;
    const startW = widths[idx];
    const cols = tableRef.current?.querySelectorAll<HTMLElement>('col');
    const onMove = (ev: MouseEvent) => {
      const newW = Math.max(50, startW + ev.clientX - startX);
      if (cols?.[idx]) cols[idx].style.width = `${newW}px`;
    };
    const onUp = (ev: MouseEvent) => {
      const newW = Math.max(50, startW + ev.clientX - startX);
      setWidths(prev => { const next = [...prev]; next[idx] = newW; return next; });
      window.removeEventListener('mousemove', onMove);
      window.removeEventListener('mouseup', onUp);
    };
    window.addEventListener('mousemove', onMove);
    window.addEventListener('mouseup', onUp);
  };

  return (
    <div className="overflow-x-auto rounded-lg border border-slate-200">
      <table
        ref={tableRef}
        style={{ tableLayout: 'fixed', width: '100%', borderCollapse: 'collapse' }}
      >
        <colgroup>
          {widths.map((w, i) => <col key={i} style={{ width: w }} />)}
        </colgroup>
        <thead>
          <tr className="bg-slate-50 border-b border-slate-200">
            {RESOURCE_COL_DEFS.map((col, i) => (
              <th
                key={col.key}
                className="relative text-left text-xs font-semibold text-slate-500 uppercase tracking-wide px-3 py-2.5 select-none whitespace-nowrap"
              >
                {col.label}
                {i < RESOURCE_COL_DEFS.length - 1 && (
                  <span
                    className="absolute right-0 top-0 h-full w-1 cursor-col-resize group"
                    onMouseDown={startResize(i)}
                  >
                    <span className="block w-px h-full bg-slate-200 group-hover:bg-blue-400 mx-auto transition-colors" />
                  </span>
                )}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-100">
          {rows.map((r, i) => {
            const rangeIdx = SCORE_RANGES.findIndex(sr => sr.filter(r.overall_score));
            const color = SCORE_RANGE_COLORS[rangeIdx] ?? '#64748b';
            return (
              <tr key={i} className="hover:bg-slate-50 transition-colors">
                <td className="px-3 py-2.5 text-xs text-slate-500 overflow-hidden text-ellipsis whitespace-nowrap" title={r.subscription_name}>
                  {r.subscription_name || '–'}
                </td>
                <td className="px-3 py-2.5 text-xs text-slate-500 overflow-hidden text-ellipsis whitespace-nowrap" title={r.resource_group}>
                  {r.resource_group || '–'}
                </td>
                <td className="px-3 py-2.5 text-sm font-medium text-slate-800 overflow-hidden text-ellipsis whitespace-nowrap" title={r.resource_name}>
                  {r.resource_name}
                </td>
                <td className="px-3 py-2.5 overflow-hidden whitespace-nowrap">
                  <span className="bg-slate-100 text-slate-600 text-xs px-2 py-0.5 rounded">
                    {r.resource_type.split('/').pop()}
                  </span>
                </td>
                <td className="px-3 py-2.5">
                  <div className="flex items-center gap-2">
                    <div className="w-16 h-1.5 bg-slate-100 rounded-full overflow-hidden">
                      <div className="h-full rounded-full" style={{ width: `${Math.round(r.overall_score)}%`, backgroundColor: color }} />
                    </div>
                    <span className="text-sm font-bold" style={{ color }}>{r.overall_score.toFixed(1)}</span>
                  </div>
                </td>
                <td className="px-3 py-2.5 text-xs text-slate-500 whitespace-nowrap">
                  <span className="text-emerald-600 font-medium">{r.passed}</span>
                  {' / '}
                  <span className="text-rose-500 font-medium">{r.failed}</span>
                  {' / '}
                  <span className="text-amber-500 font-medium">{r.warnings}</span>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function Empty({ message }: { message: string }) {
  return <div className="py-12 text-center text-slate-400 text-sm">{message}</div>;
}

/** 체크리스트 타입 불일치 리소스 목록 (팝업 하단 접이식 섹션) */
function NoChecklistSection({ resources }: { resources: ResourceRow[] }) {
  const [open, setOpen] = useState(false);
  if (resources.length === 0) return null;
  return (
    <div className="border-t border-slate-100 bg-slate-50">
      <button
        type="button"
        className="w-full px-5 py-3 flex items-center gap-2 hover:bg-slate-100 transition-colors text-left"
        onClick={() => setOpen(v => !v)}
      >
        <span className="text-xs font-semibold text-slate-400 uppercase tracking-wider">
          체크리스트 타입 불일치
        </span>
        <span className="text-xs bg-slate-200 text-slate-500 px-1.5 py-0.5 rounded-full font-medium">
          {resources.length}개
        </span>
        <span className="text-xs text-slate-400">— 점수 계산에서 제외됨</span>
        <span className="ml-auto text-slate-400 text-xs">{open ? '▲ 접기' : '▼ 펼치기'}</span>
      </button>
      {open && (
        <table className="w-full text-left text-sm border-collapse">
          <tbody>
            {resources.map((r, i) => (
              <tr key={i} className="border-t border-slate-100 opacity-60">
                <td className="py-2 px-5 text-slate-500 font-medium max-w-[180px] truncate" title={r.resource_name}>
                  {r.resource_name}
                </td>
                <td className="py-2 px-5">
                  <span className="bg-slate-100 text-slate-400 text-xs px-2 py-0.5 rounded">
                    {r.resource_type.split('/').pop()}
                  </span>
                </td>
                <td className="py-2 px-5 text-xs text-slate-400 max-w-[120px] truncate" title={r.resource_group}>
                  {r.resource_group || '–'}
                </td>
                <td className="py-2 px-5 text-right">
                  <span className="text-xs text-slate-400 bg-slate-100 px-2 py-0.5 rounded">타입 불일치</span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

function ScoreBadge({ score }: { score: number }) {
  const cls = score >= 80
    ? 'bg-emerald-50 text-emerald-700'
    : score >= 60
    ? 'bg-amber-50 text-amber-700'
    : 'bg-rose-50 text-rose-700';
  return (
    <span className={`text-sm font-extrabold px-2.5 py-0.5 rounded-full ${cls}`}>
      평균 {score} 점
    </span>
  );
}

// ─── ScoreRangePopup ─────────────────────────────────────────────────────────

export function ScoreRangePopup({ data, onClose }: {
  data: ScoreRangePopupData;
  onClose: () => void;
}) {
  const applicable = data.resources.filter(r => !r.no_checklist).sort((a, b) => b.overall_score - a.overall_score);
  const noChecklist = data.resources.filter(r => r.no_checklist);

  return (
    <PopupShell
      onClose={onClose}
      maxWidth="max-w-5xl"
      header={
        <div className="flex items-center gap-2 flex-wrap">
          <span
            className="w-3 h-3 rounded-full shrink-0"
            style={{ backgroundColor: SCORE_RANGE_COLORS[data.colorIndex] }}
          />
          <h3 className="text-base font-bold text-slate-800">점수 {data.label} 구간 리소스</h3>
          {!data.loading && (
            <span className="text-xs font-semibold text-slate-500 bg-slate-100 px-2 py-0.5 rounded-full">
              {applicable.length}개
            </span>
          )}
        </div>
      }
    >
      {data.loading ? (
        <Empty message="불러오는 중…" />
      ) : applicable.length === 0 ? (
        <Empty message="해당 구간의 리소스가 없습니다." />
      ) : (
        <ResourceResizableTable rows={applicable} />
      )}
      {!data.loading && <NoChecklistSection resources={noChecklist} />}
    </PopupShell>
  );
}

// ─── AvgScorePopup ───────────────────────────────────────────────────────────

const AVG_COL_DEFS = [
  { key: 'subscription_name', label: '구독',       defaultW: 160 },
  { key: 'resource_group',    label: '그룹',       defaultW: 130 },
  { key: 'resource_name',     label: '리소스명',   defaultW: 200 },
  { key: 'resource_type',     label: '유형',       defaultW: 120 },
  { key: 'assessment_time',   label: '최근 진단일', defaultW: 110 },
  { key: 'overall_score',     label: '점수',       defaultW: 140 },
] as const;

function AvgScoreTable({ rows }: { rows: ResourceRow[] }) {
  const [widths, setWidths] = useState<number[]>(AVG_COL_DEFS.map(c => c.defaultW));
  const tableRef = useRef<HTMLTableElement>(null);

  const startResize = (idx: number) => (e: React.MouseEvent) => {
    e.preventDefault();
    const startX = e.clientX;
    const startW = widths[idx];
    const cols = tableRef.current?.querySelectorAll<HTMLElement>('col');
    const onMove = (ev: MouseEvent) => {
      const newW = Math.max(50, startW + ev.clientX - startX);
      if (cols?.[idx]) cols[idx].style.width = `${newW}px`;
    };
    const onUp = (ev: MouseEvent) => {
      const newW = Math.max(50, startW + ev.clientX - startX);
      setWidths(prev => { const next = [...prev]; next[idx] = newW; return next; });
      window.removeEventListener('mousemove', onMove);
      window.removeEventListener('mouseup', onUp);
    };
    window.addEventListener('mousemove', onMove);
    window.addEventListener('mouseup', onUp);
  };

  return (
    <div className="overflow-x-auto rounded-lg border border-slate-200">
      <table
        ref={tableRef}
        style={{ tableLayout: 'fixed', width: '100%', borderCollapse: 'collapse' }}
      >
        <colgroup>
          {widths.map((w, i) => <col key={i} style={{ width: w }} />)}
        </colgroup>
        <thead>
          <tr className="bg-slate-50 border-b border-slate-200">
            {AVG_COL_DEFS.map((col, i) => (
              <th
                key={col.key}
                className="relative text-left text-xs font-semibold text-slate-500 uppercase tracking-wide px-3 py-2.5 select-none whitespace-nowrap"
              >
                {col.label}
                {i < AVG_COL_DEFS.length - 1 && (
                  <span
                    className="absolute right-0 top-0 h-full w-1 cursor-col-resize group"
                    onMouseDown={startResize(i)}
                  >
                    <span className="block w-px h-full bg-slate-200 group-hover:bg-blue-400 mx-auto transition-colors" />
                  </span>
                )}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-100">
          {rows.map((r, i) => {
            const rangeIdx = SCORE_RANGES.findIndex(sr => sr.filter(r.overall_score));
            const color = SCORE_RANGE_COLORS[rangeIdx] ?? '#64748b';
            return (
              <tr key={i} className="hover:bg-slate-50 transition-colors">
                <td className="px-3 py-2.5 text-xs text-slate-500 overflow-hidden text-ellipsis whitespace-nowrap" title={r.subscription_name}>
                  {r.subscription_name || '–'}
                </td>
                <td className="px-3 py-2.5 text-xs text-slate-500 overflow-hidden text-ellipsis whitespace-nowrap" title={r.resource_group}>
                  {r.resource_group || '–'}
                </td>
                <td className="px-3 py-2.5 text-sm font-medium text-slate-800 overflow-hidden text-ellipsis whitespace-nowrap" title={r.resource_name}>
                  {r.resource_name}
                </td>
                <td className="px-3 py-2.5 overflow-hidden whitespace-nowrap">
                  <span className="bg-slate-100 text-slate-600 text-xs px-2 py-0.5 rounded">
                    {r.resource_type.split('/').pop()}
                  </span>
                </td>
                <td className="px-3 py-2.5 text-xs text-slate-500 whitespace-nowrap">
                  {r.assessment_time ? r.assessment_time.slice(0, 10) : '–'}
                </td>
                <td className="px-3 py-2.5">
                  <div className="flex items-center justify-end gap-2">
                    <div className="w-16 h-1.5 bg-slate-100 rounded-full overflow-hidden">
                      <div className="h-full rounded-full" style={{ width: `${Math.round(r.overall_score)}%`, backgroundColor: color }} />
                    </div>
                    <span className="text-sm font-bold w-10 text-right" style={{ color }}>{r.overall_score.toFixed(1)}</span>
                  </div>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

export function AvgScorePopup({ avgScore, resources, onClose }: {
  avgScore: number;
  resources: ResourceRow[];
  onClose: () => void;
}) {
  const applicable = resources.filter(r => !r.no_checklist).sort((a, b) => b.overall_score - a.overall_score);
  const noChecklist = resources.filter(r => r.no_checklist);

  return (
    <PopupShell
      onClose={onClose}
      maxWidth="max-w-5xl"
      header={
        <>
          <h3 className="text-base font-bold text-slate-800">평균 점수 근거 자료</h3>
          <ScoreBadge score={avgScore} />
          <span className="text-xs text-slate-400 bg-slate-100 px-2 py-0.5 rounded-full">
            리소스 {applicable.length}개 기준
          </span>
        </>
      }
    >
      {applicable.length === 0 ? (
        <Empty message="데이터가 없습니다." />
      ) : (
        <AvgScoreTable rows={applicable} />
      )}
      <NoChecklistSection resources={noChecklist} />
    </PopupShell>
  );
}

// ─── TrendDatePopup ──────────────────────────────────────────────────────────

export function TrendDatePopup({ data, onClose }: {
  data: TrendDatePopupData;
  onClose: () => void;
}) {
  const applicable = data.resources.filter(r => !r.no_checklist);
  const noChecklist = data.resources.filter(r => r.no_checklist);

  return (
    <PopupShell
      onClose={onClose}
      maxWidth="max-w-5xl"
      header={
        <div className="flex items-center gap-2 flex-wrap">
          <h3 className="text-base font-bold text-slate-800">{data.date} 평가 결과</h3>
          {!data.loading && (
            <>
              <ScoreBadge score={data.score} />
              <span className="text-xs font-semibold text-slate-500 bg-slate-100 px-2 py-0.5 rounded-full">
                {applicable.length}개 리소스
              </span>
            </>
          )}
        </div>
      }
    >
      {data.loading ? (
        <Empty message="불러오는 중…" />
      ) : applicable.length === 0 && noChecklist.length === 0 ? (
        <Empty message="해당 일자의 리소스 데이터가 없습니다." />
      ) : (
        <>
          {applicable.length > 0 && <ResourceResizableTable rows={applicable} />}
          <NoChecklistSection resources={noChecklist} />
        </>
      )}
    </PopupShell>
  );
}

// ─── 리소스 상세 팝업 ────────────────────────────────────────────────────────

const STATUS_STYLE: Record<string, { label: string; bg: string; text: string }> = {
  fail:          { label: '실패',        bg: 'bg-rose-100',   text: 'text-rose-700'   },
  warning:       { label: '경고',        bg: 'bg-amber-100',  text: 'text-amber-700'  },
  manual_review: { label: '수동검토',    bg: 'bg-blue-100',   text: 'text-blue-700'   },
  pass:          { label: '통과',        bg: 'bg-emerald-100',text: 'text-emerald-700'},
  n_a:           { label: 'N/A',         bg: 'bg-slate-100',  text: 'text-slate-500'  },
};

const SEVERITY_STYLE: Record<string, { label: string; bg: string; text: string }> = {
  critical: { label: '심각', bg: 'bg-rose-100',   text: 'text-rose-700'   },
  high:     { label: '높음', bg: 'bg-orange-100', text: 'text-orange-700' },
  medium:   { label: '중간', bg: 'bg-amber-100',  text: 'text-amber-700'  },
  low:      { label: '낮음', bg: 'bg-slate-100',  text: 'text-slate-500'  },
};

function StatusBadge({ status }: { status: string }) {
  const s = STATUS_STYLE[status] ?? { label: status, bg: 'bg-slate-100', text: 'text-slate-500' };
  return <span className={`inline-block px-2 py-0.5 rounded text-xs font-semibold ${s.bg} ${s.text}`}>{s.label}</span>;
}

function SeverityBadge({ severity }: { severity: string }) {
  const s = SEVERITY_STYLE[severity] ?? { label: severity, bg: 'bg-slate-100', text: 'text-slate-500' };
  return <span className={`inline-block px-2 py-0.5 rounded text-xs font-medium ${s.bg} ${s.text}`}>{s.label}</span>;
}

// ─── 리사이즈 가능한 체크 결과 테이블 ──────────────────────────────────────────

const COL_DEFS = [
  { key: 'status',         label: '상태',     defaultW: 72  },
  { key: 'severity',       label: '심각도',   defaultW: 72  },
  { key: 'question',       label: '체크 항목', defaultW: 260 },
  { key: 'finding',        label: '진단 결과', defaultW: 200 },
  { key: 'recommendation', label: '권고 사항', defaultW: 200 },
  { key: 'evidence',       label: '근거',     defaultW: 180 },
] as const;

function CheckResultTable({ rows, subscriptionName }: { rows: CheckResult[]; subscriptionName: string }) {
  const [widths, setWidths] = useState<number[]>(COL_DEFS.map(c => c.defaultW));
  const [expandedRow, setExpandedRow] = useState<number | null>(null);
  const tableRef = useRef<HTMLTableElement>(null);

  const startResize = (idx: number) => (e: React.MouseEvent) => {
    e.preventDefault();
    const startX = e.clientX;
    const startW = widths[idx];
    const cols = tableRef.current?.querySelectorAll<HTMLElement>('col');

    const onMove = (ev: MouseEvent) => {
      const newW = Math.max(50, startW + ev.clientX - startX);
      if (cols?.[idx]) cols[idx].style.width = `${newW}px`;
    };
    const onUp = (ev: MouseEvent) => {
      const newW = Math.max(50, startW + ev.clientX - startX);
      setWidths(prev => { const next = [...prev]; next[idx] = newW; return next; });
      window.removeEventListener('mousemove', onMove);
      window.removeEventListener('mouseup', onUp);
    };
    window.addEventListener('mousemove', onMove);
    window.addEventListener('mouseup', onUp);
  };

  return (
    <div className="overflow-x-auto rounded-lg border border-slate-200">
      <table
        ref={tableRef}
        style={{ tableLayout: 'fixed', width: '100%', borderCollapse: 'collapse' }}
      >
        <colgroup>
          {widths.map((w, i) => <col key={i} style={{ width: w }} />)}
        </colgroup>
        <thead>
          <tr className="bg-slate-50 border-b border-slate-200">
            {COL_DEFS.map((col, i) => (
              <th
                key={col.key}
                className="relative text-left text-xs font-semibold text-slate-500 uppercase tracking-wide px-3 py-2.5 select-none whitespace-nowrap"
              >
                {col.label}
                {i < COL_DEFS.length - 1 && (
                  <span
                    className="absolute right-0 top-0 h-full w-1 cursor-col-resize group"
                    onMouseDown={startResize(i)}
                  >
                    <span className="block w-px h-full bg-slate-200 group-hover:bg-blue-400 mx-auto transition-colors" />
                  </span>
                )}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-100">
          {rows.map((item, i) => {
            const evidence = [
              item.evidence_property && `속성: ${item.evidence_property}`,
              item.evidence_actual   && `실제값: ${item.evidence_actual}`,
              item.evidence_expected && `기대값: ${item.evidence_expected}`,
              subscriptionName       && `구독: ${subscriptionName}`,
            ].filter(Boolean) as string[];
            const isExpanded = expandedRow === i;
            return (
              <>
                <tr
                  key={i}
                  className={`hover:bg-slate-50 transition-colors cursor-pointer ${isExpanded ? 'bg-slate-50' : ''}`}
                  onClick={() => setExpandedRow(isExpanded ? null : i)}
                >
                  <td className="px-3 py-2"><StatusBadge status={item.status} /></td>
                  <td className="px-3 py-2"><SeverityBadge severity={item.severity} /></td>
                  <td className="px-3 py-2">
                    <span className="block text-xs text-slate-700 overflow-hidden" style={{ display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical' }}>
                      {item.question || '—'}
                    </span>
                  </td>
                  <td className="px-3 py-2">
                    <span className="block text-xs text-slate-600 overflow-hidden" style={{ display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical' }}>
                      {item.finding || '—'}
                    </span>
                  </td>
                  <td className="px-3 py-2">
                    <span className="block text-xs text-slate-600 overflow-hidden" style={{ display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical' }}>
                      {item.recommendation || '—'}
                    </span>
                  </td>
                  <td className="px-3 py-2">
                    {evidence.length > 0 ? (
                      <span className="text-xs text-blue-600">{isExpanded ? '▲ 접기' : '▼ 보기'}</span>
                    ) : (
                      <span className="text-xs text-slate-300">—</span>
                    )}
                  </td>
                </tr>
                {isExpanded && (
                  <tr key={`${i}-detail`} className="bg-slate-50">
                    <td colSpan={COL_DEFS.length} className="px-4 py-3">
                      <div className="space-y-2">
                        {item.finding && (
                          <div>
                            <span className="text-xs font-semibold text-slate-500">진단 결과: </span>
                            <span className="text-xs text-slate-700 whitespace-pre-wrap">{item.finding}</span>
                          </div>
                        )}
                        {item.recommendation && (
                          <div>
                            <span className="text-xs font-semibold text-slate-500">권고 사항: </span>
                            <span className="text-xs text-slate-700 whitespace-pre-wrap">{item.recommendation}</span>
                          </div>
                        )}
                        {evidence.length > 0 && (
                          <div className="bg-white rounded border border-slate-200 px-3 py-2 space-y-1">
                            {item.evidence_property && (
                              <div className="text-xs text-slate-600"><span className="font-medium">속성: </span>{item.evidence_property}</div>
                            )}
                            {item.evidence_actual && (
                              <div className="text-xs text-slate-600"><span className="font-medium">실제값: </span>{item.evidence_actual}</div>
                            )}
                            {item.evidence_expected && (
                              <div className="text-xs text-slate-600"><span className="font-medium">기대값: </span>{item.evidence_expected}</div>
                            )}
                            {subscriptionName && (
                              <div className="text-xs text-slate-600"><span className="font-medium">구독명: </span>{subscriptionName}</div>
                            )}
                          </div>
                        )}
                      </div>
                    </td>
                  </tr>
                )}
              </>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

export function ResourceDetailPopup({
  resourceName,
  reportId,
  onClose,
}: {
  resourceName: string;
  reportId: number;
  onClose: () => void;
}) {
  const [data, setData] = useState<ResourceDetailData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    const params = new URLSearchParams({ report_id: String(reportId), resource_name: resourceName });
    fetch(`/api/assessments/resource-check-results?${params}`)
      .then(r => r.ok ? r.json() : Promise.reject(r.statusText))
      .then((d: ResourceDetailData) => { if (!cancelled) { setData(d); setLoading(false); } })
      .catch((e: unknown) => { if (!cancelled) { setError(String(e)); setLoading(false); } });
    return () => { cancelled = true; };
  }, [reportId, resourceName]);

  const typeName = data?.resource_type?.split('/').pop() ?? data?.resource_type ?? '';

  return (
    <PopupShell
      onClose={onClose}
      maxWidth="max-w-6xl"
      header={
        <div className="flex items-start justify-between gap-4">
          <div className="min-w-0 flex-1">
            <p className="text-xs text-slate-500 font-medium mb-0.5">{typeName}</p>
            <h2 className="text-base font-bold text-slate-800 truncate" title={resourceName}>{resourceName}</h2>
            {data && (
              <div className="flex flex-wrap gap-x-3 mt-0.5">
                {data.resource_group && <span className="text-xs text-slate-400">RG: {data.resource_group}</span>}
                {data.subscription_name && <span className="text-xs text-slate-400">구독: {data.subscription_name}</span>}
                {data.assessment_time && <span className="text-xs text-slate-400">{data.assessment_time.slice(0, 10)}</span>}
              </div>
            )}
          </div>
          {data && (
            <div
              className="shrink-0 w-14 h-14 rounded-full flex items-center justify-center font-bold text-lg text-white"
              style={{ backgroundColor: scoreColor(data.overall_score) }}
            >
              {Math.round(data.overall_score)}
            </div>
          )}
        </div>
      }
    >
      {loading ? (
        <div className="py-16 text-center text-sm text-slate-400">불러오는 중…</div>
      ) : error ? (
        <div className="py-16 text-center text-sm text-rose-500">데이터를 불러오지 못했습니다.</div>
      ) : !data || !data.resource_name ? (
        <div className="py-16 text-center text-sm text-slate-400">데이터가 없습니다.</div>
      ) : (
        <div className="space-y-4">
          {/* 요약 */}
          <div className="grid grid-cols-4 gap-3">
            {[
              { label: '전체',   value: data.total_checks, color: 'text-slate-700' },
              { label: '통과',   value: data.passed,       color: 'text-emerald-600' },
              { label: '실패',   value: data.failed,       color: 'text-rose-600' },
              { label: '경고',   value: data.warnings,     color: 'text-amber-600' },
            ].map(({ label, value, color }) => (
              <div key={label} className="bg-slate-50 rounded-lg px-3 py-2.5 text-center border border-slate-100">
                <p className="text-xs text-slate-500 mb-1">{label}</p>
                <p className={`text-xl font-bold ${color}`}>{value}</p>
              </div>
            ))}
          </div>

          {/* 체크 결과 테이블 */}
          {data.check_results.length === 0 ? (
            <p className="text-sm text-slate-400 text-center py-8">체크 결과가 없습니다.</p>
          ) : (
            <CheckResultTable rows={data.check_results} subscriptionName={data.subscription_name} />
          )}
        </div>
      )}
    </PopupShell>
  );
}
