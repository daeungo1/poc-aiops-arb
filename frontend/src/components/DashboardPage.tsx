import { useCallback, useEffect, useMemo, useState } from 'react';
import type { NavigateTo } from '../App';
import { useAzureSession, type AzureSubscriptionItem } from '../context/AzureSessionContext';
import { authFetch } from '../lib/authRest';
import { isAbortError } from '../lib/safeDashboardFetch';
import {
  CHAT_REFRESH_ASSESSMENTS_EVENT,
} from '../lib/chatDataRefreshEvents';
import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip as RechartsTooltip, ResponsiveContainer,
  AreaChart, Area, PieChart, Pie, Cell, Rectangle,
} from 'recharts';
import type { BarShapeProps } from 'recharts';
import { ChevronDown, CheckCircle2, RefreshCw } from 'lucide-react';
import { SubscriptionSelect } from './SubscriptionSelect';
import {
  ScoreRangePopup, AvgScorePopup, TrendDatePopup,
  SCORE_RANGE_COLORS, SCORE_RANGES,
} from './DashboardPopups';
import type { ResourceRow, ScoreRangePopupData, TrendDatePopupData } from './DashboardPopups';

// ─── Types ─────────────────────────────────────────────────────────────────

interface DashboardStats {
  kpi: {
    total_reports: number;
    avg_score: number;
    total_resources: number;
    total_checks: number;
  };
  trend: { date: string; score: number }[];
  score_distribution: { range: string; count: number }[];
  worst_resources: ResourceRow[];
  auto_manual: { total_checks: number; automated: number; manual: number };
  pass_fail: {
    total_checks: number;
    passed: number;
    failed: number;
    warnings: number;
    type_mismatch_count?: number;
  };
  filters: { resource_groups: string[]; resource_types: string[] };
  avg_score_resources: ResourceRow[];
  /** 동일 요청 기간·DB 기준 평가에 등장하는 구독(distinct); 통계 패널용 필터 */
  subscriptions?: { subscription_id: string; name: string; tenant_id?: string; state?: string }[];
}

interface DashboardPageProps {
  onNavigate: NavigateTo;
}

const TIER_COLORS = {
  pass: '#10b981',
  warning: '#f59e0b',
  fail: '#ef4444',
};

function toDateString(d: Date): string {
  return d.toISOString().slice(0, 10);
}

function defaultStartDate(): string {
  const d = new Date();
  d.setDate(d.getDate() - 15);
  return toDateString(d);
}

function defaultEndDate(): string {
  return toDateString(new Date());
}

type ScoreDistBarPayload = { range: string; count: number };

/** 막대 클릭 시 SVG 포커스 링(검은 테두리) 방지 */
function ClickableScoreDistBarRectangle(
  props: BarShapeProps,
  ctx: { onPick: (payload: ScoreDistBarPayload) => void },
) {
  const payload = props.payload as ScoreDistBarPayload | undefined;
  if (!payload) return null;
  const { x, y, width, height, fill } = props;
  return (
    <Rectangle
      x={x}
      y={y}
      width={width}
      height={height}
      fill={fill}
      radius={[0, 4, 4, 0]}
      stroke="none"
      strokeWidth={0}
      style={{ cursor: 'pointer', outline: 'none' }}
      tabIndex={-1}
      onMouseDown={(e) => e.preventDefault()}
      onClick={(e) => {
        e.stopPropagation();
        ctx.onPick(payload);
      }}
    />
  );
}

// ─── Component ─────────────────────────────────────────────────────────────

