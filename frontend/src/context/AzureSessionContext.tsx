import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react';
import { authFetch } from '../lib/authRest';
import { normalizeAzureGuid } from '../lib/azureIds';

const STORAGE_KEY = 'aiops.azureSession';
const LEGACY_LOCAL_KEY = 'aiops.azureSession';
const SSO_PROFILE_KEY = 'aiops.ssoProfile';

export interface AzureSubscriptionItem {
  subscription_id: string;
  name: string;
  state: string;
  tenant_id: string;
}

/** sessionStorage `aiops.ssoProfile` — Entra ID 토큰 클레임 기반 */
export interface SsoProfileDisplay {
  name: string;
  email: string;
}

export interface AzureSessionSnapshot {
  tenantId: string | null;
  subscriptionId: string | null;
  subscriptionName: string | null;
  userName: string | null;
  ssoProfile: SsoProfileDisplay | null;
}

interface SessionBootstrapResponse {
  subscription_id: string;
  tenant_id: string;
  name: string;
  state: string;
  user: string;
  /** Entra SSO 프로필 — BE가 ARM access token 클레임에서 추출(콜백이 BE로 옮겨져 FE 콜백 응답이 없으므로) */
  profile?: { name?: string; email?: string; oid?: string };
}

interface AzureSessionContextValue extends AzureSessionSnapshot {
  /** true: /api/azure/session-bootstrap 1회 요청이 끝남(성공·실패). 대시보드 데이터 fetch는 이후에만 하면 이중 호출을 줄일 수 있음 */
  azureBootstrapComplete: boolean;
  /**
   * 세션 구독이 교집합 밖일 때 선택을 유도함. 교집합 목록이 비면(설정 불가)·API 실패 시 false.
   */
  needsSubscriptionIntersectionPick: boolean;
  setSelection: (tenantId: string, subscriptionId: string, subscriptionName?: string) => void;
  clearSelection: () => void;
  chatAzureHeaders: { tenantId: string; subscriptionId: string; subscriptionName?: string } | null;
}

interface SubscriptionsIntersectionResponse {
  subscriptions: AzureSubscriptionItem[];
}

const emptySnapshot: AzureSessionSnapshot = {
  tenantId: null,
  subscriptionId: null,
  subscriptionName: null,
  userName: null,
  ssoProfile: null,
};

function readSsoProfileFromStorage(): SsoProfileDisplay | null {
  try {
    const raw = sessionStorage.getItem(SSO_PROFILE_KEY);
    if (!raw) return null;
    const o = JSON.parse(raw) as Record<string, unknown>;
    const email = typeof o.email === 'string' ? o.email.trim() : '';
    const name = typeof o.name === 'string' ? o.name.trim() : '';
    if (!name && !email) return null;
    return { name, email };
  } catch {
    return null;
  }
}

function readSsoProfileUserName(): string | null {
  const p = readSsoProfileFromStorage();
  if (!p) return null;
  return p.email || p.name || null;
}

function readPersisted(): AzureSessionSnapshot {
  try {
    let raw = sessionStorage.getItem(STORAGE_KEY);
    if (!raw) {
      const legacy = localStorage.getItem(LEGACY_LOCAL_KEY);
      if (legacy) {
        sessionStorage.setItem(STORAGE_KEY, legacy);
        localStorage.removeItem(LEGACY_LOCAL_KEY);
        raw = legacy;
      }
    }
    if (!raw) {
      const sso = readSsoProfileFromStorage();
      const ssoUser = readSsoProfileUserName();
      return ssoUser ? { ...emptySnapshot, userName: ssoUser, ssoProfile: sso } : { ...emptySnapshot };
    }
    const o = JSON.parse(raw) as Record<string, unknown>;
    const sso = readSsoProfileFromStorage();
    const ssoUser = readSsoProfileUserName();
    return {
      tenantId: typeof o.tenantId === 'string' ? o.tenantId : null,
      subscriptionId: typeof o.subscriptionId === 'string' ? o.subscriptionId : null,
      subscriptionName: typeof o.subscriptionName === 'string' ? o.subscriptionName : null,
      userName:
        typeof o.userName === 'string' && o.userName.trim()
          ? o.userName
          : ssoUser,
      ssoProfile: sso,
    };
  } catch {
    return { ...emptySnapshot };
  }
}

function writePersisted(s: AzureSessionSnapshot) {
  try {
    const { ssoProfile: _p, ...persistable } = s;
    sessionStorage.setItem(STORAGE_KEY, JSON.stringify(persistable));
  } catch {
    /* ignore */
  }
}

const AzureSessionContext = createContext<AzureSessionContextValue | null>(null);

