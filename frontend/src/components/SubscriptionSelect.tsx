import { ChevronDown } from 'lucide-react';
import type { AzureSubscriptionItem } from '../context/AzureSessionContext';

/** 구독(대분류) — RG/유형보다 상위 범위 */
export function SubscriptionSelect({
  label,
  subscriptions,
  tenantId,
  subscriptionId,
  onChange,
}: {
  label: string;
  subscriptions: AzureSubscriptionItem[];
  tenantId: string | null;
  subscriptionId: string | null;
  onChange: (sub: AzureSubscriptionItem) => void;
}) {
  const match = (s: AzureSubscriptionItem) =>
    s.subscription_id === subscriptionId && (!tenantId || s.tenant_id === tenantId);
  const value = subscriptionId && subscriptions.some(match) ? subscriptionId : '';

  return (
    <div className="flex flex-col gap-1 shrink-0">
      <span className="text-xs font-medium text-slate-500 pl-0.5">{label}</span>
      <div className="flex bg-white border border-slate-200 rounded-lg overflow-hidden hover:border-blue-400 transition-colors focus-within:border-blue-500 focus-within:ring-2 focus-within:ring-blue-100">
        <select
          className="bg-transparent py-2 pl-3 pr-8 text-sm outline-none text-slate-700 font-medium appearance-none min-w-[150px] cursor-pointer"
          value={value}
          onChange={(e) => {
            const sub = subscriptions.find((s) => s.subscription_id === e.target.value);
            if (sub) onChange(sub);
          }}
        >
          <option value="" disabled className="text-slate-400">
            {label}
          </option>
          {subscriptions.map((sub) => (
            <option key={`${sub.tenant_id}-${sub.subscription_id}`} value={sub.subscription_id}>
              {sub.name?.trim() || sub.subscription_id}
            </option>
          ))}
        </select>
        <div className="flex items-center pr-2 pointer-events-none -ml-6">
          <ChevronDown size={14} className="text-slate-400" />
        </div>
      </div>
    </div>
  );
}
