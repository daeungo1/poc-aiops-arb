import { useCallback, useState } from 'react';
import type { NavigateTo, PageId } from '../App';
import { useAzureSession } from '../context/AzureSessionContext';
import { useAssessmentRun } from '../context/AssessmentRunContext';
import { useTerraformRun } from '../context/TerraformRunContext';
import { clearAppBrowserSession } from '../lib/clearAppBrowserSession';
import { authFetch } from '../lib/authRest';
import { MiInfoModal } from './MiInfoModal';

const navItems: { id: PageId; label: string; icon: string; desc: string }[] = [
  { id: 'dashboard', label: '대시보드', icon: '📊', desc: '전체 현황' },
  { id: 'assessments', label: '진단 평가', icon: '🔬', desc: '리소스 평가 리포트' },
  { id: 'checklists', label: '체크리스트', icon: '📝', desc: '평가 기준 목록' },
  { id: 'terraform', label: 'Terraform', icon: '🏗️', desc: '자동 생성 코드' },
];

interface SidebarProps {
  currentPage: PageId;
  onNavigate: NavigateTo;
  onOpenSubscriptionPicker?: () => void;
}

export function Sidebar({ currentPage, onNavigate, onOpenSubscriptionPicker }: SidebarProps) {
  const { userName, subscriptionName, subscriptionId, ssoProfile, clearSelection } = useAzureSession();
  const { isRunning: isAssessmentRunning } = useAssessmentRun();
  const { isRunning: isTerraformRunning } = useTerraformRun();
  const [miInfoOpen, setMiInfoOpen] = useState(false);

  const handleAppLogout = useCallback(async () => {
    try {
      await authFetch('/api/auth/logout', { method: 'POST' });
    } catch {
      /* ignore — 로컬 세션은 항상 정리 */
    }
    clearSelection();
    clearAppBrowserSession();
    window.location.href = '/';
  }, [clearSelection]);

  const subscriptionLine =
    subscriptionName?.trim() ||
    (subscriptionId ? `${subscriptionId.slice(0, 8)}…` : '구독을 선택하세요');

  return (
    <aside className="w-64 h-screen bg-azure-dark text-white flex flex-col shrink-0">
      {/* Logo / Header */}
      <div className="p-5 border-b border-white/10">
        <div className="flex items-center space-x-3">
          <div className="w-10 h-10 bg-azure-blue rounded-lg flex items-center justify-center text-xl">
            🤖
          </div>
          <div>
            <h1 className="text-base font-bold leading-tight">AIOps</h1>
            <p className="text-xs text-blue-200 leading-tight">Resource Assessment</p>
          </div>
        </div>
      </div>

      {/* Navigation */}
      <nav className="flex-1 py-4">
        <ul className="space-y-1 px-3">
          {navItems.map((item) => {
            const showRunning =
              (item.id === 'assessments' && isAssessmentRunning) ||
              (item.id === 'terraform' && isTerraformRunning);
            return (
              <li key={item.id}>
                <button
                  onClick={() => onNavigate(item.id)}
                  className={`
                    w-full flex items-center space-x-3 px-3 py-2.5 rounded-lg text-sm transition-colors
                    ${currentPage === item.id
                      ? 'bg-azure-blue text-white'
                      : 'text-blue-100 hover:bg-white/10'
                    }
                  `}
                >
                  <span className="text-lg">{item.icon}</span>
                  <div className="text-left flex-1 min-w-0">
                    <div className="font-medium flex items-center gap-1.5">
                      {item.label}
                      {showRunning && (
                        <span className="inline-flex items-center gap-1">
                          <span className="w-2 h-2 rounded-full bg-yellow-400 animate-pulse shrink-0" />
                          <span className="text-[10px] font-normal text-yellow-300 leading-none">실행 중</span>
                        </span>
                      )}
                    </div>
                    <div className="text-[11px] opacity-70">{item.desc}</div>
                  </div>
                </button>
              </li>
            );
          })}
        </ul>
      </nav>

      {/* Footer — 프로필 영역 + 구독 선택 */}
      <div className="p-4 border-t border-white/10 space-y-3 mt-auto">
        <div className="flex items-center gap-3 p-2 rounded-lg bg-white/5">
          <div className="w-9 h-9 rounded-full bg-azure-blue/40 flex items-center justify-center text-lg">
            👤
          </div>
          <div className="min-w-0 flex-1 space-y-0.5">
            {ssoProfile ? (
              <>
                <p className="text-sm font-medium text-white truncate" title={ssoProfile.name || undefined}>
                  {ssoProfile.name.trim() || '—'}
                </p>
                <p
                  className="text-xs text-blue-100/90 truncate"
                  title={ssoProfile.email || undefined}
                >
                  {ssoProfile.email.trim() || '—'}
                </p>
                <p
                  className="text-[10px] text-blue-200/80 truncate pt-0.5"
                  title={subscriptionId ?? undefined}
                >
                  {subscriptionLine}
                </p>
              </>
            ) : (
              <>
                <p className="text-sm font-medium text-white truncate">
                  {userName?.trim() || 'Azure CLI'}
                </p>
                <p className="text-[10px] text-blue-200/80 truncate" title={subscriptionId ?? undefined}>
                  {subscriptionLine}
                </p>
              </>
            )}
          </div>
        </div>
        {onOpenSubscriptionPicker && (
          <button
            type="button"
            onClick={onOpenSubscriptionPicker}
            className="w-full flex items-center justify-center gap-2 px-3 py-2.5 rounded-lg text-xs font-semibold bg-white/10 border border-white/15 text-white hover:bg-azure-blue hover:border-azure-blue transition-colors"
          >
            <span aria-hidden>⇄</span>
            Subscription ID 변경
          </button>
        )}
        <button
          type="button"
          onClick={() => setMiInfoOpen(true)}
          className="w-full flex items-center justify-center gap-2 px-3 py-2.5 rounded-lg text-xs font-semibold bg-white/10 border border-white/15 text-white hover:bg-azure-blue hover:border-azure-blue transition-colors"
        >
          <span aria-hidden>🔑</span>
          Backend MI 정보
        </button>
        <button
          type="button"
          onClick={handleAppLogout}
          className="w-full rounded-lg px-3 py-2 text-xs font-medium text-white/90 bg-white/10 border border-white/15 hover:bg-red-600/40 hover:border-red-400/40 transition-colors"
        >
          로그아웃
        </button>
        <div className="text-xs text-blue-200 space-y-1 pt-1 border-t border-white/5">
          <div className="flex items-center space-x-2">
            <span className="w-2 h-2 bg-green-400 rounded-full animate-pulse" />
            <span>AG-UI 서버 연결됨</span>
          </div>
          <div className="text-[10px] opacity-60">Azure Architecture Review Board</div>
        </div>
      </div>
      <MiInfoModal open={miInfoOpen} onClose={() => setMiInfoOpen(false)} />
    </aside>
  );
}
