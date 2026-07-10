/** 대시보드용 fetch: HTTP 오류·비배열 JSON이 state를 깨지 않게 함 + Abort 지원 */

import { mergeAuthInit } from './authRest';

export function isAbortError(e: unknown): boolean {
  return e instanceof DOMException && e.name === 'AbortError';
}

export async function fetchJsonArray(
  url: string,
  init?: RequestInit,
): Promise<unknown[]> {

  const r = await fetch(url, mergeAuthInit(init));

  if (!r.ok) {
    throw new Error(`HTTP ${r.status}`);
  }
  const data = await r.json();
  return Array.isArray(data) ? data : [];
}

export interface ChecklistSummaryShape {
  total_checklists: number;
  total_checks: number;
  automated_checks: number;
  manual_checks: number;
}

export async function fetchChecklistSummary(
  url: string,
  init?: RequestInit,
): Promise<ChecklistSummaryShape | null> {

  const r = await fetch(url, mergeAuthInit(init));

  if (!r.ok) {
    throw new Error(`HTTP ${r.status}`);
  }
  const data = (await r.json()) as Record<string, unknown>;
  if (
    typeof data.total_checklists === 'number' &&
    typeof data.total_checks === 'number'
  ) {
    return data as unknown as ChecklistSummaryShape;
  }
  return null;
}
