/** REST 호출에 UI에서 선택한 Azure 테넌트·구독을 전달할 때 사용. */
export function azureScopeHeaders(
  tenantId: string | null,
  subscriptionId: string | null,
  subscriptionName?: string | null,
): Record<string, string> {
  if (!tenantId || !subscriptionId) return {};
  const headers: Record<string, string> = {
    'X-Azure-Tenant-Id': tenantId,
    'X-Azure-Subscription-Id': subscriptionId,
  };
  if (subscriptionName?.trim()) {
    headers['X-Azure-Subscription-Name'] = encodeURIComponent(subscriptionName.trim());
  }
  return headers;
}
