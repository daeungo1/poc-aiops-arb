import { useEffect, useState } from 'react';
import { authFetch } from '../lib/authRest';
import { DETAIL_HEADER_BTN_DOWNLOAD } from '../lib/detailPanelButtons';

// ─── 결과 모달 ────────────────────────────────────────────────────────────────

interface ModalResult {
  success: boolean;
  title: string;
  message: string;
}

function ResultModal({ result, onClose }: { result: ModalResult; onClose: () => void }) {
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
              {result.success ? '완료' : '실패'}
            </p>
            <h3 className="text-base font-bold text-slate-800">{result.title}</h3>
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
          <div className={`rounded-xl border px-4 py-3.5 text-sm leading-relaxed whitespace-pre-wrap ${
            result.success
              ? 'bg-slate-50 border-slate-100 text-slate-700'
              : 'bg-rose-50 border-rose-100 text-rose-700'
          }`}>
            {result.message}
          </div>
        </div>

        {/* 푸터 */}
        <div className="px-6 pb-5 flex justify-end">
          <button
            type="button"
            onClick={onClose}
            className={`px-5 py-2 rounded-lg text-sm font-semibold transition-colors ${
              result.success
                ? 'bg-emerald-600 text-white hover:bg-emerald-700'
                : 'bg-slate-200 text-slate-700 hover:bg-slate-300'
            }`}
          >
            확인
          </button>
        </div>
      </div>
    </div>
  );
}

interface ChecklistInfo {
  id: string;
  name: string;
  total_checks: number;
  automated_checks: number;
  manual_checks: number;
  applicable_resource_types?: string[];
}

interface ChecklistSummary {
  total_checklists: number;
  total_checks: number;
  automated_checks: number;
  manual_checks: number;
  checklists: ChecklistInfo[];
}

interface ChecklistDetail {
  name: string;
  version: string;
  description: string;
  applicable_resource_types: string[];
  categories: {
    id: string;
    name: string;
    items: {
      id: string;
      name: string;
      checks: {
        question: string;
        check_type: string;
        guidance: string;
      }[];
    }[];
  }[];
}

