import { useEffect, useState } from 'react';
import { AssessmentResultsPanel } from './AssessmentResultsPanel';
import { DiagnosisAssessmentPanel } from './DiagnosisAssessmentPanel';
import { AssessmentChartsPanel } from './AssessmentChartsPanel';
import type { NavigateTo } from '../App';

export interface AssessmentBoardProps {
  initialFilename?: string | null;
  onInitialFilenameConsumed?: () => void;
  initialTab?: AssessmentTabId;
  onNavigate?: NavigateTo;
}

type AssessmentTabId = 'charts' | 'diagnosis' | 'results';

export function AssessmentBoard({
  initialFilename = null,
  onInitialFilenameConsumed,
  initialTab,
  onNavigate,
}: AssessmentBoardProps) {
  const [tab, setTab] = useState<AssessmentTabId>(() => {
    if (initialTab) return initialTab;
    return initialFilename ? 'results' : 'diagnosis';
  });
  const [openLatestResultOnResultsTab, setOpenLatestResultOnResultsTab] = useState(false);

  useEffect(() => {
    if (initialFilename) {
      setTab('results');
    }
  }, [initialFilename]);

  return (
    <div className="h-full flex flex-col bg-gray-100">
      <div className="shrink-0 flex gap-1 px-6 pt-4 bg-white border-b border-gray-200">
        <TabButton
          active={tab === 'charts'}
          onClick={() => setTab('charts')}
          label="요약 차트"
        />
        <TabButton
          active={tab === 'diagnosis'}
          onClick={() => setTab('diagnosis')}
          label="진단평가"
        />
        <TabButton
          active={tab === 'results'}
          onClick={() => setTab('results')}
          label="평가결과"
        />
      </div>
      <div className="flex-1 min-h-0 overflow-auto">
        {tab === 'charts' ? (
          <AssessmentChartsPanel onNavigate={onNavigate} />
        ) : tab === 'diagnosis' ? (
          <DiagnosisAssessmentPanel
            onAssessmentFinished={() => {
              setOpenLatestResultOnResultsTab(true);
              setTab('results');
            }}
          />
        ) : (
          <AssessmentResultsPanel
            initialFilename={initialFilename}
            onInitialFilenameConsumed={onInitialFilenameConsumed}
            openLatestGroupAfterAssessment={openLatestResultOnResultsTab}
            onOpenLatestGroupConsumed={() => setOpenLatestResultOnResultsTab(false)}
            onNavigate={onNavigate}
          />
        )}
      </div>
    </div>
  );
}

function TabButton({
  active,
  onClick,
  label,
}: {
  active: boolean;
  onClick: () => void;
  label: string;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`
        px-4 py-2.5 text-sm font-medium rounded-t-lg border border-b-0 transition-colors
        ${active
          ? 'bg-gray-100 text-azure-blue border-gray-200 -mb-px z-10'
          : 'bg-white text-gray-500 border-transparent hover:text-gray-700 hover:bg-gray-50'
        }
      `}
    >
      {label}
    </button>
  );
}
