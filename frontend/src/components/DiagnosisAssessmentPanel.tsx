import { Fragment, useCallback, useEffect, useMemo, useState } from 'react';
import { useAzureSession, type AzureSubscriptionItem } from '../context/AzureSessionContext';
import { SubscriptionSelect } from './SubscriptionSelect';
import { useAssessmentRun } from '../context/AssessmentRunContext';
import { azureScopeHeaders } from '../lib/azureScopeHeaders';
import { CHAT_REFRESH_ASSESSMENTS_EVENT } from '../lib/chatDataRefreshEvents';
import { authFetch } from '../lib/authRest';
import { CLOUD_SYNC_BUTTON_CLASS } from '../lib/cloudSyncButton';
import { isAbortError } from '../lib/safeDashboardFetch';

export interface AzureResourceRow {
  id: string;
  name: string;
  type: string;
  resource_group: string;
  subscription_id: string;
  location: string;
}

function groupByRg(resources: AzureResourceRow[]): Map<string, AzureResourceRow[]> {
  const m = new Map<string, AzureResourceRow[]>();
  for (const r of resources) {
    const rg = r.resource_group || '(no group)';
    if (!m.has(rg)) m.set(rg, []);
    m.get(rg)!.push(r);
  }
  for (const list of m.values()) {
    list.sort((a, b) => a.name.localeCompare(b.name));
  }
  return m;
}

function isGroupFullySelected(list: AzureResourceRow[], selectedIds: Set<string>): boolean {
  if (list.length === 0) return false;
  return list.every((r) => selectedIds.has(r.id));
}

/**
 * 선택된 리소스 유형 집합에 대해 체크리스트가 적용 가능한지 판단.
 * - selectedTypes가 비어 있으면(리소스 미선택) 항상 true
 * - applicable_resource_types가 비어 있으면 범용 체크리스트로 항상 true
 * - 그 외: applicable_resource_types 중 하나라도 selectedTypes에 포함(대소문자 무시)되면 true
 */
function isChecklistApplicable(
  cl: ChecklistSummaryRow,
  selectedTypes: ReadonlySet<string>,
): boolean {
  if (selectedTypes.size === 0) return true;
  if (!cl.applicable_resource_types || cl.applicable_resource_types.length === 0) return true;
  return cl.applicable_resource_types.some((art) => {
    const artLower = art.toLowerCase();
    for (const st of selectedTypes) {
      if (st.includes(artLower) || artLower.includes(st)) return true;
    }
    return false;
  });
}

/** 선택된 리소스 그룹·리소스가 평가에 포함되는 고유 리소스 수 */
function countSelectedAssessmentResources(
  byRg: Map<string, AzureResourceRow[]>,
  selectedRgExplicit: Set<string>,
  selectedResourceIds: Set<string>,
): number {
  const ids = new Set(selectedResourceIds);
  for (const rg of selectedRgExplicit) {
    for (const r of byRg.get(rg) ?? []) {
      ids.add(r.id);
    }
  }
  return ids.size;
}

export interface DiagnosisAssessmentPanelProps {
  onAssessmentFinished?: () => void;
}

interface ChecklistSummaryRow {
  id: string;
  name: string;
  version?: string;
  total_checks?: number;
  applicable_resource_types?: string[];
}

