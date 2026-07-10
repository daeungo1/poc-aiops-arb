/** ARM/Entra 구독·테넌트 GUID 비교용 (중괄호·공백·대소문자 무시) */
export function normalizeAzureGuid(value: string | null | undefined): string {
  if (value == null) return '';
  return value.trim().replace(/[{}]/g, '').toLowerCase();
}
