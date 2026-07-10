import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useAzureSession } from '../context/AzureSessionContext';
import { authFetch } from '../lib/authRest';
import { azureScopeHeaders } from '../lib/azureScopeHeaders';
import { isAbortError } from '../lib/safeDashboardFetch';
import { CHAT_REFRESH_ASSESSMENTS_EVENT } from '../lib/chatDataRefreshEvents';
import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip as RechartsTooltip,
  ResponsiveContainer, AreaChart, Area, Rectangle, LineChart, Line, Legend,
} from 'recharts';
import type { BarShapeProps } from 'recharts';
import { AlertCircle, ArrowRight } from 'lucide-react';
import { TrendDatePopup, SCORE_RANGE_COLORS, SCORE_RANGES, ResourceDetailPopup } from './DashboardPopups';
import type { TrendDatePopupData, ResourceRow } from './DashboardPopups';
import type { NavigateTo } from '../App';

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

function getScoreRangeColor(score: number): string {
  const idx = SCORE_RANGES.findIndex((r) => r.filter(score));
  return SCORE_RANGE_COLORS[idx] ?? '#64748b';
}

const ORDERED_BAR_COLORS = [
  '#2563eb',
  '#16a34a',
  '#f97316',
  '#7c3aed',
  '#dc2626',
  '#0891b2',
  '#ca8a04',
  '#db2777',
  '#4f46e5',
  '#059669',
  '#ea580c',
  '#9333ea',
] as const;

function orderedBarColor(index: number): string {
  return ORDERED_BAR_COLORS[index % ORDERED_BAR_COLORS.length];
}

type BarRow = { name: string; score: number; color?: string };
type SubscriptionTrendPoint = { date: string; score: number };
type SubscriptionTrendSeries = {
  id: string;
  name: string;
  color: string;
  data: SubscriptionTrendPoint[];
};
type SubscriptionTrendChartRow = { date: string } & Record<string, string | number | null>;
type ResourceGroupTrendSeries = {
  id: string;
  name: string;
  color: string;
  data: SubscriptionTrendPoint[];
};
type ResourceGroupTrendChartRow = { date: string } & Record<string, string | number | null>;

/** 막대별 클릭(포커스 링·선택 색상 변화 없음) */
function ClickableAvgBarRectangle(props: BarShapeProps, ctx: { onPick: (name: string) => void }) {
  const payload = props.payload as BarRow | undefined;
  if (!payload) return null;
  const x = props.x;
  const y = props.y;
  const w = props.width;
  const h = props.height;
  const fill = payload.color ?? orderedBarColor(0);
  return (
    <Rectangle
      x={x}
      y={y}
      width={w}
      height={h}
      fill={fill}
      radius={[0, 4, 4, 0]}
      stroke="none"
      strokeWidth={0}
      style={{ cursor: 'pointer', outline: 'none' }}
      tabIndex={-1}
      onMouseDown={(e) => e.preventDefault()}
      onClick={(e) => {
        e.stopPropagation();
        ctx.onPick(payload.name);
      }}
    />
  );
}

interface ChartsSummaryResponse {
  resource_group_bars: { name: string; score: number }[];
  resource_type_bars: { name: string; score: number }[];
  resource_group_trend: { date: string; score: number }[];
  resource_type_trend: { date: string; score: number }[];
  trend_resource_group_applied: string | null;
  trend_resource_type_applied: string | null;
  subscription_id?: string | null;
  db_configured?: boolean;
}

interface SubscriptionChartsSummaryResponse {
  subscription_bars: { id: string; name: string; score: number }[];
  subscription_trend: { date: string; score: number }[];
  trend_subscription_applied: string | null;
  db_configured?: boolean;
}

interface DashboardStatsPartial {
  worst_resources: ResourceRow[];
  db_configured?: boolean;
}

const emptySummary: ChartsSummaryResponse = {
  resource_group_bars: [],
  resource_type_bars: [],
  resource_group_trend: [],
  resource_type_trend: [],
  trend_resource_group_applied: null,
  trend_resource_type_applied: null,
  subscription_id: null,
  db_configured: true,
};

/** 대시보드 추이·81–100 구간과 동일한 녹색 */
const trendStroke = SCORE_RANGE_COLORS[4];

type MappedWorstResource = {
  id: number;
  name: string;
  type: string;
  rg: string;
  score: number;
  date: string;
  filename: string;
  reportId: number | null;
};

