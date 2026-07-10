import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useState,
  type ReactNode,
} from 'react';

interface TerraformRunContextValue {
  isRunning: boolean;
  startRun: () => void;
  finishRun: () => void;
}

const TerraformRunContext = createContext<TerraformRunContextValue | null>(null);

export function TerraformRunProvider({ children }: { children: ReactNode }) {
  const [isRunning, setIsRunning] = useState(false);

  const startRun = useCallback(() => setIsRunning(true), []);
  const finishRun = useCallback(() => setIsRunning(false), []);

  const value = useMemo(
    () => ({ isRunning, startRun, finishRun }),
    [isRunning, startRun, finishRun],
  );

  return (
    <TerraformRunContext.Provider value={value}>
      {children}
    </TerraformRunContext.Provider>
  );
}

export function useTerraformRun() {
  const ctx = useContext(TerraformRunContext);
  if (!ctx) throw new Error('useTerraformRun must be used within TerraformRunProvider');
  return ctx;
}
