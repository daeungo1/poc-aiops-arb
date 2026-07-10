import { useCallback, useEffect, useState, useMemo } from 'react';
import { AssessmentReportContentViewer } from './AssessmentReportContentViewer';
import { AssessmentReportGroupedList } from './AssessmentReportGroupedList';
import { useAzureSession } from '../context/AzureSessionContext';
import { useTerraformRun } from '../context/TerraformRunContext';
import { assessmentReportBasename, groupAssessmentReportsByStem } from '../lib/assessmentReportPaths';
import { azureScopeHeaders } from '../lib/azureScopeHeaders';
import { CHAT_REFRESH_ASSESSMENTS_EVENT } from '../lib/chatDataRefreshEvents';
import { authFetch } from '../lib/authRest';
import { DETAIL_HEADER_BTN_DOWNLOAD } from '../lib/detailPanelButtons';
import { fetchJsonArray, isAbortError } from '../lib/safeDashboardFetch';
import type { NavigateTo } from '../App';

// ─── 테라폼 결과 모달 ─────────────────────────────────────────────────────────

interface TerraformResultState {
  success: boolean;
  message: string;
  runId?: number | null;
}

function TerraformResultModal({ result, onClose, onNavigate }: {
  result: TerraformResultState;
  onClose: () => void;
  onNavigate?: NavigateTo;
}) {
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 backdrop-blur-sm"
      onClick={onClose}
    >
      <div
        className="bg-white rounded-2xl shadow-2xl w-full max-w-lg mx-4 flex flex-col overflow-hidden"
        onClick={e => e.stopPropagation()}
      >
        {/* 헤더 */}
        <div className={`px-6 py-5 flex items-start gap-4 ${result.success ? 'bg-emerald-50 border-b border-emerald-100' : 'bg-rose-50 border-b border-rose-100'}`}>
          <div className={`shrink-0 w-10 h-10 rounded-full flex items-center justify-center text-xl font-bold ${result.success ? 'bg-emerald-100 text-emerald-600' : 'bg-rose-100 text-rose-500'}`}>
            {result.success ? '✓' : '✕'}
          </div>
          <div className="flex-1 min-w-0">
            <p className={`text-xs font-semibold uppercase tracking-wider mb-0.5 ${result.success ? 'text-emerald-600' : 'text-rose-500'}`}>
              {result.success ? '생성 완료' : '생성 실패'}
            </p>
            <h3 className="text-base font-bold text-slate-800">테라폼 코드 생성</h3>
          </div>
          <button
            className="shrink-0 p-1.5 text-slate-400 hover:text-slate-600 hover:bg-white/60 rounded-lg transition-colors"
            onClick={onClose}
          >
            ✕
          </button>
        </div>

        {/* 본문 */}
        <div className="px-6 py-5">
          {result.success ? (
            <div className="flex items-center gap-3 bg-emerald-50 border border-emerald-200 rounded-xl px-5 py-4">
              <span className="text-xs font-semibold text-emerald-600 uppercase tracking-wider whitespace-nowrap">생성 ID</span>
              <span className="text-2xl font-extrabold text-emerald-700 tabular-nums">
                {result.runId != null ? `#${result.runId}` : '–'}
              </span>
            </div>
          ) : (
            <div className="rounded-xl border px-4 py-3.5 text-sm leading-relaxed whitespace-pre-wrap bg-rose-50 border-rose-100 text-rose-700">
              {result.message}
            </div>
          )}
        </div>

        {/* 푸터 */}
        <div className="px-6 pb-5 flex items-center justify-end gap-2">
          <button
            type="button"
            onClick={onClose}
            className="px-5 py-2 rounded-lg text-sm font-semibold bg-slate-100 text-slate-600 hover:bg-slate-200 transition-colors"
          >
            닫기
          </button>
          {result.success && result.runId != null && onNavigate && (
            <button
              type="button"
              onClick={() => {
                onClose();
                onNavigate('terraform', {
                  selectTerraform: { subscription_id: '', timestamp: '', run_id: result.runId! },
                });
              }}
              className="px-5 py-2 rounded-lg text-sm font-semibold bg-emerald-600 text-white hover:bg-emerald-700 transition-colors flex items-center gap-1.5"
            >
              자동생성코드 보기 →
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

interface AssessmentFile {
  filename: string;
  date: string;
  size: number;
  source: 'azure' | 'local';
}

function formatKoreanDateTime(value: string): string {
  const normalized = (value || '').replace('T', ' ');
  const date = normalized.slice(0, 10);
  const time = normalized.slice(11, 19);
  const [year, month, day] = date.split('-');
  const [hour, minute, second] = time.split(':');
  if (!year || !month || !day || !hour || !minute || !second) return value || '-';
  return `${year}년 ${month}월 ${day}일 ${hour}시 ${minute}분 ${second}초`;
}

export interface AssessmentResultsPanelProps {
  initialFilename?: string | null;
  onInitialFilenameConsumed?: () => void;
  /** 진단평가 완료 직후 평가결과 탭으로 올 때 최신 그룹 첫 파일을 펼쳐 선택 */
  openLatestGroupAfterAssessment?: boolean;
  onOpenLatestGroupConsumed?: () => void;
  onNavigate?: NavigateTo;
}

export function AssessmentResultsPanel({
  initialFilename = null,
  onInitialFilenameConsumed,
  openLatestGroupAfterAssessment = false,
  onOpenLatestGroupConsumed,
  onNavigate,
}: AssessmentResultsPanelProps) {
  const { tenantId, subscriptionId, azureBootstrapComplete } = useAzureSession();
  const [files, setFiles] = useState<AssessmentFile[]>([]);
  const [loading, setLoading] = useState(true);
  const [selectedFile, setSelectedFile] = useState<string | null>(null);
  const [content, setContent] = useState<string>('');
  const [contentLoading, setContentLoading] = useState(false);
  const [downloading, setDownloading] = useState(false);
  const [terraformResult, setTerraformResult] = useState<TerraformResultState | null>(null);

  const selectedFileDate = useMemo(
    () => files.find((f) => f.filename === selectedFile)?.date ?? '',
    [files, selectedFile],
  );

  useEffect(() => {
    if (!azureBootstrapComplete) return;

    const ac = new AbortController();
    setLoading(true);
    fetchJsonArray('/api/assessments', { signal: ac.signal })
      .then((data) => {
        setFiles(data as AssessmentFile[]);
      })
      .catch((e) => {
        if (!isAbortError(e)) {
          console.warn('assessments list', e);
        }
      })
      .finally(() => setLoading(false));
    return () => ac.abort();
  }, [tenantId, subscriptionId, azureBootstrapComplete]);

  const loadContent = useCallback(
    async (filename: string) => {
      setSelectedFile(filename);
      setContentLoading(true);
      try {
        const scope = azureScopeHeaders(tenantId, subscriptionId);
        const res = await authFetch(`/api/assessments/${encodeURIComponent(filename)}`, {
          headers: scope,
        });
        if (!res.ok) throw new Error(String(res.status));
        const data = (await res.json()) as { content?: string };
        if (typeof data.content !== 'string') throw new Error('invalid payload');
        setContent(data.content);
      } catch {
        setContent('파일을 불러올 수 없습니다.');
      }
      setContentLoading(false);
    },
    [tenantId, subscriptionId],
  );

  const handleDownload = useCallback(async () => {
    if (!selectedFile) return;
    setDownloading(true);
    try {
      const scope = azureScopeHeaders(tenantId, subscriptionId);
      const url = `/api/assessments/${encodeURIComponent(selectedFile)}?download=1`;
      const res = await authFetch(url, { headers: scope });
      if (!res.ok) throw new Error(String(res.status));
      const blob = await res.blob();
      const name = assessmentReportBasename(selectedFile);
      const a = document.createElement('a');
      const href = URL.createObjectURL(blob);
      a.href = href;
      a.download = name;
      a.rel = 'noopener';
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(href);
    } catch {
      alert('파일을 다운로드할 수 없습니다.');
    } finally {
      setDownloading(false);
    }
  }, [selectedFile, tenantId, subscriptionId]);

  useEffect(() => {
    if (!azureBootstrapComplete) return;

    const onChatRefresh = () => {
      void (async () => {
        try {
          const data = await fetchJsonArray('/api/assessments');
          setFiles(data as AssessmentFile[]);
        } catch (e) {
          if (!isAbortError(e)) {
            console.warn('assessments list refresh (chat)', e);
          }
        }
        if (selectedFile) {
          await loadContent(selectedFile);
        }
      })();
    };

    window.addEventListener(CHAT_REFRESH_ASSESSMENTS_EVENT, onChatRefresh);
    return () => window.removeEventListener(CHAT_REFRESH_ASSESSMENTS_EVENT, onChatRefresh);
  }, [tenantId, subscriptionId, azureBootstrapComplete, selectedFile, loadContent]);

  useEffect(() => {
    if (loading || !initialFilename || files.length === 0) return;
    const exists = files.some((f) => f.filename === initialFilename);
    if (!exists) {
      onInitialFilenameConsumed?.();
      return;
    }
    void loadContent(initialFilename).finally(() => onInitialFilenameConsumed?.());
  }, [loading, files, initialFilename, loadContent, onInitialFilenameConsumed]);

  useEffect(() => {
    if (!openLatestGroupAfterAssessment || loading || files.length === 0) return;
    const groups = groupAssessmentReportsByStem(files);
    const firstFile = groups[0]?.files[0];
    onOpenLatestGroupConsumed?.();
    if (!firstFile) return;
    void loadContent(firstFile.filename);
  }, [
    openLatestGroupAfterAssessment,
    loading,
    files,
    loadContent,
    onOpenLatestGroupConsumed,
  ]);

  return (
    <div className="p-6 h-full flex flex-col">
      {terraformResult && (
        <TerraformResultModal
          result={terraformResult}
          onClose={() => setTerraformResult(null)}
          onNavigate={onNavigate}
        />
      )}
      <div className="mb-4 flex justify-between items-end">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">평가결과</h1>
          <p className="text-sm text-gray-500 mt-1">
            Azure 리소스 평가 리포트를 확인하세요. 진단 평가 탭에서 평가를 실행하거나 챗봇을 이용할 수 있습니다.
          </p>
        </div>
      </div>

      {loading ? (
        <div className="flex items-center justify-center h-64">
          <div className="animate-spin rounded-full h-10 w-10 border-b-2 border-azure-blue" />
        </div>
      ) : files.length === 0 ? (
        <ResultsEmptyState />
      ) : (
        <div className="flex-1 flex gap-4 min-h-0">
          <div className="w-72 shrink-0 bg-white rounded-xl shadow-sm border border-gray-200 overflow-hidden flex flex-col">
            <div className="px-4 py-3 border-b border-gray-100 bg-gray-50">
              <h2 className="text-sm font-semibold text-gray-700">리포트 목록 ({files.length})</h2>
            </div>
            <div className="flex-1 min-h-0 flex flex-col overflow-hidden p-2">
              <AssessmentReportGroupedList
                files={files}
                selectedFilename={selectedFile}
                onSelectFile={(fn) => void loadContent(fn)}
                hideGroupFileHeader
              />
            </div>
          </div>

          <div className="flex-1 bg-white rounded-xl shadow-sm border border-gray-200 overflow-hidden flex flex-col">
            {selectedFile ? (
              <>
                <div className="px-5 py-3 border-b border-gray-100 bg-gray-50 flex justify-between items-center gap-3">
                  <div className="min-w-0">
                    <h2 className="text-base font-semibold text-gray-800 truncate">
                      {assessmentReportBasename(selectedFile)}
                    </h2>
                    <p className="text-xs text-gray-500 mt-0.5">
                      {formatKoreanDateTime(selectedFileDate)}
                    </p>
                  </div>
                  <div className="flex gap-2 flex-wrap justify-end shrink-0">
                    {/* MODIFIED: 선택 과정 없이 즉시 전체 생성 버튼으로 변경 */}
                    <GenerateTerraformButton
                      onGenerate={async () => {
                        try {
                          const scope = azureScopeHeaders(tenantId, subscriptionId);
                          const res = await authFetch('/api/terraform/generate', {
                            method: 'POST',
                            headers: { ...scope, 'Content-Type': 'application/json' },
                            body: JSON.stringify({
                              resource_names: [],
                              assessment_filename: selectedFile
                            })
                          });
                          if (!res.ok) throw new Error('API 요청 실패');
                          const data = await res.json();
                          if (data.success) {
                            setTerraformResult({ success: true, message: data.summary, runId: data.run_id ?? null });
                          } else {
                            setTerraformResult({ success: false, message: data.detail || '생성에 실패했습니다.' });
                          }
                        } catch (err) {
                          setTerraformResult({ success: false, message: String(err) });
                        }
                      }}
                    />
                    <button
                      type="button"
                      onClick={() => void handleDownload()}
                      disabled={downloading}
                      className={`${DETAIL_HEADER_BTN_DOWNLOAD} disabled:opacity-50`}
                    >
                      {downloading ? '다운로드 중…' : '다운로드'}
                    </button>
                  </div>
                </div>
                <div className="flex-1 overflow-auto p-5">
                  {contentLoading ? (
                    <div className="flex items-center justify-center h-32">
                      <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-azure-blue" />
                    </div>
                  ) : (
                    <AssessmentReportContentViewer
                      content={content}
                      filename={selectedFile}
                      className="text-sm"
                    />
                  )}
                </div>
              </>
            ) : (
              <div className="flex-1 flex items-center justify-center text-sm text-gray-400">
                왼쪽 목록에서 리포트를 선택하세요
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

/**
 * MODIFIED: 선택 없이 즉시 전체 테라폼 생성을 수행하는 버튼 컴포넌트
 * isRunning 상태를 TerraformRunContext에서 가져와 페이지 이동 후에도 유지됩니다.
 */
function GenerateTerraformButton({ onGenerate }: { onGenerate: () => Promise<void> }) {
  const { isRunning, startRun, finishRun } = useTerraformRun();

  return (
    <button
      type="button"
      disabled={isRunning}
      onClick={async () => {
        if (isRunning) return;
        startRun();
        try {
          await onGenerate();
        } finally {
          finishRun();
        }
      }}
      className="px-3 py-1.5 text-xs font-semibold bg-azure-blue text-white rounded-md hover:bg-azure-dark flex items-center gap-1.5 shadow-sm disabled:opacity-50 transition-colors"
    >
      {isRunning ? (
        <>
          <span className="animate-spin">🔄</span> 코드 생성 중...
        </>
      ) : (
        <>
          <span>🏗️</span> 테라폼 생성
        </>
      )}
    </button>
  );
}

function ResultsEmptyState() {
  return (
    <div className="flex-1 flex items-center justify-center">
      <div className="text-center">
        <div className="text-5xl mb-4">🔬</div>
        <h3 className="text-lg font-semibold text-gray-700 mb-2">아직 평가 결과가 없습니다</h3>
        <p className="text-sm text-gray-500 mb-4 max-w-md">
          <strong>진단평가</strong> 탭에서 평가를 실행하거나, 챗봇에서{' '}
          <code className="bg-gray-100 px-1.5 py-0.5 rounded">"전체 리소스 평가를 실행해줘"</code>를 입력하세요.
        </p>
      </div>
    </div>
  );
}
