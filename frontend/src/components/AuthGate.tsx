import { useEffect, useState, type ReactNode } from 'react';
import { authFetch } from '../lib/authRest';

type SessionPayload = {
  authenticated?: boolean;
  sso_enforced?: boolean;
};

/**
 * 세션 확인 후 미인증이면 /login 으로 리다이렉트한다.
 * - authenticated: false  → /login
 * - 세션 API 오류(백엔드 미응답 등) → /login
 */
export function AuthGate({ children }: { children: ReactNode }) {
  const [ready, setReady] = useState(false);

  useEffect(() => {
    let cancelled = false;

    authFetch('/api/auth/session')

      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(String(r.status)))))
      .then((d: SessionPayload) => {
        if (cancelled) return;
        if (!d.authenticated) {
          window.location.replace('/login');
          return;
        }
        setReady(true);
      })
      .catch(() => {
        if (!cancelled) window.location.replace('/login');
      });
    return () => {
      cancelled = true;
    };
  }, []);

  if (!ready) {
    return (
      <div className="flex h-screen items-center justify-center bg-slate-100 text-sm text-slate-500">
        세션 확인 중…
      </div>
    );
  }

  return <>{children}</>;
}