export function ChecklistBoard() {
  const [summary, setSummary] = useState<ChecklistSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<ChecklistDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [isEditing, setIsEditing] = useState(false);
  const [editContent, setEditContent] = useState('');
  const [modalResult, setModalResult] = useState<ModalResult | null>(null);

  const loadSummary = async () => {
    setLoading(true);
    try {
      const res = await authFetch('/api/checklists');
      const data = await res.json();
      setSummary(data);
    } catch {
      // 유지
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { void loadSummary(); }, []);

  const loadDetail = async (id: string) => {
    setSelectedId(id);
    setDetailLoading(true);
    try {
      const res = await authFetch(`/api/checklists/${encodeURIComponent(id)}`);
      if (!res.ok) {
        setDetail(null);
        setDetailLoading(false);
        return;
      }
      const data = await res.json();
      setDetail(data);
    } catch {
      setDetail(null);
    }
    setDetailLoading(false);
  };

  const handleUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;

    // Validate file extension
    if (!file.name.endsWith('.yaml') && !file.name.endsWith('.yml')) {
      setModalResult({ success: false, title: '체크리스트 업로드', message: 'YAML 파일만 업로드 가능합니다.' });
      return;
    }

    const formData = new FormData();
    formData.append('file', file);

    try {
      const res = await authFetch('/api/checklists/upload', {
        method: 'POST',
        body: formData,
      });

      if (res.ok) {
        await loadSummary();
        setModalResult({ success: true, title: '체크리스트 업로드', message: `${file.name} 업로드가 완료되었습니다.` });
      } else {
        const error = await res.json();
        setModalResult({ success: false, title: '체크리스트 업로드', message: `업로드 실패: ${error.detail || '알 수 없는 오류'}` });
      }
    } catch (err) {
      console.error('Upload error:', err);
      setModalResult({ success: false, title: '체크리스트 업로드', message: '업로드 중 오류가 발생했습니다.' });
    } finally {
      if (e.target) e.target.value = '';
    }
  };

  const handleDelete = async () => {
    if (!selectedId) return;
    if (!confirm(`정말로 삭제하시겠습니까?`)) return;

    try {
      setLoading(true);
      const res = await authFetch(`/api/checklists/${encodeURIComponent(selectedId)}`, {
        method: 'DELETE',
      });
      if (res.ok) {
        alert('삭제되었습니다.');
        setSelectedId(null);
        setDetail(null);
        // Refresh list
        const refreshRes = await authFetch('/api/checklists');
        const newData = await refreshRes.json();
        setSummary(newData);
      } else {
        alert('삭제 실패');
      }
    } catch (err) {
      alert('오류 발생');
    } finally {
      setLoading(false);
    }
  };

  const handleDownloadYaml = async () => {
    if (!selectedId) return;
    try {
      const res = await authFetch(
        `/api/checklists/${encodeURIComponent(selectedId)}/raw`,
      );
      if (!res.ok) {
        alert('다운로드에 실패했습니다.');
        return;
      }
      const data = (await res.json()) as { content?: string };
      const content = typeof data.content === 'string' ? data.content : '';
      const blob = new Blob([content], { type: 'text/yaml;charset=utf-8' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `${selectedId}.yaml`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch {
      alert('다운로드 중 오류가 발생했습니다.');
    }
  };

  const handleEditToggle = async () => {
    if (isEditing) {
      setIsEditing(false);
      return;
    }

    if (!selectedId) return;
    
    try {
      setDetailLoading(true);
      const res = await authFetch(`/api/checklists/${encodeURIComponent(selectedId)}/raw`);
      const data = await res.json();
      setEditContent(data.content);
      setIsEditing(true);
    } catch (err) {
      alert('데이터를 불러오지 못했습니다.');
    } finally {
      setDetailLoading(false);
    }
  };

  const handleSave = async () => {
    if (!selectedId) return;

    try {
      setDetailLoading(true);
      const res = await authFetch(`/api/checklists/${encodeURIComponent(selectedId)}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ content: editContent }),
      });

      if (res.ok) {
        alert('저장되었습니다.');
        setIsEditing(false);
        // Refresh detail
        loadDetail(selectedId);
      } else {
        alert('저장 실패');
      }
    } catch (err) {
      alert('오류 발생');
    } finally {
      setDetailLoading(false);
    }
  };

  return (
    <div className="p-6 h-full flex flex-col">
      {modalResult && (
        <ResultModal result={modalResult} onClose={() => setModalResult(null)} />
      )}
      {/* Header */}
      <div className="mb-4 flex justify-between items-end">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">체크리스트</h1>
          <p className="text-sm text-gray-500 mt-1">
            Azure Architecture Review Board 기반 평가 체크리스트입니다
          </p>
        </div>
        
        <div className="flex items-center gap-2">
          <label className="cursor-pointer bg-azure-blue hover:bg-azure-dark text-white px-4 py-2 rounded-lg text-sm font-medium transition-colors flex items-center gap-2 shadow-sm">
            <span className="text-lg">+</span>
            체크리스트 추가 (YAML)
            <input
              type="file"
              accept=".yaml,.yml"
              className="hidden"
              onChange={handleUpload}
              disabled={loading}
            />
          </label>
        </div>
      </div>

      {loading ? (
        <div className="flex items-center justify-center h-64">
          <div className="animate-spin rounded-full h-10 w-10 border-b-2 border-azure-blue" />
        </div>
      ) : !summary ? (
        <div className="text-center text-gray-500 py-16">체크리스트를 불러올 수 없습니다.</div>
      ) : (
        <>
          {/* Summary Bar */}
          <div className="flex gap-3 mb-4">
            <div className="bg-blue-50 border border-blue-200 rounded-lg px-4 py-2 text-sm">
              <span className="font-semibold text-blue-800">{summary.total_checklists}</span>
              <span className="text-blue-600 ml-1">체크리스트</span>
            </div>
            <div className="bg-green-50 border border-green-200 rounded-lg px-4 py-2 text-sm">
              <span className="font-semibold text-green-800">{summary.total_checks}</span>
              <span className="text-green-600 ml-1">점검 항목</span>
            </div>
            <div className="bg-emerald-50 border border-emerald-200 rounded-lg px-4 py-2 text-sm">
              <span className="font-semibold text-emerald-800">{summary.automated_checks}</span>
              <span className="text-emerald-700 ml-1">AUTO</span>
            </div>
            <div className="bg-orange-50 border border-orange-200 rounded-lg px-4 py-2 text-sm">
              <span className="font-semibold text-orange-800">{summary.manual_checks}</span>
              <span className="text-orange-700 ml-1">MANUAL</span>
            </div>
          </div>

          <div className="flex-1 flex gap-4 min-h-0">
            {/* Checklist Cards */}
            <div className="w-80 shrink-0 overflow-auto space-y-3">
              {summary.checklists.length === 0 ? (
                <div className="bg-white rounded-xl shadow-sm border border-dashed border-gray-300 p-5 text-center">
                  <div className="text-sm font-semibold text-gray-800">
                    등록된 체크리스트가 없습니다.
                  </div>
                  <p className="text-xs text-gray-500 mt-2 leading-relaxed">
                    우측 상단의 <span className="font-medium text-azure-blue">체크리스트 추가 (YAML)</span> 버튼을 눌러
                    YAML 체크리스트 파일을 업로드하세요.
                  </p>
                </div>
              ) : (
                summary.checklists.map((cl) => (
                  <button
                    key={cl.id}
                    onClick={() => loadDetail(cl.id)}
                    className={`
                      w-full text-left bg-white rounded-xl shadow-sm border p-4 transition-all
                      ${selectedId === cl.id
                        ? 'border-azure-blue ring-2 ring-azure-blue/20'
                        : 'border-gray-200 hover:shadow-md'
                      }
                    `}
                  >
                    <div className="text-sm font-semibold text-gray-800 mb-2">{cl.name}</div>
                    <div className="flex gap-2 flex-wrap mb-2">
                      <span className="text-[11px] bg-indigo-50 text-indigo-700 px-2 py-0.5 rounded-full">
                        점검항목: {cl.total_checks}
                      </span>
                      <span className="text-[11px] bg-green-100 text-green-700 px-2 py-0.5 rounded-full">
                        AUTO: {cl.automated_checks}
                      </span>
                      <span className="text-[11px] bg-orange-100 text-orange-700 px-2 py-0.5 rounded-full">
                        MANUAL: {cl.manual_checks}
                      </span>
                    </div>
                    {cl.applicable_resource_types && cl.applicable_resource_types.length > 0 && (
                      <div className="flex flex-wrap gap-1">
                        {cl.applicable_resource_types.map((rt) => (
                          <span key={rt} className="text-[10px] bg-gray-100 text-gray-600 px-1.5 py-0.5 rounded">
                            {rt.split('/').pop()}
                          </span>
                        ))}
                      </div>
                    )}
                  </button>
                ))
              )}
            </div>

            {/* Detail Panel */}
            <div className="flex-1 bg-white rounded-xl shadow-sm border border-gray-200 overflow-hidden flex flex-col">
              {selectedId && detail && !detailLoading ? (
                <>
                  <div className="px-5 py-3 border-b border-gray-100 bg-gray-50 flex justify-between items-center">
                    <div>
                      <h2 className="text-base font-semibold text-gray-800">{detail.name}</h2>
                      <p className="text-xs text-gray-500 mt-0.5">
                        v{detail.version} — {detail.description}
                      </p>
                    </div>
                    <div className="flex gap-2 flex-wrap justify-end">
                      {!isEditing && (
                        <button
                          type="button"
                          onClick={() => void handleDownloadYaml()}
                          className={DETAIL_HEADER_BTN_DOWNLOAD}
                        >
                          다운로드
                        </button>
                      )}
                      <button
                        type="button"
                        onClick={handleEditToggle}
                        className={`inline-flex items-center justify-center text-[11px] px-3 py-1.5 rounded-md font-medium transition-colors border ${
                          isEditing 
                            ? 'bg-gray-100 text-gray-700 border-gray-300 hover:bg-gray-200' 
                            : 'bg-white text-azure-blue border-azure-blue hover:bg-azure-light'
                        }`}
                      >
                        {isEditing ? '취소' : '편집'}
                      </button>
                      {!isEditing && (
                        <button
                          type="button"
                          onClick={handleDelete}
                          className="inline-flex items-center justify-center text-[11px] px-3 py-1.5 rounded-md font-medium bg-white text-red-600 border border-red-200 hover:bg-red-50 transition-colors"
                        >
                          삭제
                        </button>
                      )}
                      {isEditing && (
                        <button
                          type="button"
                          onClick={handleSave}
                          className="inline-flex items-center justify-center text-[11px] px-3 py-1.5 rounded-md font-medium border border-transparent bg-azure-blue text-white hover:bg-azure-dark transition-colors"
                        >
                          저장
                        </button>
                      )}
                    </div>
                  </div>
                  
                  <div className="flex-1 overflow-auto flex flex-col bg-white">
                    {isEditing ? (
                      <textarea
                        value={editContent}
                        onChange={(e) => setEditContent(e.target.value)}
                        className="flex-1 min-h-[50vh] w-full p-5 font-mono text-sm text-gray-900 bg-white border-t border-gray-200 focus:outline-none focus:ring-2 focus:ring-inset focus:ring-azure-blue/25 resize-y"
                        spellCheck={false}
                      />
                    ) : (
                      <div className="p-5 space-y-4">
                        <div className="flex gap-1 mb-2 flex-wrap">
                          {(detail.applicable_resource_types ?? []).map((rt) => (
                            <span key={rt} className="text-[10px] bg-azure-light text-azure-dark px-2 py-0.5 rounded-full">
                              {rt}
                            </span>
                          ))}
                        </div>
                        {(detail.categories ?? []).map((cat) => (
                          <div key={cat.id}>
                            <h3 className="text-sm font-semibold text-gray-700 mb-2 flex items-center">
                              <span className="bg-azure-blue text-white text-[10px] px-1.5 py-0.5 rounded mr-2">
                                {cat.id}
                              </span>
                              {cat.name}
                            </h3>
                            <div className="space-y-2 ml-2">
                              {cat.items.map((item) => (
                                <div key={item.id} className="border border-gray-100 rounded-lg">
                                  <div className="px-3 py-2 bg-gray-50 text-xs font-medium text-gray-700 rounded-t-lg">
                                    {item.id}. {item.name}
                                  </div>
                                  <div className="divide-y divide-gray-50">
                                    {item.checks.map((check, ci) => (
                                      <div key={ci} className="px-3 py-2.5">
                                        <div className="flex items-start gap-2">
                                          <span className={`
                                            shrink-0 text-[10px] px-1.5 py-0.5 rounded font-medium mt-0.5
                                            ${check.check_type === 'automated'
                                              ? 'bg-green-100 text-green-700'
                                              : 'bg-orange-100 text-orange-700'
                                            }
                                          `}>
                                            {check.check_type === 'automated' ? 'AUTO' : 'MANUAL'}
                                          </span>
                                          <div>
                                            <div className="text-xs text-gray-800">{check.question}</div>
                                            {check.guidance && (
                                              <div className="text-[11px] text-gray-500 mt-1 leading-relaxed">
                                                💡 {check.guidance}
                                              </div>
                                            )}
                                          </div>
                                        </div>
                                      </div>
                                    ))}
                                  </div>
                                </div>
                              ))}
                            </div>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                </>
              ) : detailLoading ? (
                <div className="flex-1 flex items-center justify-center">
                  <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-azure-blue" />
                </div>
              ) : summary.checklists.length === 0 ? (
                <div className="flex-1 flex items-center justify-center px-6">
                  <div className="max-w-md text-center">
                    <div className="text-base font-semibold text-gray-800">
                      체크리스트를 먼저 추가해 주세요.
                    </div>
                    <p className="text-sm text-gray-500 mt-2 leading-relaxed">
                      리소스 평가는 체크리스트가 있어야 실행할 수 있습니다. 우측 상단의
                      <span className="font-medium text-azure-blue"> 체크리스트 추가 (YAML)</span> 버튼으로 YAML 파일을 업로드하세요.
                    </p>
                  </div>
                </div>
              ) : (
                <div className="flex-1 flex items-center justify-center text-sm text-gray-400">
                  왼쪽에서 체크리스트를 선택하세요
                </div>
              )}
            </div>
          </div>
        </>
      )}
    </div>
  );
}