export function AssessmentChartsPanel({ onNavigate }: { onNavigate?: NavigateTo }) {
  const { tenantId, subscriptionId, azureBootstrapComplete } = useAzureSession();

  const [startDate, setStartDate] = useState<string>(defaultStartDate);
  const [endDate, setEndDate] = useState<string>(defaultEndDate);
  const [appliedStartDate, setAppliedStartDate] = useState<string>(defaultStartDate);
  const [appliedEndDate, setAppliedEndDate] = useState<string>(defaultEndDate);
  const [fetchKey, setFetchKey] = useState<number>(0);
  const [summary, setSummary] = useState<ChartsSummaryResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [rgTrendRefreshing, setRgTrendRefreshing] = useState(false);
  const [noDb, setNoDb] = useState(false);
  const chartsBootstrappedRef = useRef(false);
  const refreshScopeRef = useRef<'all' | 'rg'>('all');

  const [rgSubFilter, setRgSubFilter] = useState<string>('');
  const [rgOverride, setRgOverride] = useState<string | null>(null);
  const [rgOverrideLabel, setRgOverrideLabel] = useState<string | null>(null);
  const [selectedRgTrendIds, setSelectedRgTrendIds] = useState<string[]>([]);
  const [selectedRgTrends, setSelectedRgTrends] = useState<Record<string, ResourceGroupTrendSeries>>({});

  const [trendPopup, setTrendPopup] = useState<TrendDatePopupData | null>(null);

  const [worstResources, setWorstResources] = useState<MappedWorstResource[]>([]);
  const [loadingWorst, setLoadingWorst] = useState(true);
  const [resourceDetailTarget, setResourceDetailTarget] = useState<{ name: string; reportId: number } | null>(null);

  // 구독별 차트
  const [subSummary, setSubSummary] = useState<SubscriptionChartsSummaryResponse | null>(null);
  const [loadingSubCharts, setLoadingSubCharts] = useState(true);
  const [subTrendRefreshing, setSubTrendRefreshing] = useState(false);
  const [subOverride, setSubOverride] = useState<string | null>(null);       // API 파라미터용 ID
  const [subOverrideLabel, setSubOverrideLabel] = useState<string | null>(null); // 표시용 이름
  const [selectedSubTrendIds, setSelectedSubTrendIds] = useState<string[]>([]);
  const [selectedSubTrends, setSelectedSubTrends] = useState<Record<string, SubscriptionTrendSeries>>({});

  const resourceGroupBars = useMemo(
    () => (summary?.resource_group_bars ?? []).map((bar, index) => ({ ...bar, color: orderedBarColor(index) })),
    [summary?.resource_group_bars],
  );
  const subscriptionBars = useMemo(
    () => (subSummary?.subscription_bars ?? []).map((bar, index) => ({ ...bar, color: orderedBarColor(index) })),
    [subSummary?.subscription_bars],
  );

  const fetchSummary = useCallback(
    async (ac: AbortController) => {
      if (!azureBootstrapComplete) return;
      if (chartsBootstrappedRef.current) {
        const scope = refreshScopeRef.current;
        setRgTrendRefreshing(scope === 'all' || scope === 'rg');
      } else {
        setLoading(true);
      }
      setNoDb(false);
      try {
        const scope = azureScopeHeaders(tenantId, subscriptionId);
        const params = new URLSearchParams({ start_date: appliedStartDate, end_date: appliedEndDate });
        if (rgOverride != null && rgOverride.length > 0) {
          params.set('trend_resource_group', rgOverride);
        }
        if (rgSubFilter) {
          params.set('rg_subscription_id', rgSubFilter);
        }
        const res = await authFetch(`/api/assessments/charts-summary?${params}`, {
          headers: scope,
          signal: ac.signal,
        });
        if (!res.ok) {
          const t = await res.text();
          throw new Error(t || String(res.status));
        }
        const data = (await res.json()) as ChartsSummaryResponse;
        const next: ChartsSummaryResponse = {
          resource_group_bars: Array.isArray(data.resource_group_bars) ? data.resource_group_bars : [],
          resource_type_bars: Array.isArray(data.resource_type_bars) ? data.resource_type_bars : [],
          resource_group_trend: Array.isArray(data.resource_group_trend) ? data.resource_group_trend : [],
          resource_type_trend: Array.isArray(data.resource_type_trend) ? data.resource_type_trend : [],
          trend_resource_group_applied: data.trend_resource_group_applied ?? null,
          trend_resource_type_applied: data.trend_resource_type_applied ?? null,
          subscription_id: data.subscription_id ?? null,
          db_configured: data.db_configured !== false,
        };
        if (ac.signal.aborted) return;
        setSummary(next);
        chartsBootstrappedRef.current = true;
        const emptyBars = next.resource_group_bars.length === 0;
        setNoDb(
          !next.db_configured ||
            (emptyBars && next.resource_group_trend.length === 0),
        );
      } catch (e) {
        if (!isAbortError(e)) {
          console.warn('charts-summary', e);
          setSummary({ ...emptySummary, db_configured: true });
          setNoDb(true);
        }
      } finally {
        if (!ac.signal.aborted) {
          setLoading(false);
          setRgTrendRefreshing(false);
        }
      }
    },
    [azureBootstrapComplete, tenantId, subscriptionId, appliedStartDate, appliedEndDate, rgOverride, rgSubFilter, fetchKey],
  );

  const fetchResourceGroupTrend = useCallback(
    async (ac: AbortController, resourceGroup: string) => {
      if (!azureBootstrapComplete) return [];
      try {
        const scope = azureScopeHeaders(tenantId, subscriptionId);
        const params = new URLSearchParams({ start_date: appliedStartDate, end_date: appliedEndDate });
        params.set('trend_resource_group', resourceGroup);
        if (rgSubFilter) {
          params.set('rg_subscription_id', rgSubFilter);
        }
        const res = await authFetch(`/api/assessments/charts-summary?${params}`, {
          headers: scope,
          signal: ac.signal,
        });
        if (!res.ok) {
          const t = await res.text();
          throw new Error(t || String(res.status));
        }
        const data = (await res.json()) as ChartsSummaryResponse;
        if (ac.signal.aborted) return [];
        return Array.isArray(data.resource_group_trend) ? data.resource_group_trend : [];
      } catch (e) {
        if (!isAbortError(e)) console.warn('resource-group-trend', e);
        return [];
      }
    },
    [azureBootstrapComplete, tenantId, subscriptionId, appliedStartDate, appliedEndDate, rgSubFilter],
  );

  const fetchWorstResources = useCallback(
    async (ac: AbortController) => {
      if (!azureBootstrapComplete) return;
      setLoadingWorst(true);
      try {
        const params = new URLSearchParams({ start_date: appliedStartDate, end_date: appliedEndDate });
        const res = await authFetch(`/api/dashboard/stats?${params}`, {
          signal: ac.signal,
        });
        if (!res.ok) throw new Error(String(res.status));
        const data = (await res.json()) as DashboardStatsPartial;
        if (ac.signal.aborted) return;
        const mapped = (data.worst_resources ?? []).map((r, i) => ({
          id: i + 1,
          name: r.resource_name,
          type: r.resource_type.split('/').pop() ?? r.resource_type,
          rg: r.resource_group,
          score: Math.round(r.overall_score),
          date: r.assessment_time ? r.assessment_time.slice(0, 10) : '–',
          filename: r.assessment_filename ?? '',
          reportId: r.report_id ?? null,
        }));
        setWorstResources(mapped);
      } catch (e) {
        if (!isAbortError(e)) {
          console.warn('dashboard/stats worst-resources', e);
          setWorstResources([]);
        }
      } finally {
        if (!ac.signal.aborted) setLoadingWorst(false);
      }
    },
    [azureBootstrapComplete, appliedStartDate, appliedEndDate, fetchKey],
  );

  const fetchSubscriptionCharts = useCallback(
    async (ac: AbortController, subFilter: string | null = null) => {
      if (!azureBootstrapComplete) return;
      if (subFilter !== null) {
        setSubTrendRefreshing(true);
      } else {
        setLoadingSubCharts(true);
      }
      try {
        const scope = azureScopeHeaders(tenantId, subscriptionId);
        const params = new URLSearchParams({ start_date: appliedStartDate, end_date: appliedEndDate });
        if (subFilter) params.set('trend_subscription', subFilter);
        const res = await authFetch(`/api/assessments/subscription-charts-summary?${params}`, {
          headers: scope,
          signal: ac.signal,
        });
        if (!res.ok) throw new Error(String(res.status));
        const data = (await res.json()) as SubscriptionChartsSummaryResponse;
        if (ac.signal.aborted) return;
        setSubSummary({
          subscription_bars: Array.isArray(data.subscription_bars) ? data.subscription_bars : [],
          subscription_trend: Array.isArray(data.subscription_trend) ? data.subscription_trend : [],
          trend_subscription_applied: data.trend_subscription_applied ?? null,
          db_configured: data.db_configured !== false,
        });
      } catch (e) {
        if (!isAbortError(e)) {
          console.warn('subscription-charts-summary', e);
          setSubSummary({ subscription_bars: [], subscription_trend: [], trend_subscription_applied: null, db_configured: true });
        }
      } finally {
        if (!ac.signal.aborted) {
          setLoadingSubCharts(false);
          setSubTrendRefreshing(false);
        }
      }
    },
    [azureBootstrapComplete, tenantId, subscriptionId, appliedStartDate, appliedEndDate, fetchKey],
  );

  const fetchSubscriptionTrend = useCallback(
    async (ac: AbortController, subFilter: string) => {
      if (!azureBootstrapComplete) return [];
      try {
        const scope = azureScopeHeaders(tenantId, subscriptionId);
        const params = new URLSearchParams({ start_date: appliedStartDate, end_date: appliedEndDate });
        params.set('trend_subscription', subFilter);
        const res = await authFetch(`/api/assessments/subscription-charts-summary?${params}`, {
          headers: scope,
          signal: ac.signal,
        });
        if (!res.ok) throw new Error(String(res.status));
        const data = (await res.json()) as SubscriptionChartsSummaryResponse;
        if (ac.signal.aborted) return [];
        return Array.isArray(data.subscription_trend) ? data.subscription_trend : [];
      } catch (e) {
        if (!isAbortError(e)) console.warn('subscription-trend', e);
        return [];
      }
    },
    [azureBootstrapComplete, tenantId, subscriptionId, appliedStartDate, appliedEndDate],
  );

  useEffect(() => {
    if (!azureBootstrapComplete) return;
    const ac = new AbortController();
    refreshScopeRef.current = 'all';
    void fetchSummary(ac);
    return () => ac.abort();
  }, [azureBootstrapComplete, fetchSummary]);

  useEffect(() => {
    if (!azureBootstrapComplete) return;
    const ac = new AbortController();
    void fetchWorstResources(ac);
    return () => ac.abort();
  }, [azureBootstrapComplete, fetchWorstResources]);

  useEffect(() => {
    if (!azureBootstrapComplete) return;
    const ac = new AbortController();
    void fetchSubscriptionCharts(ac, null);
    return () => ac.abort();
  }, [azureBootstrapComplete, fetchSubscriptionCharts]);

  useEffect(() => {
    setRgOverride(null);
    setRgOverrideLabel(null);
    setSelectedRgTrendIds([]);
    setSelectedRgTrends({});
    setSubOverride(null);
    setSubOverrideLabel(null);
    setSelectedSubTrendIds([]);
    setSelectedSubTrends({});
    refreshScopeRef.current = 'all';
    chartsBootstrappedRef.current = false;
  }, [appliedStartDate, appliedEndDate, tenantId, subscriptionId, rgSubFilter, fetchKey]);

  useEffect(() => {
    if (!azureBootstrapComplete) return;
    const onRefresh = () => {
      const ac = new AbortController();
      refreshScopeRef.current = 'all';
      void fetchSummary(ac);
      const ac2 = new AbortController();
      void fetchWorstResources(ac2);
      const ac3 = new AbortController();
      setRgOverride(null);
      setRgOverrideLabel(null);
      setSelectedRgTrendIds([]);
      setSelectedRgTrends({});
      setSubOverride(null);
      setSubOverrideLabel(null);
      setSelectedSubTrendIds([]);
      setSelectedSubTrends({});
      void fetchSubscriptionCharts(ac3, null);
    };
    window.addEventListener(CHAT_REFRESH_ASSESSMENTS_EVENT, onRefresh);
    return () => window.removeEventListener(CHAT_REFRESH_ASSESSMENTS_EVENT, onRefresh);
  }, [azureBootstrapComplete, fetchSummary, fetchWorstResources, fetchSubscriptionCharts]);

  const rgBarSelection = (rgOverrideLabel ?? rgOverride ?? summary?.trend_resource_group_applied ?? '').trim();
  const selectedRgTrendSeries = useMemo(
    () => selectedRgTrendIds.map((id) => selectedRgTrends[id]).filter((series): series is ResourceGroupTrendSeries => Boolean(series)),
    [selectedRgTrendIds, selectedRgTrends],
  );
  const selectedRgTrendChartData = useMemo<ResourceGroupTrendChartRow[]>(() => {
    const rows = new Map<string, ResourceGroupTrendChartRow>();
    selectedRgTrendSeries.forEach((series, seriesIndex) => {
      const key = `rg_series_${seriesIndex}`;
      series.data.forEach((point) => {
        const row = rows.get(point.date) ?? { date: point.date };
        row[key] = point.score;
        rows.set(point.date, row);
      });
    });
    return Array.from(rows.values()).sort((a, b) => a.date.localeCompare(b.date));
  }, [selectedRgTrendSeries]);
  const hasSelectedRgTrends = selectedRgTrendSeries.length > 0;
  const hasSelectedRgTrendData = selectedRgTrendChartData.length > 0;
  // 표시용: subOverrideLabel(클릭 시 이름) 또는 API에서 반환한 이름
  const subBarSelection = (subOverrideLabel ?? subSummary?.trend_subscription_applied ?? '').trim();
  const selectedSubTrendSeries = useMemo(
    () => selectedSubTrendIds.map((id) => selectedSubTrends[id]).filter((series): series is SubscriptionTrendSeries => Boolean(series)),
    [selectedSubTrendIds, selectedSubTrends],
  );
  const selectedSubTrendChartData = useMemo<SubscriptionTrendChartRow[]>(() => {
    const rows = new Map<string, SubscriptionTrendChartRow>();
    selectedSubTrendSeries.forEach((series, seriesIndex) => {
      const key = `series_${seriesIndex}`;
      series.data.forEach((point) => {
        const row = rows.get(point.date) ?? { date: point.date };
        row[key] = point.score;
        rows.set(point.date, row);
      });
    });
    return Array.from(rows.values()).sort((a, b) => a.date.localeCompare(b.date));
  }, [selectedSubTrendSeries]);
  const hasSelectedSubTrends = selectedSubTrendSeries.length > 0;
  const hasSelectedSubTrendData = selectedSubTrendChartData.length > 0;

  const tooltipStyle = { borderRadius: '8px', border: 'none', boxShadow: '0 4px 6px -1px rgb(0 0 0 / 0.1)' };

  const handleRgTrendPointClick = async (date: string, score: number, resourceGroup?: string) => {
    setTrendPopup({ date, score, resources: [], loading: true });
    try {
      const params = new URLSearchParams({ date });
      if (resourceGroup) params.set('resource_group', resourceGroup);
      const res = await authFetch(`/api/dashboard/trend-detail?${params.toString()}`);
      const data: ResourceRow[] = res.ok ? await res.json() : [];
      setTrendPopup({ date, score, resources: data, loading: false });
    } catch {
      setTrendPopup((prev) => (prev ? { ...prev, loading: false } : null));
    }
  };

  const handleSubTrendPointClick = async (date: string, score: number, subId: string) => {
    setTrendPopup({ date, score, resources: [], loading: true });
    try {
      const params = new URLSearchParams({ date, subscription_id: subId });
      const res = await authFetch(`/api/dashboard/trend-detail?${params.toString()}`);
      const data: ResourceRow[] = res.ok ? await res.json() : [];
      setTrendPopup({ date, score, resources: data, loading: false });
    } catch {
      setTrendPopup((prev) => (prev ? { ...prev, loading: false } : null));
    }
  };

  return (
    <div className="p-6 font-sans bg-slate-50 min-h-full flex flex-col gap-6">
      {trendPopup ? <TrendDatePopup data={trendPopup} onClose={() => setTrendPopup(null)} /> : null}
      {resourceDetailTarget ? (
        <ResourceDetailPopup
          resourceName={resourceDetailTarget.name}
          reportId={resourceDetailTarget.reportId}
          onClose={() => setResourceDetailTarget(null)}
        />
      ) : null}

      <div className="flex flex-wrap items-end gap-x-4 gap-y-3">
        <div className="flex flex-col gap-1 shrink-0">
          <span className="text-xs font-medium text-slate-500 pl-0.5">시작일</span>
          <input
            type="date"
            className="bg-white border border-slate-200 rounded-lg py-2 px-3 text-sm text-slate-700 font-medium outline-none hover:border-blue-400 focus:border-blue-500 focus:ring-2 focus:ring-blue-100 transition-colors cursor-pointer"
            value={startDate}
            max={endDate}
            onChange={(e) => { if (e.target.value) setStartDate(e.target.value); }}
          />
        </div>
        <span className="text-slate-400 text-sm self-end pb-2">~</span>
        <div className="flex flex-col gap-1 shrink-0">
          <span className="text-xs font-medium text-slate-500 pl-0.5">종료일</span>
          <input
            type="date"
            className="bg-white border border-slate-200 rounded-lg py-2 px-3 text-sm text-slate-700 font-medium outline-none hover:border-blue-400 focus:border-blue-500 focus:ring-2 focus:ring-blue-100 transition-colors cursor-pointer"
            value={endDate}
            min={startDate}
            onChange={(e) => { if (e.target.value) setEndDate(e.target.value); }}
          />
        </div>
        <button
          className="self-end py-2 px-4 bg-blue-600 hover:bg-blue-700 active:bg-blue-800 text-white text-sm font-medium rounded-lg transition-colors"
          onClick={() => {
            setAppliedStartDate(startDate);
            setAppliedEndDate(endDate);
            setFetchKey(k => k + 1);
          }}
        >
          조회
        </button>
      </div>

      <div className="border-t border-slate-200" role="separator" aria-hidden="true" />

      {noDb && !loading ? (
        <div className="bg-amber-50 border border-amber-200 text-amber-900 text-sm rounded-xl px-4 py-3">
          {summary?.db_configured === false ? (
            <>
              PostgreSQL 진단 결과 저장소(<code className="text-xs bg-amber-100 px-1 rounded">DB_HOST</code> 등)가
              설정되지 않았습니다. 환경 변수로 DB를 연결하면 요약 차트가 표시됩니다.
            </>
          ) : (
            <>
              표시할 진단 집계 데이터가 없습니다. 선택한 기간에 평가 결과가 있는지 확인하거나, 진단 평가를 실행한 뒤
              다시 열어 주세요.
            </>
          )}
        </div>
      ) : null}

      {/* 구독별 차트 */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 min-h-[300px]">
        <div className="bg-white p-5 rounded-xl border border-slate-200 shadow-sm flex flex-col relative">
          <div className="mb-3">
            <h3 className="text-base font-semibold text-slate-800">구독 전체 평균 점수</h3>
          </div>
          {loadingSubCharts ? (
            <div className="flex-1 flex items-center justify-center text-sm text-slate-400">로딩 중…</div>
          ) : (subSummary?.subscription_bars.length ?? 0) === 0 ? (
            <div className="flex-1 flex items-center justify-center text-sm text-slate-400">데이터가 없습니다.</div>
          ) : (
            <div className="flex-1 min-h-[220px] outline-none [&_svg]:outline-none">
              <ResponsiveContainer width="100%" height="100%">
                <BarChart
                  data={subscriptionBars}
                  layout="vertical"
                  barCategoryGap="35%"
                  margin={{ top: 0, right: 16, left: 8, bottom: 0 }}
                >
                  <CartesianGrid strokeDasharray="3 3" horizontal={false} stroke="#e2e8f0" />
                  <XAxis
                    type="number"
                    domain={[0, 100]}
                    axisLine={false}
                    tickLine={false}
                    tick={{ fontSize: 12, fill: '#64748b' }}
                  />
                  <YAxis
                    dataKey="name"
                    type="category"
                    width={150}
                    axisLine={false}
                    tickLine={false}
                    tick={{ fontSize: 11, fill: '#475569', fontWeight: 500 }}
                    tickFormatter={(v: string) => v.length > 18 ? `${v.slice(0, 17)}…` : v}
                  />
                  <RechartsTooltip cursor={{ fill: '#f8fafc' }} contentStyle={tooltipStyle} />
                  <Bar
                    dataKey="score"
                    barSize={22}
                    maxBarSize={22}
                    cursor="pointer"
                    shape={(props) =>
                      ClickableAvgBarRectangle(props, {
                        onPick: async (name) => {
                          // name = 표시 이름, id는 bars에서 찾아서 API에 전달
                          const bar = subscriptionBars.find(b => b.name === name);
                          const subId = bar?.id ?? name;
                          const color = bar?.color ?? orderedBarColor(0);
                          if (selectedSubTrends[subId]) {
                            setSubOverride(null);
                            setSubOverrideLabel(null);
                            setSelectedSubTrendIds((prev) => prev.filter((id) => id !== subId));
                            setSelectedSubTrends((prev) => {
                              const { [subId]: _removed, ...rest } = prev;
                              return rest;
                            });
                            return;
                          }
                          setSubOverride(subId);
                          setSubOverrideLabel(name);
                          const ac = new AbortController();
                          const trend = await fetchSubscriptionTrend(ac, subId);
                          if (!ac.signal.aborted) {
                            setSelectedSubTrendIds((prev) => (prev.includes(subId) ? prev : [...prev, subId]));
                            setSelectedSubTrends((prev) => ({
                              ...prev,
                              [subId]: { id: subId, name, color, data: trend },
                            }));
                          }
                        },
                      })
                    }
                  />
                </BarChart>
              </ResponsiveContainer>
            </div>
          )}
        </div>

        <div className="bg-white p-5 rounded-xl border border-slate-200 shadow-sm flex flex-col relative">
          <div className="mb-3">
            <h3 className="text-base font-semibold text-slate-800">구독 일별 평균 점수 추이</h3>
          </div>
          {!loadingSubCharts && !hasSelectedSubTrends ? (
            <div className="flex-1 flex items-center justify-center text-sm text-slate-400">구독 막대를 클릭해 추이선을 추가하세요.</div>
          ) : !loadingSubCharts && !subTrendRefreshing && !hasSelectedSubTrendData ? (
            <div className="flex-1 flex items-center justify-center text-sm text-slate-400">선택한 구독의 기준 데이터가 부족합니다.</div>
          ) : (
            <div className="flex-1 min-h-[220px] relative">
              {subTrendRefreshing ? (
                <div className="absolute inset-0 z-10 flex items-center justify-center bg-white/60 text-sm text-slate-600 rounded-lg pointer-events-none">
                  추이 갱신 중…
                </div>
              ) : null}
              <ResponsiveContainer width="100%" height="100%">
                <LineChart data={selectedSubTrendChartData} margin={{ top: 10, right: 18, left: -20, bottom: 0 }}>
                  <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#e2e8f0" />
                  <XAxis
                    dataKey="date"
                    axisLine={false}
                    tickLine={false}
                    tick={{ fontSize: 12, fill: '#64748b' }}
                    minTickGap={20}
                  />
                  <YAxis domain={[0, 100]} axisLine={false} tickLine={false} tick={{ fontSize: 12, fill: '#64748b' }} />
                  <RechartsTooltip
                    contentStyle={{ borderRadius: '8px', border: 'none', boxShadow: '0 4px 6px -1px rgb(0 0 0 / 0.1)' }}
                  />
                  <Legend verticalAlign="top" height={28} wrapperStyle={{ fontSize: 12 }} />
                  {selectedSubTrendSeries.map((series, seriesIndex) => {
                    return (
                      <Line
                        key={series.id}
                        type="monotone"
                        dataKey={`series_${seriesIndex}`}
                        name={series.name}
                        stroke={series.color}
                        strokeWidth={3}
                        dot={{ r: 4, fill: series.color }}
                        activeDot={(dotProps: { cx?: number; cy?: number; payload?: SubscriptionTrendChartRow }) => {
                          const { cx, cy, payload } = dotProps;
                          const score = payload?.[`series_${seriesIndex}`];
                          return (
                            <circle
                              key={`sub-dot-${series.id}-${payload?.date}`}
                              cx={cx}
                              cy={cy}
                              r={6}
                              fill={series.color}
                              stroke="#fff"
                              strokeWidth={2}
                              style={{ cursor: 'pointer' }}
                              onClick={() => {
                                if (payload?.date != null && typeof score === 'number') {
                                  void handleSubTrendPointClick(payload.date, score, series.id);
                                }
                              }}
                            />
                          );
                        }}
                        connectNulls
                      />
                    );
                  })}
                </LineChart>
              </ResponsiveContainer>
            </div>
          )}
        </div>
      </div>

      {/* 리소스 그룹별 차트 */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 min-h-[300px]">
        <div className="bg-white p-5 rounded-xl border border-slate-200 shadow-sm flex flex-col relative">
          <div className="mb-3 flex items-center justify-between gap-3">
            <h3 className="text-base font-semibold text-slate-800 shrink-0">리소스 그룹별 평균 점수</h3>
            {(subSummary?.subscription_bars.length ?? 0) > 0 && (
              <select
                className="text-xs border border-slate-200 rounded-lg px-2 py-1.5 bg-white text-slate-600 outline-none hover:border-blue-400 focus:border-blue-500 focus:ring-1 focus:ring-blue-100 transition-colors cursor-pointer max-w-[180px]"
                value={rgSubFilter}
                onChange={(e) => {
                  setRgSubFilter(e.target.value);
                  setRgOverride(null);
                  setRgOverrideLabel(null);
                  setSelectedRgTrendIds([]);
                  setSelectedRgTrends({});
                }}
              >
                <option value="">전체 구독</option>
                {(subSummary?.subscription_bars ?? []).map((sub) => (
                  <option key={sub.id} value={sub.id}>{sub.name}</option>
                ))}
              </select>
            )}
          </div>
          {loading ? (
            <div className="flex-1 flex items-center justify-center text-sm text-slate-400">로딩 중…</div>
          ) : (summary?.resource_group_bars.length ?? 0) === 0 ? (
            <div className="flex-1 flex items-center justify-center text-sm text-slate-400">데이터가 없습니다.</div>
          ) : (
            <div className="flex-1 min-h-[220px] outline-none [&_svg]:outline-none">
              <ResponsiveContainer width="100%" height="100%">
                <BarChart
                  data={resourceGroupBars}
                  layout="vertical"
                  barCategoryGap="35%"
                  margin={{ top: 0, right: 16, left: 8, bottom: 0 }}
                >
                  <CartesianGrid strokeDasharray="3 3" horizontal={false} stroke="#e2e8f0" />
                  <XAxis
                    type="number"
                    domain={[0, 100]}
                    axisLine={false}
                    tickLine={false}
                    tick={{ fontSize: 12, fill: '#64748b' }}
                  />
                  <YAxis
                    dataKey="name"
                    type="category"
                    width={120}
                    axisLine={false}
                    tickLine={false}
                    tick={{ fontSize: 11, fill: '#475569', fontWeight: 500 }}
                  />
                  <RechartsTooltip cursor={{ fill: '#f8fafc' }} contentStyle={tooltipStyle} />
                  <Bar
                    dataKey="score"
                    barSize={22}
                    maxBarSize={22}
                    cursor="pointer"
                    shape={(props) =>
                      ClickableAvgBarRectangle(props, {
                        onPick: async (name) => {
                          const bar = resourceGroupBars.find(b => b.name === name);
                          const color = bar?.color ?? orderedBarColor(0);
                          if (selectedRgTrends[name]) {
                            setRgOverrideLabel(null);
                            setSelectedRgTrendIds((prev) => prev.filter((id) => id !== name));
                            setSelectedRgTrends((prev) => {
                              const { [name]: _removed, ...rest } = prev;
                              return rest;
                            });
                            return;
                          }
                          setRgOverrideLabel(name);
                          const ac = new AbortController();
                          const trend = await fetchResourceGroupTrend(ac, name);
                          if (!ac.signal.aborted) {
                            setSelectedRgTrendIds((prev) => (prev.includes(name) ? prev : [...prev, name]));
                            setSelectedRgTrends((prev) => ({
                              ...prev,
                              [name]: { id: name, name, color, data: trend },
                            }));
                          }
                        },
                      })
                    }
                  />
                </BarChart>
              </ResponsiveContainer>
            </div>
          )}
        </div>

        <div className="bg-white p-5 rounded-xl border border-slate-200 shadow-sm flex flex-col relative">
          <div className="mb-3">
            <h3 className="text-base font-semibold text-slate-800">리소스 그룹 일별 평균 점수 추이</h3>
          </div>
          {!loading && !hasSelectedRgTrends ? (
            <div className="flex-1 flex items-center justify-center text-sm text-slate-400">리소스 그룹 막대를 클릭해 추이선을 추가하세요.</div>
          ) : !loading && !rgTrendRefreshing && !hasSelectedRgTrendData ? (
            <div className="flex-1 flex items-center justify-center text-sm text-slate-400">선택한 리소스 그룹의 기준 데이터가 부족합니다.</div>
          ) : (
            <div className="flex-1 min-h-[220px] relative">
              {rgTrendRefreshing ? (
                <div className="absolute inset-0 z-10 flex items-center justify-center bg-white/60 text-sm text-slate-600 rounded-lg pointer-events-none">
                  추이 갱신 중…
                </div>
              ) : null}
              <ResponsiveContainer width="100%" height="100%">
                <LineChart data={selectedRgTrendChartData} margin={{ top: 10, right: 18, left: -20, bottom: 0 }}>
                  <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#e2e8f0" />
                  <XAxis
                    dataKey="date"
                    axisLine={false}
                    tickLine={false}
                    tick={{ fontSize: 12, fill: '#64748b' }}
                    minTickGap={20}
                  />
                  <YAxis domain={[0, 100]} axisLine={false} tickLine={false} tick={{ fontSize: 12, fill: '#64748b' }} />
                  <RechartsTooltip
                    contentStyle={{ borderRadius: '8px', border: 'none', boxShadow: '0 4px 6px -1px rgb(0 0 0 / 0.1)' }}
                  />
                  <Legend verticalAlign="top" height={28} wrapperStyle={{ fontSize: 12 }} />
                  {selectedRgTrendSeries.map((series, seriesIndex) => {
                    return (
                      <Line
                        key={series.id}
                        type="monotone"
                        dataKey={`rg_series_${seriesIndex}`}
                        name={series.name}
                        stroke={series.color}
                        strokeWidth={3}
                        dot={{ r: 4, fill: series.color, cursor: 'pointer' }}
                        activeDot={(dotProps: { cx?: number; cy?: number; payload?: ResourceGroupTrendChartRow }) => {
                          const { cx, cy, payload } = dotProps;
                          const score = payload?.[`rg_series_${seriesIndex}`];
                          return (
                            <circle
                              key={`rg-dot-${series.id}-${payload?.date}`}
                              cx={cx}
                              cy={cy}
                              r={6}
                              fill={series.color}
                              stroke="#fff"
                              strokeWidth={2}
                              style={{ cursor: 'pointer' }}
                              onClick={() => {
                                if (payload?.date != null && typeof score === 'number') {
                                  void handleRgTrendPointClick(payload.date, score, series.name);
                                }
                              }}
                            />
                          );
                        }}
                        connectNulls
                      />
                    );
                  })}
                </LineChart>
              </ResponsiveContainer>
            </div>
          )}
        </div>
      </div>

      {/* 취약 리소스 관리 */}
      <div className="space-y-3 pb-8">
        <div className="px-1">
          <h3 className="text-base font-bold text-slate-800">취약 리소스 관리 (Bottom 10 Resources)</h3>
          <p className="text-sm text-slate-500">인프라 개선이 시급한 점수 하위 리소스 목록입니다.</p>
        </div>
        <div className="bg-white rounded-xl border border-slate-200 shadow-sm overflow-hidden">
          <div className="overflow-x-auto">
            <table className="w-full text-left border-collapse">
              <thead>
                <tr className="bg-slate-50 border-b border-slate-200 text-xs text-slate-500 font-semibold uppercase tracking-wider">
                  <th className="py-4 px-6">순위</th>
                  <th className="py-4 px-6">리소스명</th>
                  <th className="py-4 px-6">유형</th>
                  <th className="py-4 px-6">소속 RG</th>
                  <th className="py-4 px-6 w-64">점수</th>
                  <th className="py-4 px-6">최근 진단일</th>
                  <th className="py-4 px-6 text-right">상세보기</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {loadingWorst ? (
                  <tr><td colSpan={7} className="py-12 px-6 text-center text-slate-400">로딩 중…</td></tr>
                ) : worstResources.length === 0 ? (
                  <tr>
                    <td colSpan={7} className="py-12 px-6 text-center text-slate-500">
                      가장 취약한 리소스 정보를 표시하려면 리소스 평가를 실행하세요.
                    </td>
                  </tr>
                ) : worstResources.map((row) => (
                  <tr key={row.id} className="hover:bg-slate-50 transition-colors">
                    <td className="py-4 px-6 font-medium text-slate-500">{row.id}</td>
                    <td className="py-4 px-6 font-bold text-slate-800 flex items-center gap-2">
                      {row.score < 60 && <AlertCircle size={16} className="text-rose-500" />}
                      {row.name}
                    </td>
                    <td className="py-4 px-6 text-sm text-slate-600">
                      <span className="bg-slate-100 px-2 py-1 rounded text-xs">{row.type}</span>
                    </td>
                    <td className="py-4 px-6 text-sm text-slate-600">
                      <div className="text-xs text-slate-400">{row.rg}</div>
                    </td>
                    <td className="py-4 px-6">
                      <div className="flex items-center gap-3">
                        <div className="flex-1 h-2.5 bg-slate-100 rounded-full overflow-hidden">
                          <div
                            className="h-full rounded-full"
                            style={{ width: `${row.score}%`, backgroundColor: getScoreRangeColor(row.score) }}
                          />
                        </div>
                        <span className="text-sm font-bold" style={{ color: getScoreRangeColor(row.score) }}>
                          {row.score}
                        </span>
                      </div>
                    </td>
                    <td className="py-4 px-6 text-sm text-slate-500">{row.date}</td>
                    <td className="py-4 px-6 text-right">
                      <button
                        className="p-2 text-slate-400 hover:text-azure-blue hover:bg-blue-50 rounded-lg transition-colors border border-transparent hover:border-blue-100 disabled:opacity-30 disabled:cursor-not-allowed"
                        title={row.reportId ? '평가 결과 보기' : '연결된 리포트 없음'}
                        disabled={!row.reportId}
                        onClick={() => {
                          if (row.reportId) {
                            setResourceDetailTarget({ name: row.name, reportId: row.reportId });
                          }
                        }}
                      >
                        <ArrowRight size={18} />
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </div>
  );
}

