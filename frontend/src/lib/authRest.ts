/**
 * SSO 액세스 토큰은 HttpOnly 쿠키로만 전달된다. REST 호출은 credentials: 'include'로 쿠키를 붙인다.
 * FE 정적 파일과 /api/* 프록시를 nginx가 동일 오리진으로 제공하므로 경로 변환 없이 상대 경로 그대로 호출한다.
 */

export function mergeAuthInit(init?: RequestInit): RequestInit {
  return {
    ...init,
    credentials: init?.credentials ?? 'include',
  };
}

export function authFetch(input: RequestInfo | URL, init?: RequestInit): Promise<Response> {
  return fetch(input, mergeAuthInit(init));
}
