import { useCallback, useEffect, useMemo, useState } from 'react';
import { ExternalLink } from 'lucide-react';
import { MarkdownViewer, CodeViewer } from './MarkdownViewer';
import { useAzureSession } from '../context/AzureSessionContext';
import { azureScopeHeaders } from '../lib/azureScopeHeaders';
import { CHAT_REFRESH_TERRAFORM_EVENT } from '../lib/chatDataRefreshEvents';
import { authFetch } from '../lib/authRest';
import { DETAIL_HEADER_BTN_COPY, DETAIL_HEADER_BTN_DOWNLOAD } from '../lib/detailPanelButtons';
import { fetchJsonArray, isAbortError } from '../lib/safeDashboardFetch';
import type { NavigateTo } from '../App';

interface TerraformOutput {
  run_id?: number;
  subscription_id: string;
  timestamp: string;
  files: string[];
  created_at?: string;
  source?: string;
  resources_count?: number;
  recommendations_count?: number;
  source_report_ids?: Array<number | string>;
  source_diagnosis_ids?: Array<number | string>;
  source_resource_names?: string[];
}

interface TerraformFile {
  filename: string;
  timestamp: string;
  subscription_id: string;
  content: string;
}

type TerraformInitialRun = {
  subscription_id: string;
  timestamp: string;
  filename?: string;
  run_id?: number;
};

const TERRAFORM_FILE_ORDER: Record<string, number> = {
  'README.md': 0,
  summary_md: 1,
  'main.tf': 2,
  'variables.tf': 3,
  'provider.tf': 4,
  'outputs.tf': 5,
};

function getTerraformFileOrder(filename: string): number {
  if (/^terraform_.*\.md$/i.test(filename)) return TERRAFORM_FILE_ORDER.summary_md;
  return TERRAFORM_FILE_ORDER[filename] ?? 99;
}

function sortTerraformFiles(files: string[]): string[] {
  return [...files].sort((a, b) => {
    const ao = getTerraformFileOrder(a);
    const bo = getTerraformFileOrder(b);
    if (ao !== bo) return ao - bo;
    return a.localeCompare(b);
  });
}

function parseTerraformDate(value: string): Date | null {
  if (!value) return null;
  const iso = new Date(value);
  if (!Number.isNaN(iso.getTime())) return iso;
  if (/^\d{8}_\d{6}$/.test(value)) {
    const normalized = `${value.slice(0, 4)}-${value.slice(4, 6)}-${value.slice(6, 8)}T${value.slice(9, 11)}:${value.slice(11, 13)}:${value.slice(13, 15)}`;
    const parsed = new Date(normalized);
    if (!Number.isNaN(parsed.getTime())) return parsed;
  }
  return null;
}

function formatRelativeTime(value: string, now = new Date()): string {
  const parsed = parseTerraformDate(value);
  if (!parsed) return value || '-';

  const diffSeconds = Math.max(0, Math.floor((now.getTime() - parsed.getTime()) / 1000));
  if (diffSeconds < 60) return `${diffSeconds}초 전`;

  const diffMinutes = Math.floor(diffSeconds / 60);
  if (diffMinutes < 60) return `${diffMinutes}분 전`;

  const diffHours = Math.floor(diffMinutes / 60);
  if (diffHours < 24) return `${diffHours}시간 전`;

  const diffDays = Math.floor(diffHours / 24);
  if (diffDays < 30) return `${diffDays}일 전`;

  const diffMonths = Math.floor(diffDays / 30);
  if (diffMonths < 12) return `${diffMonths}달 전`;

  const diffYears = Math.floor(diffMonths / 12);
  return `${diffYears}년 전`;
}

