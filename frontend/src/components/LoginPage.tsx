import { useEffect, useState } from 'react';
import microsoftLogo from '../resources/images/microsoft.png';

/** OAuth 콜백 실패 시 BE가 `/login?error=...` 로 리다이렉트하므로 메시지로 변환. */
function loginErrorMessage(code: string): string {
  switch (code) {
    case 'invalid_state':
      return '로그인 상태(state)가 유효하지 않거나 만료되었습니다. 다시 시도해 주세요.';
    case 'auth_failed':
      return '인증에 실패했습니다. 다시 로그인해 주세요.';
    case 'sso_not_configured':
      return 'SSO가 설정되지 않았습니다. 관리자에게 문의하세요.';
    default:
      return decodeURIComponent(code.replace(/\+/g, ' ')) || '로그인이 거부되었습니다.';
  }
}

/**
 * Entra ID SSO 로그인 화면 — 제목·카드는 유지, 사용자가 조작하는 컨트롤은 로그인 버튼만 (docs/plan/sso.md).
 * 로그인 버튼은 BE 엔드포인트 `/api/auth/login` 으로 top-level 이동 → BE가 Entra로 302 redirect.
 */
export function LoginPage() {
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    const code = new URLSearchParams(window.location.search).get('error');
    if (code) setErr(loginErrorMessage(code));
  }, []);

  const handleLogin = () => {
    setLoading(true);
    setErr(null);
    window.location.href = '/api/auth/login';
  };

  return (
    <div className="flex min-h-screen items-center justify-center bg-slate-100">
      <div className="flex w-full max-w-sm flex-col gap-6 rounded-xl bg-white px-10 py-12 shadow-md">
        <img
          src={microsoftLogo}
          alt="Microsoft"
          className="h-auto w-full max-h-28 object-contain object-center select-none"
        />
        <h1 className="text-center text-lg font-semibold text-slate-800">
          AIOps Resource Assessment
        </h1>
        <button
          type="button"
          onClick={handleLogin}
          disabled={loading}
          className="w-full rounded-md bg-[#0078d4] px-6 py-3 text-sm font-medium text-white shadow hover:bg-[#106ebe] disabled:opacity-60"
        >
          {loading ? '이동 중…' : 'Microsoft 계정으로 로그인'}
        </button>
        {err ? (
          <p className="max-w-sm text-center text-sm text-red-600" role="alert">
            {err}
          </p>
        ) : null}
      </div>
    </div>
  );
}