export function AzureSessionProvider({ children }: { children: ReactNode }) {
  const [snapshot, setSnapshot] = useState<AzureSessionSnapshot>(() => readPersisted());
  const [azureBootstrapComplete, setAzureBootstrapComplete] = useState(false);
  const [needsSubscriptionIntersectionPick, setNeedsSubscriptionIntersectionPick] = useState(false);

  useEffect(() => {
    let cancelled = false;

    authFetch('/api/azure/session-bootstrap')

      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(String(r.status)))))
      .then((d: SessionBootstrapResponse) => {
        if (cancelled) return;
        // 콜백이 BE로 옮겨져 FE가 프로필 응답을 직접 받지 못하므로 bootstrap이 단일 소스.
        // 다음 로드 깜빡임 방지를 위해 sessionStorage 에도 기록.
        const name = d.profile?.name?.trim() ?? '';
        const email = d.profile?.email?.trim() ?? '';
        const profile = name || email ? { name, email } : readSsoProfileFromStorage();
        if (profile) {
          try {
            sessionStorage.setItem(SSO_PROFILE_KEY, JSON.stringify(profile));
          } catch {
            /* ignore */
          }
        }
        setSnapshot((prev) => {
          const next: AzureSessionSnapshot = {
            ...prev,
            userName: d.user || prev.userName,
            ssoProfile: profile,
          };
          if (!prev.subscriptionId && d.subscription_id && d.tenant_id) {
            next.tenantId = d.tenant_id;
            next.subscriptionId = d.subscription_id;
            next.subscriptionName = d.name || null;
          } else if (prev.subscriptionId === d.subscription_id && d.subscription_id) {
            next.subscriptionName = d.name || prev.subscriptionName;
          }
          writePersisted(next);
          return next;
        });
      })
      .catch(() => {})
      .finally(() => {
        if (!cancelled) setAzureBootstrapComplete(true);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (!azureBootstrapComplete) return;
    const subId = snapshot.subscriptionId?.trim();
    const tenId = snapshot.tenantId?.trim();
    if (!subId || !tenId) {
      setNeedsSubscriptionIntersectionPick(false);
      return;
    }
    let cancelled = false;
    authFetch('/api/azure/subscriptions')
      .then((r) => (r.ok ? r.json() : Promise.reject()))
      .then((data: SubscriptionsIntersectionResponse) => {
        if (cancelled) return;
        const ns = normalizeAzureGuid(subId);
        const nt = normalizeAzureGuid(tenId);
        const inList = data.subscriptions.some(
          (s) =>
            normalizeAzureGuid(s.subscription_id) === ns &&
            normalizeAzureGuid(s.tenant_id) === nt,
        );
        if (!inList && data.subscriptions.length === 0) {
          setNeedsSubscriptionIntersectionPick(false);
          return;
        }
        setNeedsSubscriptionIntersectionPick(!inList);
      })
      .catch(() => {
        if (!cancelled) setNeedsSubscriptionIntersectionPick(false);
      });
    return () => {
      cancelled = true;
    };
  }, [azureBootstrapComplete, snapshot.subscriptionId, snapshot.tenantId]);

  const setSelection = useCallback(
    (tenantId: string, subscriptionId: string, subscriptionName?: string) => {
      const disk = readPersisted();
      const merged: AzureSessionSnapshot = {
        ...disk,
        tenantId,
        subscriptionId,
        subscriptionName: subscriptionName ?? disk.subscriptionName,
      };
      writePersisted(merged);
      setSnapshot((prev) => ({
        ...prev,
        tenantId,
        subscriptionId,
        subscriptionName: subscriptionName ?? prev.subscriptionName,
      }));
    },
    [],
  );

  const clearSelection = useCallback(() => {
    setSnapshot({ ...emptySnapshot });
    try {
      sessionStorage.removeItem(STORAGE_KEY);
      localStorage.removeItem(LEGACY_LOCAL_KEY);
    } catch {
      /* ignore */
    }
  }, []);

  const chatAzureHeaders = useMemo(() => {
    if (snapshot.tenantId && snapshot.subscriptionId) {
      return {
        tenantId: snapshot.tenantId,
        subscriptionId: snapshot.subscriptionId,
        subscriptionName: snapshot.subscriptionName || undefined,
      };
    }
    return null;
  }, [snapshot.tenantId, snapshot.subscriptionId, snapshot.subscriptionName]);

  const value = useMemo(
    () => ({
      tenantId: snapshot.tenantId,
      subscriptionId: snapshot.subscriptionId,
      subscriptionName: snapshot.subscriptionName,
      userName: snapshot.userName,
      ssoProfile: snapshot.ssoProfile,
      azureBootstrapComplete,
      needsSubscriptionIntersectionPick,
      setSelection,
      clearSelection,
      chatAzureHeaders,
    }),
    [
      snapshot,
      azureBootstrapComplete,
      needsSubscriptionIntersectionPick,
      setSelection,
      clearSelection,
      chatAzureHeaders,
    ],
  );

  return <AzureSessionContext.Provider value={value}>{children}</AzureSessionContext.Provider>;
}

export function useAzureSession() {
  const ctx = useContext(AzureSessionContext);
  if (!ctx) {
    throw new Error('useAzureSession must be used within AzureSessionProvider');
  }
  return ctx;
}
