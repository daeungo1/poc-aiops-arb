import { useEffect, useState } from 'react';
import { useAzureSession, type AzureSubscriptionItem } from '../context/AzureSessionContext';
import { authFetch } from '../lib/authRest';

interface SubscriptionPickerDrawerProps {
  open: boolean;
  onClose: () => void;
  /** 교집합 밖 선택 시 목록을 고를 때까지 오버레이·닫기 버튼으로 닫지 않음 */
  preventBackdropClose?: boolean;
}

interface SubscriptionsResponse {
  tenant_id: string;
  default_subscription_id?: string;
  subscriptions: AzureSubscriptionItem[];
}

export function SubscriptionPickerDrawer({
  open,
  onClose,
  preventBackdropClose = false,
}: SubscriptionPickerDrawerProps) {
  const { setSelection, tenantId, subscriptionId, subscriptionName } = useAzureSession();
  const [data, setData] = useState<SubscriptionsResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const selectedSubscriptionName =
    data?.subscriptions.find((sub) => sub.subscription_id === subscriptionId && sub.tenant_id === tenantId)?.name.trim() ||
    subscriptionName?.trim() ||
    subscriptionId;

  useEffect(() => {
    if (!open) return;
    setLoading(true);
    setError(null);
    authFetch('/api/azure/subscriptions')
      .then(async (r) => {
        if (!r.ok) {
          const j = await r.json().catch(() => ({}));
          throw new Error((j as { detail?: string }).detail || r.statusText);
        }
        return (await r.json()) as SubscriptionsResponse;
      })
      .then(setData)
      .catch((e: Error) => setError(e.message))
      .finally(() => setLoading(false));
  }, [open]);

  const blockDismiss =
    preventBackdropClose &&
    !loading &&
    !error &&
    !!data &&
    data.subscriptions.length > 0;

  const maybeClose = () => {
    if (blockDismiss) return;
    onClose();
  };

  const handlePick = (sub: AzureSubscriptionItem) => {
    setSelection(sub.tenant_id, sub.subscription_id, sub.name);
    onClose();
  };

  return (
    <>
      <button
        type="button"
        aria-label="Close subscription picker"
        className={`fixed inset-0 bg-black/50 z-[55] transition-opacity duration-300 ${
          open ? 'opacity-100 pointer-events-auto' : 'opacity-0 pointer-events-none'
        }`}
        onClick={maybeClose}
      />

      <aside
        className={`fixed top-0 right-0 h-full w-[min(100vw,22rem)] z-[60] bg-azure-dark text-white border-l border-white/10 shadow-[-8px_0_32px_rgba(0,0,0,0.35)] flex flex-col transition-transform duration-300 ease-out ${
          open ? 'translate-x-0' : 'translate-x-full'
        }`}
      >
        <div className="p-4 border-b border-white/10 flex items-start justify-between gap-3 shrink-0">
          <div>
            <h2 className="text-base font-bold text-white">Subscription</h2>
            <p className="text-xs text-blue-200/90 mt-1 leading-relaxed">
              테넌트에 속한 구독을 선택하면 채팅·리소스 조회에 적용됩니다.
            </p>
          </div>
          <button
            type="button"
            onClick={maybeClose}
            className="p-2 rounded-lg text-blue-200 hover:bg-white/10 hover:text-white shrink-0"
            aria-label="닫기"
          >
            ✕
          </button>
        </div>

        <div className="flex-1 overflow-y-auto p-3 space-y-2">
          {blockDismiss && (
            <div className="rounded-lg border border-amber-400/45 bg-amber-500/15 px-3 py-2 text-xs text-amber-50 leading-relaxed">
              현재 구독은 백엔드 Resource Reader(UAMI 포함) 허용 범위와 겹치지 않습니다.
              아래 목록에서 사용할 구독을 선택하세요.
            </div>
          )}
          {subscriptionId && tenantId && (
            <div className="rounded-lg bg-gradient-to-r from-azure-blue/35 to-cyan-400/10 border border-cyan-300/35 px-3 py-2 text-xs shadow-[0_0_18px_rgba(0,120,212,0.18)]">
              <p className="text-[10px] uppercase tracking-wide text-cyan-100 font-semibold mb-1">선택됨</p>
              <p className="text-sm font-semibold truncate text-white" title={selectedSubscriptionName ?? undefined}>
                {selectedSubscriptionName}
              </p>
            </div>
          )}

          {loading && <p className="text-sm text-blue-200 px-2 py-3 text-center">불러오는 중…</p>}
          {error && (
            <p className="text-sm text-red-300 px-2 py-2 rounded-lg bg-red-500/15 border border-red-400/30">{error}</p>
          )}
          {!loading && !error && data && data.subscriptions.length === 0 && (
            <p className="text-sm text-blue-200 px-2">사용 가능한 구독이 없습니다.</p>
          )}
          {!loading &&
            !error &&
            data?.subscriptions
              .filter((sub) => !(sub.subscription_id === subscriptionId && sub.tenant_id === tenantId))
              .map((sub) => (
                <button
                  key={sub.subscription_id}
                  type="button"
                  onClick={() => handlePick(sub)}
                  className="w-full text-left px-3 py-2.5 rounded-lg border border-white/10 hover:bg-white/5 text-white transition-colors"
                >
                  <p className="font-semibold text-sm truncate">{sub.name}</p>
                  <p className="font-mono text-[10px] text-blue-200/80 mt-1 break-all">{sub.subscription_id}</p>
                  <p className="text-[10px] text-blue-200/60 mt-0.5">{sub.state}</p>
                </button>
              ))}
        </div>
      </aside>
    </>
  );
}
