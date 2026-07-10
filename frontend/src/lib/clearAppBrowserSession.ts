/** 앱 로그아웃 시 브라우저에만 있는 세션 데이터 제거 (SSO 프로필·구독 선택 등). 액세스 토큰은 BE가 HttpOnly 쿠키를 삭제. */

const AZURE_SESSION_KEY = 'aiops.azureSession';
const SSO_PROFILE_KEY = 'aiops.ssoProfile';

export function clearAppBrowserSession(): void {
  try {
    sessionStorage.removeItem(AZURE_SESSION_KEY);
    sessionStorage.removeItem(SSO_PROFILE_KEY);
    localStorage.removeItem(AZURE_SESSION_KEY);
  } catch {
    /* ignore */
  }
}