export function DiagnosisAssessmentPanel({ onAssessmentFinished }: DiagnosisAssessmentPanelProps) {
  const { tenantId, subscriptionId, subscriptionName, azureBootstrapComplete, setSelection } = useAzureSession();
  const { isRunning, timeHint, resultMessage, startRun, finishRun, clearResult } = useAssessmentRun();
  const [resources, setResources] = useState<AzureResourceRow[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [loadingList, setLoadingList] = useState(false);

  const [selectedRgExplicit, setSelectedRgExplicit] = useState<Set<string>>(() => new Set());
  const [selectedResourceIds, setSelectedResourceIds] = useState<Set<string>>(() => new Set());
  const [filterText, setFilterText] = useState('');
  const [expandedRg, setExpandedRg] = useState<Set<string>>(() => new Set());

  const [checklistOptions, setChecklistOptions] = useState<ChecklistSummaryRow[]>([]);
  const [checklistLoading, setChecklistLoading] = useState(false);
  const [checklistLoadError, setChecklistLoadError] = useState<string | null>(null);
  const [selectedChecklistIds, setSelectedChecklistIds] = useState<Set<string>>(() => new Set());
  const [subscriptions, setSubscriptions] = useState<AzureSubscriptionItem[]>([]);

  useEffect(() => {
    if (!azureBootstrapComplete) return;
    const ac = new AbortController();
    authFetch('/api/azure/subscriptions', { signal: ac.signal })
      .then(async (r) => {
        if (!r.ok) {
          const j = await r.json().catch(() => ({}));
          throw new Error((j as { detail?: string }).detail || r.statusText);
        }
        return (await r.json()) as { subscriptions?: AzureSubscriptionItem[] };
      })
      .then((data) => {
        if (!ac.signal.aborted) setSubscriptions(Array.isArray(data.subscriptions) ? data.subscriptions : []);
      })
      .catch((e: unknown) => {
        if (!isAbortError(e)) console.warn('diagnosis/subscriptions', e);
        if (!ac.signal.aborted) setSubscriptions([]);
      });
    return () => ac.abort();
  }, [azureBootstrapComplete]);

  /** API 목록에 없어도 세션 구독이 드롭다운에서 선택된 상태로 보이게 */
  const subscriptionsForSelect = useMemo(() => {
    if (!subscriptionId || !tenantId) return subscriptions;
    const exists = subscriptions.some(
      (s) => s.subscription_id === subscriptionId && s.tenant_id === tenantId,
    );
    if (exists) return subscriptions;
    const fallback: AzureSubscriptionItem = {
      tenant_id: tenantId,
      subscription_id: subscriptionId,
      name: subscriptionName?.trim() || '현재 구독',
      state: '',
    };
    return [fallback, ...subscriptions];
  }, [subscriptions, subscriptionId, tenantId, subscriptionName]);

  const byRg = useMemo(() => groupByRg(resources), [resources]);
  const sortedRgNames = useMemo(() => [...byRg.keys()].sort((a, b) => a.localeCompare(b)), [byRg]);

  /** 현재 선택된 리소스들의 ARM 타입 집합 (소문자). 리소스 미선택 시 빈 Set. */
  const selectedTypes = useMemo<Set<string>>(() => {
    if (selectedRgExplicit.size === 0 && selectedResourceIds.size === 0) return new Set();
    const types = new Set<string>();
    for (const rg of selectedRgExplicit) {
      for (const r of byRg.get(rg) ?? []) {
        types.add(r.type.toLowerCase());
      }
    }
    for (const r of resources) {
      if (selectedResourceIds.has(r.id)) {
        types.add(r.type.toLowerCase());
      }
    }
    return types;
  }, [selectedRgExplicit, selectedResourceIds, byRg, resources]);

  const loadResources = useCallback(async () => {
    if (!azureBootstrapComplete) return;
    setLoadingList(true);
    setLoadError(null);
    try {
      const scope = azureScopeHeaders(tenantId, subscriptionId);
      const res = await authFetch('/api/azure/resources', { headers: scope });
      if (!res.ok) {
        const t = await res.text();
        throw new Error(t || String(res.status));
      }
      const data = (await res.json()) as AzureResourceRow[];
      setResources(Array.isArray(data) ? data : []);
      setSelectedRgExplicit(new Set());
      setSelectedResourceIds(new Set());
      setExpandedRg(new Set());
    } catch (e) {
      if (!isAbortError(e)) {
        setLoadError(e instanceof Error ? e.message : String(e));
      }
    } finally {
      setLoadingList(false);
    }
  }, [tenantId, subscriptionId, azureBootstrapComplete]);

  useEffect(() => {
    void loadResources();
  }, [loadResources]);

  const loadChecklists = useCallback(async () => {
    setChecklistLoading(true);
    setChecklistLoadError(null);
    try {
      const res = await authFetch('/api/checklists');
      if (!res.ok) {
        const t = await res.text();
        throw new Error(t || String(res.status));
      }
      const data = (await res.json()) as { checklists?: ChecklistSummaryRow[] };
      setChecklistOptions(Array.isArray(data.checklists) ? data.checklists : []);
    } catch (e) {
      setChecklistLoadError(e instanceof Error ? e.message : String(e));
      setChecklistOptions([]);
    } finally {
      setChecklistLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadChecklists();
  }, [loadChecklists]);

  /** 리소스 선택이 바뀌면 비적용 체크리스트를 자동 해제 */
  useEffect(() => {
    if (selectedTypes.size === 0) return;
    setSelectedChecklistIds((prev) => {
      const next = new Set(prev);
      let changed = false;
      for (const id of prev) {
        const cl = checklistOptions.find((c) => c.id === id);
        if (cl && !isChecklistApplicable(cl, selectedTypes)) {
          next.delete(id);
          changed = true;
        }
      }
      return changed ? next : prev;
    });
  }, [selectedTypes, checklistOptions]);

  const rgVisuallyChecked = useCallback(
    (rg: string) => {
      const list = byRg.get(rg) ?? [];
      return selectedRgExplicit.has(rg) || isGroupFullySelected(list, selectedResourceIds);
    },
    [byRg, selectedRgExplicit, selectedResourceIds],
  );

  const toggleRg = (rg: string, wantChecked: boolean) => {
    const list = byRg.get(rg) ?? [];
    setSelectedRgExplicit((prev) => {
      const n = new Set(prev);
      if (wantChecked) n.add(rg);
      else n.delete(rg);
      return n;
    });
    setSelectedResourceIds((prev) => {
      const n = new Set(prev);
      for (const r of list) {
        if (wantChecked) n.add(r.id);
        else n.delete(r.id);
      }
      return n;
    });
  };

  const onRgCheckboxChange = (rg: string, checked: boolean) => {
    const visual = rgVisuallyChecked(rg);
    if (checked && !visual) {
      toggleRg(rg, true);
      return;
    }
    if (!checked && visual) {
      toggleRg(rg, false);
    }
  };

  const onResourceCheckboxChange = (r: AzureResourceRow, checked: boolean) => {
    const nextIds = new Set(selectedResourceIds);
    if (checked) nextIds.add(r.id);
    else nextIds.delete(r.id);
    setSelectedResourceIds(nextIds);
    const list = byRg.get(r.resource_group) ?? [];
    if (!checked) {
      setSelectedRgExplicit((prev) => {
        const n = new Set(prev);
        n.delete(r.resource_group);
        return n;
      });
    } else if (isGroupFullySelected(list, nextIds)) {
      setSelectedRgExplicit((prev) => new Set(prev).add(r.resource_group));
    }
  };

  /** API 호출 — 성공 시 summary 반환, 실패 시 throw */
  const executeAssessment = async (body: {
    resource_group_names: string[];
    resource_ids: string[];
    checklist_ids: string[];
  }): Promise<string> => {
    const scope = azureScopeHeaders(tenantId, subscriptionId, subscriptionName);
    const res = await authFetch('/api/assessments/run', {
      method: 'POST',
      headers: { ...scope, 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    const data = (await res.json()) as { success?: boolean; detail?: string; summary?: string };
    if (!res.ok) throw new Error(data.detail || `요청 실패 (${res.status})`);
    if (!data.success) throw new Error(data.detail || '평가에 실패했습니다.');
    return data.summary || '평가가 완료되었습니다.';
  };

  /** 현재 구독의 지원 리소스 전체 평가 */
  const runFullSubscriptionAssessment = async () => {
    if (!azureBootstrapComplete || isRunning) return;
    if (selectedChecklistIds.size === 0) {
      alert('체크리스트를 하나 이상 선택해 주세요.');
      return;
    }
    clearResult();
    startRun(null);
    try {
      const summary = await executeAssessment({
        resource_group_names: [],
        resource_ids: [],
        checklist_ids: [...selectedChecklistIds],
      });
      finishRun(summary);
      window.dispatchEvent(new CustomEvent(CHAT_REFRESH_ASSESSMENTS_EVENT));
    } catch (e) {
      finishRun(e instanceof Error ? e.message : String(e));
    } finally {
      onAssessmentFinished?.();
    }
  };

  /** 선택한 리소스 그룹·리소스만 평가 */
  const runAssessment = async () => {
    if (!azureBootstrapComplete || isRunning) return;
    if (selectedChecklistIds.size === 0) {
      alert('체크리스트를 하나 이상 선택해 주세요.');
      return;
    }
    if (selectedRgExplicit.size === 0 && selectedResourceIds.size === 0) {
      alert('리소스 그룹 또는 리소스를 하나 이상 선택해 주세요.');
      return;
    }
    clearResult();
    const resourceCount = countSelectedAssessmentResources(byRg, selectedRgExplicit, selectedResourceIds);
    startRun(`${Math.max(1, resourceCount)}분 정도 소요됩니다.`);
    try {
      const summary = await executeAssessment({
        resource_group_names: [...selectedRgExplicit],
        resource_ids: [...selectedResourceIds],
        checklist_ids: [...selectedChecklistIds],
      });
      finishRun(summary);
      window.dispatchEvent(new CustomEvent(CHAT_REFRESH_ASSESSMENTS_EVENT));
    } catch (e) {
      finishRun(e instanceof Error ? e.message : String(e));
    } finally {
      onAssessmentFinished?.();
    }
  };

  const filterLower = filterText.trim().toLowerCase();
  const filteredRgNames = useMemo(() => {
    if (!filterLower) return sortedRgNames;
    return sortedRgNames.filter((rg) => {
      if (rg.toLowerCase().includes(filterLower)) return true;
      const list = byRg.get(rg) ?? [];
      return list.some(
        (r) =>
          r.name.toLowerCase().includes(filterLower) ||
          r.type.toLowerCase().includes(filterLower) ||
          r.id.toLowerCase().includes(filterLower),
      );
    });
  }, [sortedRgNames, byRg, filterLower]);

  if (!azureBootstrapComplete) {
    return (
      <div className="p-6 text-sm text-gray-500">
        구독을 선택하면 진단 평가를 사용할 수 있습니다.
      </div>
    );
  }

  return (
    <div className="p-6 h-full flex flex-col min-h-0">
      <div className="flex justify-between items-center gap-4 mb-4 shrink-0">
        <h1 className="text-2xl font-bold text-gray-900">진단평가</h1>
        <div className="flex items-center gap-3 shrink-0">
          {timeHint ? (
            <span className="text-sm font-semibold text-red-600 whitespace-nowrap">
              {timeHint}
            </span>
          ) : null}
          <button
            type="button"
            onClick={() => void runFullSubscriptionAssessment()}
            disabled={isRunning || loadingList}
            className="px-4 py-2.5 rounded-lg text-sm font-semibold border-2 border-azure-blue text-azure-blue bg-white hover:bg-azure-light/30 disabled:opacity-50 shadow-sm"
          >
            {isRunning ? '실행 중…' : '전체 평가'}
          </button>
          <button
            type="button"
            onClick={() => void runAssessment()}
            disabled={isRunning || loadingList}
            className="px-5 py-2.5 rounded-lg text-sm font-semibold text-white bg-azure-blue hover:bg-azure-dark disabled:opacity-50 shadow-sm"
          >
            {isRunning ? '실행 중…' : '평가'}
          </button>
        </div>
      </div>

      {loadError && (
        <div className="mb-4 p-3 rounded-lg bg-red-50 text-red-800 text-sm border border-red-100">
          리소스 목록을 불러오지 못했습니다: {loadError}
          <button
            type="button"
            className="ml-2 underline"
            onClick={() => void loadResources()}
          >
            다시 시도
          </button>
        </div>
      )}



      <div className="flex flex-col flex-1 min-h-0 gap-4 overflow-hidden">
        <div className="shrink-0 flex flex-wrap gap-3 items-end">
          <SubscriptionSelect
            label="구독 ID"
            subscriptions={subscriptionsForSelect}
            tenantId={tenantId}
            subscriptionId={subscriptionId}
            onChange={(sub) => {
              setSelection(sub.tenant_id, sub.subscription_id, sub.name);
            }}
          />
          <input
            type="search"
            placeholder="리소스 그룹·이름·타입·ID 검색…"
            value={filterText}
            onChange={(e) => setFilterText(e.target.value)}
            className="flex-1 min-w-[200px] border border-gray-200 rounded-lg px-3 py-2 text-sm"
          />
          <button
            type="button"
            onClick={() => void loadResources()}
            disabled={loadingList}
            className={CLOUD_SYNC_BUTTON_CLASS}
          >
            <span>☁️</span> 클라우드 동기화
          </button>
        </div>

        <div className="flex-1 min-h-0 flex flex-col gap-4 min-h-0 overflow-hidden">
          <div className="flex-1 min-h-0 flex flex-col overflow-hidden min-h-0">
          {loadingList ? (
            <div className="flex-1 flex items-center justify-center min-h-0 bg-white rounded-xl border border-gray-200 shadow-sm p-4">
              <div className="animate-spin rounded-full h-10 w-10 border-b-2 border-azure-blue" />
            </div>
          ) : (
            <div className="flex-1 min-h-0 bg-white rounded-xl border border-gray-200 shadow-sm flex flex-col overflow-hidden p-4">
              <div className="flex items-center justify-between gap-2 mb-2 shrink-0">
                <h2 className="text-sm font-semibold text-gray-800">리소스 그룹 / 리소스</h2>
              </div>
              <div className="flex-1 overflow-auto min-h-0">
                <table className="w-full text-sm border-collapse table-fixed">
                  <colgroup>
                    <col className="w-[2.25rem]" />
                    <col className="w-[1.75rem]" />
                    <col />
                  </colgroup>
                  <thead className="sr-only">
                    <tr>
                      <th scope="col">펼치기</th>
                      <th scope="col">선택</th>
                      <th scope="col">리소스 그룹 / 리소스</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-gray-100">
                    {filteredRgNames.map((rg) => {
                      const list = byRg.get(rg) ?? [];
                      const open = expandedRg.has(rg) || !!filterLower;
                      return (
                        <Fragment key={rg}>
                          <tr className="bg-gray-50/70 hover:bg-gray-100/80">
                            <td className="py-2 pl-1 pr-0 align-middle w-px">
                              <button
                                type="button"
                                aria-expanded={open}
                                aria-label={open ? `${rg} 접기` : `${rg} 펼치기`}
                                onClick={() =>
                                  setExpandedRg((prev) => {
                                    const n = new Set(prev);
                                    if (n.has(rg)) n.delete(rg);
                                    else n.add(rg);
                                    return n;
                                  })
                                }
                                className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md text-gray-500 hover:bg-white hover:text-gray-800 border border-transparent hover:border-gray-200"
                              >
                                <span className="text-xs leading-none">{open ? '▼' : '▶'}</span>
                              </button>
                            </td>
                            <td className="py-2 pl-0 pr-0 align-middle w-px">
                              <input
                                type="checkbox"
                                className="h-4 w-4 shrink-0 rounded border-gray-300"
                                checked={rgVisuallyChecked(rg)}
                                onChange={(e) => onRgCheckboxChange(rg, e.target.checked)}
                                aria-label={`${rg} 전체 선택`}
                              />
                            </td>
                            <td className="py-2 pl-3 pr-3 align-middle">
                              <span className="font-semibold text-gray-800">{rg}</span>
                              <span className="text-gray-400 text-xs ml-1.5">({list.length})</span>
                            </td>
                          </tr>
                          {open &&
                            list.map((r) => (
                              <tr key={r.id} className="hover:bg-gray-50/60 bg-white">
                                <td className="py-2 pl-1 pr-0 align-top w-px" aria-hidden />
                                <td className="py-2 pl-0 pr-0 align-top w-px">
                                  <div className="flex items-center pt-0.5">
                                    <input
                                      type="checkbox"
                                      className="h-4 w-4 shrink-0 rounded border-gray-300"
                                      checked={selectedResourceIds.has(r.id)}
                                      onChange={(e) => onResourceCheckboxChange(r, e.target.checked)}
                                      aria-label={`${r.name} 선택`}
                                    />
                                  </div>
                                </td>
                                <td className="py-2 pr-3 align-top">
                                  <div className="border-l-2 border-gray-200 pl-3 ml-1">
                                    <div className="font-medium text-gray-800 truncate">{r.name}</div>
                                    <div className="text-[11px] text-gray-500 break-all">{r.type}</div>
                                    <div className="text-[10px] text-gray-400 break-all mt-0.5">{r.id}</div>
                                  </div>
                                </td>
                              </tr>
                            ))}
                        </Fragment>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </div>
          )}
          </div>

        <div className="flex-1 min-h-0 flex flex-col border border-gray-200 rounded-xl bg-white p-4 shadow-sm overflow-hidden min-h-0">
          <div className="flex items-center justify-between gap-2 mb-2 shrink-0">
            <h2 className="text-sm font-semibold text-gray-800">체크리스트</h2>
          </div>
          {checklistLoadError && (
            <p className="text-xs text-red-600 mb-2 shrink-0">
              체크리스트를 불러오지 못했습니다: {checklistLoadError}
            </p>
          )}
          {checklistLoading ? (
            <div className="flex-1 min-h-0 flex items-center justify-center">
              <div className="animate-spin rounded-full h-8 w-8 border-2 border-azure-blue border-t-transparent" />
            </div>
          ) : checklistOptions.length === 0 ? (
            <p className="text-sm text-gray-400 flex-1 min-h-0 flex items-center">등록된 체크리스트가 없습니다.</p>
          ) : (
            <div className="flex-1 min-h-0 overflow-auto grid grid-cols-1 sm:grid-cols-2 gap-2 pr-1 content-start">
              {checklistOptions.map((cl) => {
                const applicable = isChecklistApplicable(cl, selectedTypes);
                return (
                  <label
                    key={cl.id}
                    title={!applicable ? '선택된 리소스 유형에 적용되지 않는 체크리스트입니다.' : undefined}
                    className={`flex items-start gap-2 p-2 rounded-lg border text-sm transition-colors ${
                      applicable
                        ? 'border-gray-100 hover:bg-gray-50 cursor-pointer'
                        : 'border-gray-100 bg-gray-50 opacity-40 cursor-not-allowed'
                    }`}
                  >
                    <input
                      type="checkbox"
                      className="mt-0.5 shrink-0"
                      checked={selectedChecklistIds.has(cl.id)}
                      disabled={!applicable}
                      onChange={(e) => {
                        setSelectedChecklistIds((prev) => {
                          const n = new Set(prev);
                          if (e.target.checked) n.add(cl.id);
                          else n.delete(cl.id);
                          return n;
                        });
                      }}
                    />
                    <span className="min-w-0">
                      <span className="font-medium text-gray-800 block">{cl.name}</span>
                      <span className="text-[11px] text-gray-500">
                        ID: {cl.id}
                        {typeof cl.total_checks === 'number' ? ` · 항목 ${cl.total_checks}개` : ''}
                      </span>
                      {cl.applicable_resource_types && cl.applicable_resource_types.length > 0 && (
                        <span className="text-[10px] text-gray-400 block mt-0.5 truncate" title={cl.applicable_resource_types.join(', ')}>
                          대상: {cl.applicable_resource_types.join(', ')}
                        </span>
                      )}
                      {!applicable && selectedTypes.size > 0 && (
                        <span className="text-[10px] text-amber-500 block mt-0.5 font-medium">
                          ⚠ 선택 리소스 유형 미해당
                        </span>
                      )}
                    </span>
                  </label>
                );
              })}
            </div>
          )}
        </div>
      </div>
      </div>
    </div>
  );
}