function formatKoreanDateTime(value: string): string {
  const parsed = parseTerraformDate(value);
  if (!parsed) return value || '-';
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${parsed.getFullYear()}년 ${pad(parsed.getMonth() + 1)}월 ${pad(parsed.getDate())}일 ${pad(parsed.getHours())}시 ${pad(parsed.getMinutes())}분 ${pad(parsed.getSeconds())}초`;
}

function terraformRunLabel(output: TerraformOutput): string {
  return output.run_id ? `Terraform_${output.run_id}` : `Terraform_${output.timestamp}`;
}

interface TerraformBoardProps {
  initialRun?: TerraformInitialRun | null;
  onInitialRunConsumed?: () => void;
  onNavigate?: NavigateTo;
}

export function TerraformBoard({
  initialRun = null,
  onInitialRunConsumed,
  onNavigate,
}: TerraformBoardProps) {
  const { tenantId, subscriptionId, azureBootstrapComplete } = useAzureSession();
  const [outputs, setOutputs] = useState<TerraformOutput[]>([]);
  const [loading, setLoading] = useState(true);
  const [selectedFile, setSelectedFile] = useState<TerraformFile | null>(null);
  const [fileLoading, setFileLoading] = useState(false);
  const [expandedRuns, setExpandedRuns] = useState<Record<string, boolean>>({});
  const now = useMemo(() => new Date(), [outputs]);

  const selectedOutput = useMemo(
    () =>
      selectedFile
        ? outputs.find(
            (o) =>
              o.subscription_id === selectedFile.subscription_id &&
              o.timestamp === selectedFile.timestamp,
          ) ?? null
        : null,
    [outputs, selectedFile],
  );

  useEffect(() => {
    if (!azureBootstrapComplete) return;

    const ac = new AbortController();
    setLoading(true);
    const scope = azureScopeHeaders(tenantId, subscriptionId);
    fetchJsonArray('/api/terraform', { signal: ac.signal, headers: scope })
      .then((data) => {
        const rows = (data as TerraformOutput[]).map((r) => ({
          ...r,
          subscription_id: r.subscription_id ?? 'legacy',
        }));
        setOutputs(rows);
      })
      .catch((e) => {
        if (!isAbortError(e)) {
          console.warn('terraform list', e);
        }
      })
      .finally(() => setLoading(false));
    return () => ac.abort();
  }, [tenantId, subscriptionId, azureBootstrapComplete]);

  const loadFile = useCallback(
    async (subId: string, timestamp: string, filename: string) => {
      setFileLoading(true);
      try {
        const scope = azureScopeHeaders(tenantId, subscriptionId);
        const res = await authFetch(
          `/api/terraform/${encodeURIComponent(subId)}/${encodeURIComponent(timestamp)}/${encodeURIComponent(filename)}`,
          { headers: scope },
        );
        if (!res.ok) throw new Error(String(res.status));
        const data = (await res.json()) as TerraformFile;
        if (typeof data.content !== 'string') throw new Error('invalid payload');
        setSelectedFile(data);
      } catch {
        setSelectedFile(null);
      }
      setFileLoading(false);
    },
    [tenantId, subscriptionId],
  );

  useEffect(() => {
    if (!azureBootstrapComplete) return;

    const onChatRefresh = () => {
      void (async () => {
        const scope = azureScopeHeaders(tenantId, subscriptionId);
        try {
          const newData = await fetchJsonArray('/api/terraform', { headers: scope });
          const rows = (newData as TerraformOutput[]).map((r) => ({
            ...r,
            subscription_id: r.subscription_id ?? 'legacy',
          }));
          setOutputs(rows);
        } catch (e) {
          if (!isAbortError(e)) {
            console.warn('terraform list refresh (chat)', e);
          }
        }
        if (selectedFile) {
          await loadFile(
            selectedFile.subscription_id,
            selectedFile.timestamp,
            selectedFile.filename,
          );
        }
      })();
    };

    window.addEventListener(CHAT_REFRESH_TERRAFORM_EVENT, onChatRefresh);
    return () => window.removeEventListener(CHAT_REFRESH_TERRAFORM_EVENT, onChatRefresh);
  }, [tenantId, subscriptionId, azureBootstrapComplete, selectedFile, loadFile]);

  useEffect(() => {
    if (loading || !initialRun || outputs.length === 0) return;
    const sub = initialRun.subscription_id;
    const run = initialRun.run_id != null
      ? outputs.find((o) => o.run_id === initialRun.run_id)
      : outputs.find((o) => o.subscription_id === sub && o.timestamp === initialRun.timestamp);
    if (!run || run.files.length === 0) {
      onInitialRunConsumed?.();
      return;
    }
    const pick =
      (initialRun.filename && run.files.includes(initialRun.filename)
        ? initialRun.filename
        : null) ||
      sortTerraformFiles(run.files)[0] ||
      run.files[0];
    void loadFile(run.subscription_id, run.timestamp, pick).finally(() =>
      onInitialRunConsumed?.(),
    );
  }, [loading, outputs, initialRun, loadFile, onInitialRunConsumed]);

  const getFileIcon = (filename: string) => {
    if (filename.endsWith('.tf')) return '📄';
    if (filename.endsWith('.md')) return '📝';
    return '📎';
  };

  const getFileLanguage = (filename: string) => {
    if (filename.endsWith('.tf')) return 'hcl';
    if (filename.endsWith('.md')) return 'markdown';
    return 'text';
  };

  const toggleRunExpanded = (runKey: string) => {
    setExpandedRuns((prev) => ({ ...prev, [runKey]: !prev[runKey] }));
  };

  const toggleRunExpandedAndSelectFirst = (output: TerraformOutput) => {
    const runKey = `${output.subscription_id}-${output.timestamp}`;
    const wasExpanded = expandedRuns[runKey] ?? false;
    setExpandedRuns((prev) => ({ ...prev, [runKey]: !wasExpanded }));
    if (!wasExpanded) {
      const first = sortTerraformFiles(output.files)[0];
      if (first) void loadFile(output.subscription_id, output.timestamp, first);
    }
  };

  const handleReportIdClick = useCallback(
    async (reportId: number) => {
      try {
        const scope = azureScopeHeaders(tenantId, subscriptionId);
        const rows = await fetchJsonArray('/api/assessments', { headers: scope }) as Array<{
          filename?: string;
          report_id?: number;
        }>;
        const picked =
          rows.find((r) => r.report_id === reportId && (r.filename ?? '').endsWith('.json')) ??
          rows.find((r) => r.report_id === reportId);
        if (!picked?.filename) {
          alert(`진단평가결과 ID ${reportId}에 해당하는 리포트를 찾지 못했습니다.`);
          return;
        }
        onNavigate?.('assessments', {
          initialAssessmentTab: 'results',
          selectAssessmentFilename: picked.filename,
        });
      } catch {
        alert('리포트 이동 중 오류가 발생했습니다.');
      }
    },
    [tenantId, subscriptionId, onNavigate],
  );

  const getTraceableReportIds = (output: TerraformOutput): number[] => {
    const raw =
      output.source_report_ids && output.source_report_ids.length > 0
        ? output.source_report_ids
        : output.source_diagnosis_ids ?? [];
    return raw
      .map((v) => Number(v))
      .filter((v) => Number.isFinite(v) && v > 0);
  };

  return (
    <div className="p-6 h-full flex flex-col">
      {/* Header */}
      <div className="mb-4 flex justify-between items-end">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Terraform 출력</h1>
          <p className="text-sm text-gray-500 mt-1">
            평가 결과 기반으로 자동 생성된 Terraform 코드입니다. 챗봇에서 "Terraform 코드 생성"을 요청하면 새로 생성됩니다.
          </p>
        </div>
      </div>

      {loading ? (
        <div className="flex items-center justify-center h-64">
          <div className="animate-spin rounded-full h-10 w-10 border-b-2 border-azure-blue" />
        </div>
      ) : outputs.length === 0 ? (
        <EmptyState />
      ) : (
        <div className="flex-1 flex gap-4 min-h-0">
          {/* Output List */}
          <div className="w-72 shrink-0 bg-white rounded-xl shadow-sm border border-gray-200 overflow-hidden flex flex-col">
            <div className="px-4 py-3 border-b border-gray-100 bg-gray-50">
              <h2 className="text-sm font-semibold text-gray-700">
                출력 세트 ({outputs.length})
              </h2>
            </div>
            <div className="flex-1 overflow-auto">
              {outputs.map((output) => (
                <div
                  key={`${output.subscription_id}-${output.timestamp}`}
                  className="border-b border-gray-100 transition-colors"
                >
                  {(() => {
                    const runKey = `${output.subscription_id}-${output.timestamp}`;
                    const expanded = expandedRuns[runKey] ?? false;
                    const sortedFiles = sortTerraformFiles(output.files);
                    const markdownFiles = sortedFiles.filter((f) => f.endsWith('.md'));
                    const tfFiles = sortedFiles.filter((f) => f.endsWith('.tf'));
                    return (
                      <div className="px-4 py-3 space-y-2">
                        <button
                          type="button"
                          onClick={() => toggleRunExpandedAndSelectFirst(output)}
                          className="w-full text-left flex min-h-[44px] items-center justify-between gap-2 text-xs font-semibold text-gray-700 hover:text-gray-900"
                        >
                          <span className="min-w-0 flex-1">
                            <span className="block truncate">📂 {terraformRunLabel(output)}</span>
                            <span className="block text-right text-[11px] font-normal text-gray-400">
                              {formatRelativeTime(output.created_at || output.timestamp, now)}
                            </span>
                          </span>
                          <span className="shrink-0 text-gray-500">{expanded ? '▴' : '▾'}</span>
                        </button>

                        {expanded ? (
                          <div className="space-y-2">
                            <div className="space-y-1">
                              {markdownFiles.map((f) => (
                                <button
                                  key={f}
                                  onClick={() => loadFile(output.subscription_id, output.timestamp, f)}
                                  className={`
                                    w-full text-left text-[11px] px-2 py-1 rounded border transition-colors
                                    ${selectedFile?.timestamp === output.timestamp && selectedFile?.subscription_id === output.subscription_id && selectedFile?.filename === f
                                      ? 'bg-azure-blue text-white border-azure-blue'
                                      : 'bg-white text-gray-600 border-gray-200 hover:bg-gray-50'
                                    }
                                  `}
                                >
                                  {getFileIcon(f)} {f}
                                </button>
                              ))}
                            </div>
                            <div className="grid grid-cols-2 gap-1">
                              {tfFiles.map((f) => (
                                <button
                                  key={f}
                                  onClick={() => loadFile(output.subscription_id, output.timestamp, f)}
                                  className={`
                                    text-left text-[11px] px-2 py-1 rounded border transition-colors
                                    ${selectedFile?.timestamp === output.timestamp && selectedFile?.subscription_id === output.subscription_id && selectedFile?.filename === f
                                      ? 'bg-azure-blue text-white border-azure-blue'
                                      : 'bg-white text-gray-600 border-gray-200 hover:bg-gray-50'
                                    }
                                  `}
                                >
                                  {getFileIcon(f)} {f}
                                </button>
                              ))}
                            </div>
                          </div>
                        ) : null}
                      </div>
                    );
                  })()}
                </div>
              ))}
            </div>
          </div>

          {/* File Viewer */}
          <div className="flex-1 bg-white rounded-xl shadow-sm border border-gray-200 overflow-hidden flex flex-col">
            {selectedFile && !fileLoading ? (
              <>
                <div className="px-5 py-3 border-b border-gray-100 bg-gray-50 flex justify-between items-center gap-3">
                  <div className="min-w-0 space-y-2">
                    <div className="flex items-center gap-2 min-w-0">
                      <span className="shrink-0">{getFileIcon(selectedFile.filename)}</span>
                      <span className="text-base font-semibold text-gray-800 truncate">
                        {selectedFile.filename}
                      </span>
                      <span className="text-[10px] bg-gray-200 text-gray-600 px-1.5 py-0.5 rounded shrink-0">
                        {getFileLanguage(selectedFile.filename)}
                      </span>
                    </div>
                    {selectedOutput && (
                      <div className="space-y-1.5">
                        <div className="flex flex-wrap items-center gap-1.5">
                          {((selectedOutput.resources_count ?? 0) > 0 || (selectedOutput.recommendations_count ?? 0) > 0) && (
                            <>
                              {(selectedOutput.resources_count ?? 0) > 0 && (
                                <span className="text-[10px] bg-blue-50 text-blue-700 border border-blue-200 px-1.5 py-0.5 rounded-full font-medium">
                                  리소스 {selectedOutput.resources_count}개
                                </span>
                              )}
                              {(selectedOutput.recommendations_count ?? 0) > 0 && (
                                <span className="text-[10px] bg-amber-50 text-amber-700 border border-amber-200 px-1.5 py-0.5 rounded-full font-medium">
                                  권고 {selectedOutput.recommendations_count}건
                                </span>
                              )}
                            </>
                          )}
                          {getTraceableReportIds(selectedOutput).length > 0 ? (
                            getTraceableReportIds(selectedOutput).map((rid) => (
                              <button
                                key={rid}
                                type="button"
                                onClick={() => void handleReportIdClick(rid)}
                                className="flex max-w-full items-center gap-2 rounded-lg border border-purple-200 bg-purple-50 px-2.5 py-1 text-left text-xs font-semibold text-purple-800 transition-colors hover:border-purple-300 hover:bg-purple-100"
                                title={`진단 평가 결과 ID ${rid}로 이동`}
                              >
                                <span className="truncate">진단 평가 결과 ID: {rid}</span>
                                <span className="flex h-4 w-4 shrink-0 items-center justify-center rounded border border-purple-300 bg-white text-purple-700">
                                  <ExternalLink size={12} strokeWidth={2.2} />
                                </span>
                              </button>
                            ))
                          ) : (
                            <span className="text-[11px] text-gray-400">진단 평가 결과 ID: 없음</span>
                          )}
                        </div>
                        <p className="text-xs text-gray-500">
                          {formatKoreanDateTime(selectedOutput.created_at || selectedOutput.timestamp)}
                        </p>
                      </div>
                    )}
                  </div>
                  <div className="flex gap-2 flex-wrap justify-end shrink-0">
                    <button
                      type="button"
                      onClick={() => {
                        void navigator.clipboard.writeText(selectedFile.content);
                      }}
                      className={DETAIL_HEADER_BTN_COPY}
                    >
                      복사
                    </button>
                    <a
                      href={`/api/downloads/${encodeURIComponent(selectedFile.subscription_id)}/${encodeURIComponent(selectedFile.timestamp)}/${encodeURIComponent(selectedFile.filename)}`}
                      target="_blank"
                      rel="noopener noreferrer"
                      className={`${DETAIL_HEADER_BTN_DOWNLOAD} no-underline`}
                    >
                      다운로드
                    </a>
                  </div>
                </div>
                <div className="flex-1 overflow-auto">
                  {selectedFile.filename.endsWith('.md') ? (
                    <div className="p-5">
                      <MarkdownViewer content={selectedFile.content} className="text-sm" />
                    </div>
                  ) : (
                    <div className="p-4">
                      <CodeViewer
                        code={selectedFile.content}
                        language={getFileLanguage(selectedFile.filename)}
                        filename={selectedFile.filename}
                      />
                    </div>
                  )}
                </div>
              </>
            ) : fileLoading ? (
              <div className="flex-1 flex items-center justify-center">
                <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-azure-blue" />
              </div>
            ) : (
              <div className="flex-1 flex items-center justify-center text-sm text-gray-400">
                왼쪽에서 파일을 선택하세요
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

/* ── Empty State ──────────────────────────────────────────── */
function EmptyState() {
  return (
    <div className="flex-1 flex items-center justify-center">
      <div className="text-center">
        <div className="text-5xl mb-4">🏗️</div>
        <h3 className="text-lg font-semibold text-gray-700 mb-2">
          Terraform 코드가 아직 없습니다
        </h3>
        <p className="text-sm text-gray-500 mb-4 max-w-md">
          평가 실행 후 오른쪽 챗봇에서{' '}
          <code className="bg-gray-100 px-1.5 py-0.5 rounded">
            "실패 항목에 대한 Terraform 코드를 생성해줘"
          </code>
          를 입력하세요.
        </p>
      </div>
    </div>
  );
}