export function DashboardPage({ onNavigate }: DashboardPageProps) {
  const { subscriptionId, setSelection } = useAzureSession();

  const [stats, setStats]                 = useState<DashboardStats | null>(null);
  const [loading, setLoading]             = useState(true);

  const [filterRG,     setFilterRG]     = useState<string>('All RGs');
  const [filterType,   setFilterType]   = useState<string>('All Types');
  const [startDate, setStartDate]       = useState<string>(defaultStartDate);
  const [endDate, setEndDate]           = useState<string>(defaultEndDate);
  const [appliedStartDate, setAppliedStartDate] = useState<string>(defaultStartDate);
  const [appliedEndDate, setAppliedEndDate]     = useState<string>(defaultEndDate);
  const [fetchKey, setFetchKey]         = useState<number>(0);

  const subscriptionsForSelect = useMemo((): AzureSubscriptionItem[] => {
    const raw = stats?.subscriptions;
    if (!Array.isArray(raw)) return [];
    return raw
      .filter((s) => typeof (s as { subscription_id?: string }).subscription_id === 'string')
      .filter((s) => (s as { subscription_id: string }).subscription_id.trim())
      .map((s) => {
        const row = s as { subscription_id: string; name?: string; state?: string; tenant_id?: string };
        const id = row.subscription_id.trim();
        return {
          subscription_id: id,
          name: typeof row.name === 'string' && row.name.trim() ? row.name.trim() : id,
          state: typeof row.state === 'string' ? row.state : '',
          tenant_id: typeof row.tenant_id === 'string' ? row.tenant_id : '',
        };
      });
  }, [stats?.subscriptions]);

  const selectedDashboardSubscription = useMemo(
    () => subscriptionsForSelect.find((s) => s.subscription_id === subscriptionId) ?? null,
    [subscriptionsForSelect, subscriptionId],
  );

  const dashboardScopeHeaders = useCallback((): Record<string, string> => {
    return selectedDashboardSubscription
      ? { 'X-Azure-Subscription-Id': selectedDashboardSubscription.subscription_id }
      : {};
  }, [selectedDashboardSubscription]);

  // filterRG が変わったら filterType をリセット
  useEffect(() => { setFilterType('All Types'); }, [filterRG]);

  // ── /api/dashboard/stats fetch ─────────────────────────────────────────
  const fetchStats = (ac: AbortController) => {
    const scope  = dashboardScopeHeaders();
    const rg     = filterRG   === 'All RGs'   ? '' : filterRG;
    const rt     = filterType === 'All Types' ? '' : filterType;
    const params = new URLSearchParams({ start_date: appliedStartDate, end_date: appliedEndDate });
    if (rg) params.set('resource_group', rg);
    if (rt) params.set('resource_type', rt);

    setLoading(true);
    authFetch(`/api/dashboard/stats?${params.toString()}`, { signal: ac.signal, headers: scope })
      .then(r => r.ok ? r.json() : null)
      .then((data: DashboardStats | null) => {
        if (!data) return;
        setStats({
          ...data,
          pass_fail: data.pass_fail ?? {
            total_checks: 0,
            passed: 0,
            failed: 0,
            warnings: 0,
            type_mismatch_count: 0,
          },
          avg_score_resources: data.avg_score_resources ?? [],
          subscriptions: Array.isArray(data.subscriptions) ? data.subscriptions : [],
        });
      })
      .catch(e => { if (!isAbortError(e)) console.warn('dashboard/stats', e); })
      .finally(() => { if (!ac.signal.aborted) setLoading(false); });
  };

  const refreshStats = () => {
    const ac = new AbortController();
    fetchStats(ac);
  };

  const selectedSubId = selectedDashboardSubscription?.subscription_id ?? null;

  useEffect(() => {
    const ac = new AbortController();
    fetchStats(ac);
    return () => ac.abort();
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedSubId, appliedStartDate, appliedEndDate, filterRG, filterType, fetchKey]);

  // ── Chat refresh event ─────────────────────────────────────────────────
  useEffect(() => {
    const onRefresh = () => {
      const ac = new AbortController();
      fetchStats(ac);
    };
    window.addEventListener(CHAT_REFRESH_ASSESSMENTS_EVENT, onRefresh);
    return () => window.removeEventListener(CHAT_REFRESH_ASSESSMENTS_EVENT, onRefresh);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [subscriptionId, appliedStartDate, appliedEndDate, filterRG, filterType]);

  // ── Derived values ─────────────────────────────────────────────────────
  const resourceGroups = useMemo(() => {
    const rgs = [...new Set((stats?.filters.resource_groups ?? []).filter(Boolean))].sort();
    return ['All RGs', ...rgs];
  }, [stats]);

  const availableTypes = useMemo(() => {
    const types = [...new Set((stats?.filters.resource_types ?? []).filter(Boolean))].sort();
    return ['All Types', ...types];
  }, [stats]);

  const [scoreRangePopup, setScoreRangePopup] = useState<ScoreRangePopupData | null>(null);
  const [avgScorePopup, setAvgScorePopup] = useState(false);
  const [trendPopup, setTrendPopup] = useState<TrendDatePopupData | null>(null);

  const handleTrendPointClick = async (date: string, score: number) => {
    setTrendPopup({ date, score, resources: [], loading: true });
    try {
      const scope = dashboardScopeHeaders();
      const params = new URLSearchParams({ date });
      if (filterRG   !== 'All RGs')   params.set('resource_group', filterRG);
      if (filterType !== 'All Types') params.set('resource_type', filterType);
      const res = await authFetch(`/api/dashboard/trend-detail?${params.toString()}`, { headers: scope });
      const data: ResourceRow[] = res.ok ? await res.json() : [];
      setTrendPopup({ date, score, resources: data, loading: false });
    } catch {
      setTrendPopup(prev => prev ? { ...prev, loading: false } : null);
    }
  };

  const handleScoreDistBarClick = useCallback(
    async (row: ScoreDistBarPayload) => {
      const range = String(row?.range ?? '');
      const idx = SCORE_RANGES.findIndex(r => r.label === range);
      if (idx === -1) return;
      const rangeInfo = SCORE_RANGES[idx];
      const [minStr, maxStr] = rangeInfo.label.split('-');
      const score_min = Number(minStr);
      const score_max = Number(maxStr);
      setScoreRangePopup({ label: rangeInfo.label, colorIndex: idx, resources: [], loading: true });
      try {
        const scope = dashboardScopeHeaders();
        const rg = filterRG === 'All RGs' ? '' : filterRG;
        const rt = filterType === 'All Types' ? '' : filterType;
        const params = new URLSearchParams({
          score_min: String(score_min),
          score_max: String(score_max),
          start_date: appliedStartDate,
          end_date: appliedEndDate,
        });
        if (rg) params.set('resource_group', rg);
        if (rt) params.set('resource_type', rt);
        const res = await fetch(`/api/dashboard/score-range-resources?${params}`, { headers: scope });
        const data = res.ok ? await res.json() : [];
        setScoreRangePopup({ label: rangeInfo.label, colorIndex: idx, resources: data, loading: false });
      } catch {
        setScoreRangePopup({ label: rangeInfo.label, colorIndex: idx, resources: [], loading: false });
      }
    },
    [dashboardScopeHeaders, appliedStartDate, appliedEndDate, filterRG, filterType],
  );

  const passFailData = useMemo(() => {
    const pf = stats?.pass_fail;
    if (!pf) return null;
    // 타입 불일치는 분모에서 제외하고 별도 표시
    const denominator = pf.passed + pf.failed + pf.warnings;
    if (denominator === 0 && (pf.type_mismatch_count ?? 0) === 0) return null;
    return {
      passed:              pf.passed,
      failed:              pf.failed,
      warnings:            pf.warnings,
      typeMismatchCount:   pf.type_mismatch_count ?? 0,
      passRate:            denominator > 0 ? Math.round((pf.passed / denominator) * 100) : 0,
    };
  }, [stats]);

  const getScoreRangeColor = (score: number) => {
    const idx = SCORE_RANGES.findIndex(r => r.filter(score));
    return SCORE_RANGE_COLORS[idx] ?? '#64748b';
  };

  const hasData = !loading && (stats?.kpi.total_reports ?? 0) > 0;
  const isDashboardLoading = loading;

  return (
    <div className="min-h-screen bg-slate-50 p-6 space-y-6 font-sans">
      {isDashboardLoading && (
        <div className="dashboard-top-loading fixed left-0 right-0 top-0 z-50">
          <div className="dashboard-top-loading__bar" />
        </div>
      )}

      {/* Popups */}
      {scoreRangePopup && (
        <ScoreRangePopup data={scoreRangePopup} onClose={() => setScoreRangePopup(null)} />
      )}
      {avgScorePopup && (
        <AvgScorePopup
          avgScore={stats?.kpi.avg_score ?? 0}
          resources={stats?.avg_score_resources ?? []}
          onClose={() => setAvgScorePopup(false)}
        />
      )}
      {trendPopup && (
        <TrendDatePopup data={trendPopup} onClose={() => setTrendPopup(null)} />
      )}

      {/* SECTION 1: EXECUTIVE SUMMARY */}
      <section className="space-y-4">
        <h2 className="text-lg font-bold text-slate-800 px-1">요약 및 지표 (Executive Summary)</h2>

        {/* GLOBAL FILTER BAR */}
        <div className="bg-white px-6 py-4 rounded-xl shadow-sm border border-slate-200 flex flex-col gap-3">
          <div className="flex flex-wrap items-end gap-4">
            <SubscriptionSelect
              label="구독 ID"
              subscriptions={subscriptionsForSelect}
              tenantId={null}
              subscriptionId={subscriptionId}
              onChange={(sub) => {
                setSelection('', sub.subscription_id, sub.name);
                setFilterRG('All RGs');
                setFilterType('All Types');
                setFetchKey((k) => k + 1);
              }}
            />
            <FilterSelect label="리소스 그룹 (RG)"  options={resourceGroups}  value={filterRG}     onChange={setFilterRG} />
            <FilterSelect label="리소스 유형"        options={availableTypes}  value={filterType}   onChange={setFilterType} />
            <DateInput label="시작일" value={startDate} max={endDate} onChange={setStartDate} />
            <span className="text-slate-400 text-sm self-end pb-2">~</span>
            <DateInput label="종료일" value={endDate} min={startDate} onChange={setEndDate} />
            <button
              type="button"
              onClick={() => {
                setAppliedStartDate(startDate);
                setAppliedEndDate(endDate);
                setFetchKey(k => k + 1);
              }}
              className="flex items-center gap-1.5 h-[38px] px-4 rounded-lg border border-blue-600 bg-azure-blue text-xs font-semibold text-white hover:bg-azure-dark active:scale-95 transition-all whitespace-nowrap shadow-sm"
              title="선택한 날짜 범위로 조회"
            >
              조회
            </button>
            <button
              type="button"
              onClick={() => {
                const start = defaultStartDate();
                const end = defaultEndDate();
                setFilterRG('All RGs');
                setFilterType('All Types');
                setStartDate(start);
                setEndDate(end);
                setAppliedStartDate(start);
                setAppliedEndDate(end);
                setFetchKey(k => k + 1);
              }}
              className="flex items-center gap-1.5 h-[38px] px-3 rounded-lg border border-slate-300 bg-white text-xs font-semibold text-slate-600 hover:bg-slate-50 hover:border-slate-400 active:scale-95 transition-all whitespace-nowrap shadow-sm"
              title="필터를 기본값으로 초기화"
            >
              <RefreshCw size={13} />
              <span>초기화</span>
            </button>
          </div>

          {/* Active filter chips */}
          {(() => {
            const active: { key: string; label: string; value: string }[] = [];
            if (selectedDashboardSubscription) {
              const subLabel = selectedDashboardSubscription.name?.trim() || '';
              active.push({
                key: 'sub',
                label: '구독',
                value: subLabel || selectedDashboardSubscription.subscription_id,
              });
            }
            if (filterRG   !== 'All RGs')   active.push({ key: 'rg',     label: 'RG',   value: filterRG });
            if (filterType !== 'All Types') active.push({ key: 'type',   label: '유형', value: filterType });
            active.push({ key: 'period', label: '조회 기간', value: `${appliedStartDate} ~ ${appliedEndDate}` });
            return active.length === 0 ? null : (
              <div className="flex w-full flex-wrap items-center gap-2 border-t border-slate-100 pt-3">
                <span className="text-xs text-slate-400 font-medium whitespace-nowrap">적용 중</span>
                {active.map(f => (
                  <span key={f.key} className="inline-flex items-center gap-1 bg-blue-50 text-blue-700 text-xs font-medium px-2.5 py-1 rounded-full border border-blue-200 whitespace-nowrap">
                    <span className="text-blue-400 font-normal">{f.label}</span>
                    <span>{f.value}</span>
                  </span>
                ))}
              </div>
            );
          })()}
        </div>

        <div className="grid grid-cols-1 gap-6">

          {/* KPI Cards (Left) */}
          <div className="h-full">
            <div className="bg-white p-5 rounded-xl border border-slate-200 shadow-sm h-full flex flex-col md:flex-row gap-5">
              <div
                className="flex-1 min-w-0 rounded-lg -m-2 p-2 hover:bg-slate-50 transition-colors cursor-pointer flex items-center justify-between gap-6"
                onClick={() => onNavigate('assessments', { initialAssessmentTab: 'results' })}
              >
                <div className="min-w-0">
                  <div className="flex items-start gap-3">
                    <div className="min-w-0">
                      <h3 className="text-sm font-semibold text-slate-500">전체 평가 건수</h3>
                      <div className="mt-2 text-3xl font-extrabold text-slate-800 tracking-tight leading-none">
                        {loading ? '…' : String(stats?.kpi.total_reports ?? 0)}
                      </div>
                      <div className="text-xs text-slate-500 mt-2">생성된 진단 리포트 수</div>
                    </div>
                    <div className="p-2 bg-slate-50 rounded-lg shrink-0">
                      <CheckCircle2 className="text-emerald-500" size={24} />
                    </div>
                  </div>
                </div>

                <div
                  className="flex items-center gap-4 shrink-0"
                  onClick={e => e.stopPropagation()}
                >
                  <div className="min-w-0 text-right">
                    <p className="text-xs text-slate-400 font-medium">평균 점수</p>
                    <p className="text-sm font-semibold text-slate-700 mt-1">조회 기간·필터 기준</p>
                  </div>
                  <button
                    type="button"
                    className="relative w-28 h-28 shrink-0 rounded-full focus:outline-none focus:ring-2 focus:ring-blue-100 [&_svg]:outline-none"
                    onClick={() => setAvgScorePopup(true)}
                    title="평균 점수 근거 자료 보기"
                  >
                    <ResponsiveContainer width="100%" height="100%">
                      <PieChart>
                        <Pie
                          data={
                            loading
                              ? [{ value: 100, fill: '#e2e8f0' }]
                              : [
                                  { value: Math.max(0, Math.min(100, stats?.kpi.avg_score ?? 0)), fill: getScoreRangeColor(stats?.kpi.avg_score ?? 0) },
                                  { value: Math.max(0, 100 - Math.min(100, stats?.kpi.avg_score ?? 0)), fill: '#e2e8f0' },
                                ]
                          }
                          innerRadius={30}
                          outerRadius={46}
                          dataKey="value"
                          startAngle={90}
                          endAngle={-270}
                          stroke="none"
                        >
                          {(loading
                            ? [{ fill: '#e2e8f0' }]
                            : [
                                { fill: getScoreRangeColor(stats?.kpi.avg_score ?? 0) },
                                { fill: '#e2e8f0' },
                              ]
                          ).map((d, i) => <Cell key={`avg-${i}`} fill={d.fill} />)}
                        </Pie>
                      </PieChart>
                    </ResponsiveContainer>
                    <span className="absolute inset-0 flex flex-col items-center justify-center">
                      <span className="text-xl font-extrabold text-slate-800 leading-none">
                        {loading ? '…' : Math.round(stats?.kpi.avg_score ?? 0)}
                      </span>
                      <span className="text-[10px] font-semibold text-slate-400 mt-0.5">점</span>
                    </span>
                  </button>
                </div>
              </div>

              <div className="border-t md:border-t-0 md:border-l border-slate-100" role="separator" aria-hidden="true" />

              <div className="flex-1 flex items-center justify-between gap-6 min-w-0 py-1">
                <div className="flex flex-col justify-center min-w-0">
                  <p className="text-sm text-slate-500 font-medium">성공/실패 비율</p>
                  {loading ? (
                    <span className="mt-2 text-3xl font-extrabold text-slate-800 leading-none">…</span>
                  ) : !passFailData ? (
                    <span className="mt-2 text-2xl font-extrabold text-slate-400 leading-none">–</span>
                  ) : (
                    <>
                      <div className="mt-2 flex items-end gap-1">
                        <span className="text-3xl font-extrabold text-slate-800 leading-none">{passFailData.passRate}%</span>
                        <span className="text-sm text-emerald-600 font-medium leading-none pb-0.5">Pass</span>
                      </div>
                      <div className="mt-3 flex flex-col gap-1.5 text-xs text-slate-500">
                        <div className="flex items-center gap-1.5 leading-none">
                          <span className="w-2.5 h-2.5 rounded-full bg-emerald-500 shrink-0" />
                          <span>성공 {passFailData.passed.toLocaleString()}</span>
                        </div>
                        <div className="flex items-center gap-1.5 leading-none">
                          <span className="w-2.5 h-2.5 rounded-full bg-rose-500 shrink-0" />
                          <span>실패 {passFailData.failed.toLocaleString()}</span>
                        </div>
                        <div className="flex items-center gap-1.5 leading-none">
                          <span className="w-2.5 h-2.5 rounded-full bg-amber-400 shrink-0" />
                          <span>경고 {passFailData.warnings.toLocaleString()}</span>
                        </div>
                      </div>
                    </>
                  )}
                </div>
                <div className="w-28 h-28 shrink-0 outline-none [&_svg]:outline-none">
                  <ResponsiveContainer width="100%" height="100%">
                    <PieChart>
                      <Pie
                        data={
                          loading || !passFailData
                            ? [{ value: 100, fill: '#e2e8f0' }]
                            : [
                                { name: '성공', value: passFailData.passed,   fill: TIER_COLORS.pass },
                                { name: '실패', value: passFailData.failed,   fill: TIER_COLORS.fail },
                                { name: '경고', value: passFailData.warnings, fill: TIER_COLORS.warning },
                              ]
                        }
                        innerRadius={30} outerRadius={46} dataKey="value" stroke="none"
                      >
                        {(loading || !passFailData
                          ? [{ fill: '#e2e8f0' }]
                          : [
                              { fill: TIER_COLORS.pass },
                              { fill: TIER_COLORS.fail },
                              { fill: TIER_COLORS.warning },
                            ]
                        ).map((d, i) => <Cell key={i} fill={d.fill} />)}
                      </Pie>
                      <RechartsTooltip
                        contentStyle={{ borderRadius: '8px', border: 'none', boxShadow: '0 4px 6px -1px rgb(0 0 0 / 0.1)', fontSize: '12px' }}
                      />
                    </PieChart>
                  </ResponsiveContainer>
                </div>
              </div>
            </div>
          </div>

          {/* Score Distribution (Center) */}
          <div className="bg-white p-5 rounded-xl border border-slate-200 shadow-sm flex flex-col relative">
            <div className="mb-4">
              <h3 className="text-base font-semibold text-slate-800">점수 분포 현황</h3>
            </div>
            {!loading && !hasData && (
              <div className="absolute inset-x-0 bottom-0 top-16 flex flex-col items-center justify-center bg-white/80 z-10 backdrop-blur-sm rounded-xl">
                <p className="text-sm font-medium text-slate-500">진단된 리소스 점수가 없습니다.</p>
                <p className="text-xs text-slate-400 mt-1">챗봇에서 리소스 평가를 실행해주세요.</p>
              </div>
            )}
            <div className="flex-1 min-h-[200px] outline-none [&_svg]:outline-none">
              <ResponsiveContainer width="100%" height="100%">
                <BarChart
                  data={[...(stats?.score_distribution ?? [])].reverse()}
                  layout="vertical"
                  margin={{ top: 10, right: 10, left: 12, bottom: 0 }}
                >
                  <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#e2e8f0" />
                  <XAxis type="number" axisLine={false} tickLine={false} tick={{ fontSize: 12, fill: '#64748b' }} allowDecimals={false} />
                  <YAxis dataKey="range" type="category" axisLine={false} tickLine={false} tick={{ fontSize: 12, fill: '#64748b' }} width={56} />
                  <RechartsTooltip
                    cursor={{ fill: 'rgba(0,0,0,0.04)' }}
                    contentStyle={{ borderRadius: '8px', border: 'none', boxShadow: '0 4px 6px -1px rgb(0 0 0 / 0.1)' }}
                    formatter={(value) => [Number(value ?? 0), '리소스 수']}
                  />
                  <Bar
                    dataKey="count"
                    radius={[0, 4, 4, 0]}
                    shape={(props) =>
                      ClickableScoreDistBarRectangle(props, { onPick: handleScoreDistBarClick })
                    }
                  >
                    {[...(stats?.score_distribution ?? [])].reverse().map((_entry, index) => {
                      const totalLen = (stats?.score_distribution ?? []).length;
                      return <Cell key={`cell-${index}`} fill={SCORE_RANGE_COLORS[totalLen - 1 - index]} />;
                    })}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            </div>
          </div>

          {/* Trend (Right) */}
          <div className="bg-white p-5 rounded-xl border border-slate-200 shadow-sm flex flex-col relative">
            <div className="mb-4">
              <h3 className="text-base font-semibold text-slate-800">전체 평균 점수 추이</h3>
            </div>
            {!loading && (stats?.trend.length ?? 0) === 0 && (
              <div className="absolute inset-x-0 bottom-0 top-16 flex flex-col items-center justify-center bg-white/80 z-10 backdrop-blur-sm rounded-xl">
                <p className="text-sm font-medium text-slate-500">기준 데이터가 부족합니다.</p>
              </div>
            )}
            <div className="flex-1 min-h-[200px] outline-none [&_svg]:outline-none">
              <ResponsiveContainer width="100%" height="100%">
                <AreaChart
                  data={stats?.trend ?? []}
                  margin={{ top: 10, right: 10, left: -20, bottom: 0 }}
                >
                  <defs>
                    <linearGradient id="colorScore" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="5%"  stopColor={TIER_COLORS.pass} stopOpacity={0.3} />
                      <stop offset="95%" stopColor={TIER_COLORS.pass} stopOpacity={0} />
                    </linearGradient>
                  </defs>
                  <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#e2e8f0" />
                  <XAxis dataKey="date" axisLine={false} tickLine={false} tick={{ fontSize: 12, fill: '#64748b' }} minTickGap={20} />
                  <YAxis domain={[0, 100]} axisLine={false} tickLine={false} tick={{ fontSize: 12, fill: '#64748b' }} />
                  <RechartsTooltip contentStyle={{ borderRadius: '8px', border: 'none', boxShadow: '0 4px 6px -1px rgb(0 0 0 / 0.1)' }} />
                  <Area
                    type="monotone"
                    dataKey="score"
                    stroke={TIER_COLORS.pass}
                    strokeWidth={3}
                    fillOpacity={1}
                    fill="url(#colorScore)"
                    dot={(dotProps: { cx?: number; cy?: number }) => {
                      const { cx, cy } = dotProps;
                      if (cx == null || cy == null) return null;
                      return (
                        <circle
                          cx={cx}
                          cy={cy}
                          r={5}
                          fill={TIER_COLORS.pass}
                          style={{ cursor: 'pointer', outline: 'none' }}
                          tabIndex={-1}
                          onMouseDown={(e) => e.preventDefault()}
                        />
                      );
                    }}
                    activeDot={(dotProps: any) => {
                      const { cx, cy, payload } = dotProps;
                      return (
                        <circle
                          key={`dot-${payload?.date}`}
                          cx={cx}
                          cy={cy}
                          r={7}
                          fill={TIER_COLORS.pass}
                          stroke="#fff"
                          strokeWidth={2}
                          style={{ cursor: 'pointer', outline: 'none' }}
                          tabIndex={-1}
                          onMouseDown={(e) => e.preventDefault()}
                          onClick={() => {
                            if (payload?.date) handleTrendPointClick(payload.date, payload.score);
                          }}
                        />
                      );
                    }}
                  />
                </AreaChart>
              </ResponsiveContainer>
            </div>
          </div>
        </div>
      </section>

    </div>
  );
}

// ─── Sub components ─────────────────────────────────────────────────────────

function FilterSelect({
  label, options, value, onChange, loading = false,
}: { label: string; options: string[]; value?: string; onChange?: (val: string) => void; loading?: boolean }) {
  return (
    <div className="flex flex-col gap-1 shrink-0">
      <span className="text-xs font-medium text-slate-500 pl-0.5">{label}</span>
      {loading ? (
        <div className="flex flex-col bg-white border border-blue-200 rounded-lg min-w-[150px] overflow-hidden">
          <div className="flex items-center gap-2 px-3 py-2">
            <div className="w-3.5 h-3.5 rounded-full border-2 border-blue-200 border-t-blue-500 animate-spin shrink-0" />
            <span className="text-sm text-blue-400 font-medium">Loading...</span>
          </div>
          <div className="dashboard-indeterminate-track rounded-none h-0.5">
            <div className="dashboard-indeterminate-thumb" />
          </div>
        </div>
      ) : (
        <div className="flex bg-white border border-slate-200 rounded-lg overflow-hidden hover:border-blue-400 transition-colors focus-within:border-blue-500 focus-within:ring-2 focus-within:ring-blue-100">
          <select
            className="bg-transparent py-2 pl-3 pr-8 text-sm outline-none text-slate-700 font-medium appearance-none min-w-[150px] cursor-pointer"
            value={value ?? ''}
            onChange={(e) => onChange?.(e.target.value)}
          >
            <option value="" disabled className="text-slate-400">{label}</option>
            {options.map(opt => <option key={opt} value={opt}>{opt}</option>)}
          </select>
          <div className="flex items-center pr-2 pointer-events-none -ml-6">
            <ChevronDown size={14} className="text-slate-400" />
          </div>
        </div>
      )}
    </div>
  );
}

function DateInput({
  label, value, min, max, onChange,
}: { label: string; value: string; min?: string; max?: string; onChange: (val: string) => void }) {
  return (
    <div className="flex flex-col gap-1 shrink-0">
      <span className="text-xs font-medium text-slate-500 pl-0.5">{label}</span>
      <input
        type="date"
        className="bg-white border border-slate-200 rounded-lg py-2 px-3 text-sm text-slate-700 font-medium outline-none hover:border-blue-400 focus:border-blue-500 focus:ring-2 focus:ring-blue-100 transition-colors cursor-pointer"
        value={value}
        min={min}
        max={max}
        onChange={(e) => {
          if (e.target.value) onChange(e.target.value);
        }}
      />
    </div>
  );
}

