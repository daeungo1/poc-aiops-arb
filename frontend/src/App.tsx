import { useState, useTransition, useCallback, useEffect } from 'react';
import { Sidebar } from './components/Sidebar';
import { LoginPage } from './components/LoginPage';
import { DashboardPage } from './components/DashboardPage';
import { AssessmentBoard } from './components/AssessmentBoard';
import { ChecklistBoard } from './components/ChecklistBoard';
import { TerraformBoard } from './components/TerraformBoard';
import { ChatSidebar } from './components/ChatSidebar';
import { SubscriptionPickerDrawer } from './components/SubscriptionPickerDrawer';
import { AzureSessionProvider, useAzureSession } from './context/AzureSessionContext';
import { AssessmentRunProvider } from './context/AssessmentRunContext';
import { TerraformRunProvider } from './context/TerraformRunContext';
import { AuthGate } from './components/AuthGate';

export type PageId = 'dashboard' | 'assessments' | 'checklists' | 'terraform';

/** 대시보드 등에서 페이지 이동 시 목록에서 미리 선택할 항목 */
export type NavigateOptions = {
  selectAssessmentFilename?: string | null;
  initialAssessmentTab?: 'charts' | 'diagnosis' | 'results';
  selectTerraform?: {
    subscription_id: string;
    timestamp: string;
    filename?: string;
    run_id?: number;
  } | null;
};

export type NavigateTo = (page: PageId, options?: NavigateOptions) => void;

function AppShell() {
  const { needsSubscriptionIntersectionPick } = useAzureSession();
  const [currentPage, setCurrentPage] = useState<PageId>('dashboard');
  const [subscriptionDrawerOpen, setSubscriptionDrawerOpen] = useState(false);
  const [pendingAssessmentFilename, setPendingAssessmentFilename] = useState<string | null>(null);
  const [pendingAssessmentTab, setPendingAssessmentTab] = useState<NavigateOptions['initialAssessmentTab']>(undefined);
  const [pendingTerraformRun, setPendingTerraformRun] = useState<NavigateOptions['selectTerraform']>(null);
  const [, startTransition] = useTransition();

  useEffect(() => {
    if (needsSubscriptionIntersectionPick) {
      setSubscriptionDrawerOpen(true);
    }
  }, [needsSubscriptionIntersectionPick]);

  // Wrap page navigation in startTransition so React can interrupt
  // heavy chat-sidebar re-renders and switch pages immediately.
  const navigateTo = useCallback<NavigateTo>((page, options) => {
    startTransition(() => {
      setCurrentPage(page);
      if (page === 'assessments') {
        setPendingAssessmentFilename(options?.selectAssessmentFilename ?? null);
        setPendingAssessmentTab(options?.initialAssessmentTab);
      } else {
        setPendingAssessmentFilename(null);
        setPendingAssessmentTab(undefined);
      }
      if (page === 'terraform') {
        setPendingTerraformRun(options?.selectTerraform ?? null);
      } else {
        setPendingTerraformRun(null);
      }
    });
  }, [startTransition]);

  const clearPendingAssessment = useCallback(() => {
    setPendingAssessmentFilename(null);
  }, []);
  const clearPendingTerraform = useCallback(() => {
    setPendingTerraformRun(null);
  }, []);

  const renderPage = () => {
    switch (currentPage) {
      case 'dashboard':
        return <DashboardPage onNavigate={navigateTo} />;
      case 'assessments':
        return (
          <AssessmentBoard
            initialFilename={pendingAssessmentFilename}
            onInitialFilenameConsumed={clearPendingAssessment}
            initialTab={pendingAssessmentTab}
            onNavigate={navigateTo}
          />
        );
      case 'checklists':
        return <ChecklistBoard />;
      case 'terraform':
        return (
          <TerraformBoard
            initialRun={pendingTerraformRun}
            onInitialRunConsumed={clearPendingTerraform}
            onNavigate={navigateTo}
          />
        );
      default:
        return <DashboardPage onNavigate={navigateTo} />;
    }
  };

  return (
    <div className="flex h-screen bg-gray-100">
      <Sidebar
        currentPage={currentPage}
        onNavigate={navigateTo}
        onOpenSubscriptionPicker={() => setSubscriptionDrawerOpen(true)}
      />

      <main className="flex-1 overflow-auto">
        {renderPage()}
      </main>

      <SubscriptionPickerDrawer
        open={subscriptionDrawerOpen}
        onClose={() => setSubscriptionDrawerOpen(false)}
        preventBackdropClose={needsSubscriptionIntersectionPick}
      />


      <ChatSidebar />

    </div>
  );
}

function routePath(): string {
  const p = window.location.pathname.replace(/\/$/, '') || '/';
  return p;
}

function App() {
  const path = routePath();
  if (path === '/login') {
    return <LoginPage />;
  }
  return (
    <AzureSessionProvider>
      <AssessmentRunProvider>
        <TerraformRunProvider>
          <AuthGate>
            <AppShell />
          </AuthGate>
        </TerraformRunProvider>
      </AssessmentRunProvider>
    </AzureSessionProvider>
  );
}

export default App;
