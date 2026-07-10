import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useState,
  type ReactNode,
} from 'react';

interface AssessmentRunContextValue {
  isRunning: boolean;
  timeHint: string | null;
  resultMessage: string | null;
  /** 평가 시작 — timeHint: 예상 소요 시간 문자열 */
  startRun: (timeHint?: string | null) => void;
  /** 평가 종료 — message: 결과/오류 메시지 */
  finishRun: (message: string | null) => void;
  /** 결과 메시지 초기화 */
  clearResult: () => void;
}

const AssessmentRunContext = createContext<AssessmentRunContextValue | null>(null);

export function AssessmentRunProvider({ children }: { children: ReactNode }) {
  const [isRunning, setIsRunning] = useState(false);
  const [timeHint, setTimeHint] = useState<string | null>(null);
  const [resultMessage, setResultMessage] = useState<string | null>(null);

  const startRun = useCallback((hint?: string | null) => {
    setIsRunning(true);
    setTimeHint(hint ?? null);
    setResultMessage(null);
  }, []);

  const finishRun = useCallback((message: string | null) => {
    setIsRunning(false);
    setTimeHint(null);
    setResultMessage(message);
  }, []);

  const clearResult = useCallback(() => {
    setResultMessage(null);
  }, []);

  const value = useMemo(
    () => ({ isRunning, timeHint, resultMessage, startRun, finishRun, clearResult }),
    [isRunning, timeHint, resultMessage, startRun, finishRun, clearResult],
  );

  return (
    <AssessmentRunContext.Provider value={value}>
      {children}
    </AssessmentRunContext.Provider>
  );
}

export function useAssessmentRun() {
  const ctx = useContext(AssessmentRunContext);
  if (!ctx) throw new Error('useAssessmentRun must be used within AssessmentRunProvider');
  return ctx;
}
