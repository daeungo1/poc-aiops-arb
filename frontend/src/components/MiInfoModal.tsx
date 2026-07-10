import { useEffect, useState } from 'react';
import { authFetch } from '../lib/authRest';

interface MiInfoModalProps {
  open: boolean;
  onClose: () => void;
}

interface MiInfoResponse {
  resource_name: string;
  subscription_id: string;
  subscription_name: string;
  object_id: string;
  uami_client_id?: string;
  uami_object_id?: string;
  uami_resource_id?: string;
  uami_name?: string;
}

export function MiInfoModal({ open, onClose }: MiInfoModalProps) {
  const [data, setData] = useState<MiInfoResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [copiedKey, setCopiedKey] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    setLoading(true);
    setError(null);
    setCopiedKey(null);
    authFetch('/api/system/mi-info')
      .then(async (r) => {
        if (!r.ok) {
          const j = await r.json().catch(() => ({}));
          throw new Error((j as { detail?: string }).detail || r.statusText);
        }
        return (await r.json()) as MiInfoResponse;
      })
      .then(setData)
      .catch((e: Error) => setError(e.message))
      .finally(() => setLoading(false));
  }, [open]);

  const handleCopy = async (key: string, value: string) => {
    if (!value) return;
    try {
      await navigator.clipboard.writeText(value);
      setCopiedKey(key);
      window.setTimeout(() => setCopiedKey((curr) => (curr === key ? null : curr)), 1500);
    } catch {
      /* 클립보드 접근 거부 시 무시 */
    }
  };

  if (!open) return null;

  return (
    <>
      <button
        type="button"
        aria-label="Close MI info"
        className="fixed inset-0 bg-black/50 z-[55]"
        onClick={onClose}
      />

      <div
        role="dialog"
        aria-modal="true"
        className="fixed top-1/2 left-1/2 z-[60] -translate-x-1/2 -translate-y-1/2 w-[min(92vw,30rem)] bg-azure-dark text-white border border-white/10 rounded-xl shadow-2xl flex flex-col"
      >
        <div className="p-4 border-b border-white/10 flex items-start justify-between gap-3">
          <div>
            <h2 className="text-base font-bold text-white">Backend MI 정보</h2>
            <p className="text-xs text-blue-200/90 mt-1 leading-relaxed">
              리소스 조회·평가에 사용하는 Managed Identity 환경변수 값입니다.
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="p-2 rounded-lg text-blue-200 hover:bg-white/10 hover:text-white shrink-0"
            aria-label="닫기"
          >
            ✕
          </button>
        </div>

        <div className="p-4 space-y-3 max-h-[70vh] overflow-y-auto">
          {loading && <p className="text-xs text-blue-200/80">불러오는 중…</p>}
          {error && (
            <p className="text-xs text-red-300 bg-red-500/10 border border-red-400/30 rounded-lg p-3">
              MI 정보를 불러오지 못했습니다: {error}
            </p>
          )}
          {data && (
            <>
              <MiInfoRow
                label="name"
                value={data.uami_name ?? data.resource_name}
                copyKey="uami_name"
                copiedKey={copiedKey}
                onCopy={handleCopy}
                highlight
              />
              <MiInfoRow
                label="resource id"
                value={data.uami_resource_id ?? ''}
                copyKey="uami_resource_id"
                copiedKey={copiedKey}
                onCopy={handleCopy}
                mono
              />
              <MiInfoRow
                label="client id"
                value={data.uami_client_id ?? ''}
                copyKey="uami_client_id"
                copiedKey={copiedKey}
                onCopy={handleCopy}
                mono
              />
              <MiInfoRow
                label="object id"
                value={data.uami_object_id ?? data.object_id}
                copyKey="uami_object_id"
                copiedKey={copiedKey}
                onCopy={handleCopy}
                mono
              />
            </>
          )}
        </div>
      </div>
    </>
  );
}

interface MiInfoRowProps {
  label: string;
  value: string;
  copyKey: string;
  copiedKey: string | null;
  onCopy: (key: string, value: string) => void;
  mono?: boolean;
  highlight?: boolean;
}

function MiInfoRow({ label, value, copyKey, copiedKey, onCopy, mono, highlight }: MiInfoRowProps) {
  const displayValue = value?.trim() || '(없음)';
  const isCopied = copiedKey === copyKey;
  const isEmpty = !value?.trim();
  return (
    <div
      className={`rounded-lg border p-3 ${
        highlight
          ? 'border-azure-blue/60 bg-azure-blue/10'
          : 'border-white/10 bg-white/5'
      }`}
    >
      <div className="flex items-center justify-between gap-2">
        <span className="text-[11px] uppercase tracking-wide text-blue-200/80">{label}</span>
        <button
          type="button"
          disabled={isEmpty}
          onClick={() => onCopy(copyKey, value)}
          className="text-[11px] px-2 py-1 rounded border border-white/15 text-white/90 hover:bg-white/10 disabled:opacity-40 disabled:cursor-not-allowed"
        >
          {isCopied ? '복사됨' : '복사'}
        </button>
      </div>
      <p
        className={`mt-1.5 text-sm break-all ${mono ? 'font-mono' : ''} ${
          isEmpty ? 'text-blue-200/50' : 'text-white'
        }`}
      >
        {displayValue}
      </p>
    </div>
  );
}
