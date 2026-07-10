/**
 * React hook for AG-UI chat.
 *
 * Manages chat messages, streaming, tool-call indicators, and abort.
 * Uses the lightweight AG-UI client — no CopilotKit dependency.
 */

import { useState, useCallback, useRef } from 'react';
import { streamAgUi, matchEvent, type AgUiMessage } from '../lib/ag-ui-client';
import { useAzureSession } from '../context/AzureSessionContext';
import {
  CHAT_REFRESH_ASSESSMENTS_EVENT,
  CHAT_REFRESH_TERRAFORM_EVENT,
} from '../lib/chatDataRefreshEvents';

// ── Helpers ──────────────────────────────────────────────────────

/** Throttle a callback to fire at most once per animation frame. */
function rafThrottle<T extends (...args: any[]) => void>(fn: T): T & { flush: () => void } {
  let rafId: number | null = null;
  let lastArgs: any[] | null = null;

  const throttled = ((...args: any[]) => {
    lastArgs = args;
    if (rafId === null) {
      rafId = requestAnimationFrame(() => {
        rafId = null;
        if (lastArgs) {
          fn(...lastArgs);
          lastArgs = null;
        }
      });
    }
  }) as T & { flush: () => void };

  throttled.flush = () => {
    if (rafId !== null) {
      cancelAnimationFrame(rafId);
      rafId = null;
    }
    if (lastArgs) {
      fn(...lastArgs);
      lastArgs = null;
    }
  };

  return throttled;
}

// ── Types ────────────────────────────────────────────────────────

export interface ChatMessage {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  isStreaming?: boolean;
}

export interface UseAgUiChatReturn {
  messages: ChatMessage[];
  isLoading: boolean;
  activeTools: string[];
  sendMessage: (content: string) => Promise<void>;
  stop: () => void;
  clear: () => void;
}

export interface UseAgUiChatOptions {
  onAssessmentStart?: () => void;
  onAssessmentEnd?: () => void;
  onTerraformStart?: () => void;
  onTerraformEnd?: () => void;
}

// ── Hook ─────────────────────────────────────────────────────────

export function useAgUiChat(runtimeUrl: string, options?: UseAgUiChatOptions): UseAgUiChatReturn {
  const { chatAzureHeaders } = useAzureSession();
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [activeTools, setActiveTools] = useState<string[]>([]);
  const threadIdRef = useRef(crypto.randomUUID());
  const abortRef = useRef<AbortController | null>(null);
  const azureRef = useRef(chatAzureHeaders);
  azureRef.current = chatAzureHeaders;

  // Keep a ref to the latest messages so sendMessage doesn't need
  // `messages` in its dependency array.  This prevents the entire
  // ChatSidebar from re-creating sendMessage on every state update.
  const messagesRef = useRef(messages);
  messagesRef.current = messages;

  const isLoadingRef = useRef(isLoading);
  isLoadingRef.current = isLoading;

  /** ToolCallEnd에는 이름이 없어서 Start 시점의 id↔name을 스택으로 맞춤 */
  const pendingToolCallsRef = useRef<{ id: string; name: string }[]>([]);

  const sendMessage = useCallback(
    async (content: string) => {
      const text = content.trim();
      if (!text || isLoadingRef.current) return;

      const userMsg: ChatMessage = {
        id: crypto.randomUUID(),
        role: 'user',
        content: text,
      };

      // Optimistic update
      setMessages((prev) => [...prev, userMsg]);
      setIsLoading(true);
      setActiveTools([]);
      pendingToolCallsRef.current = [];

      const abortCtrl = new AbortController();
      abortRef.current = abortCtrl;

      // Throttle streaming content updates to once per animation frame
      // so the main thread stays free for page transitions & user interactions.
      const flushContent = rafThrottle((id: string, snap: string) => {
        setMessages((prev) =>
          prev.map((m) => (m.id === id ? { ...m, content: snap } : m)),
        );
      });

      try {
        // Build message history for the API
        const allMsgs: AgUiMessage[] = [
          ...messagesRef.current.map((m) => ({ id: m.id, role: m.role, content: m.content })),
          { id: userMsg.id, role: userMsg.role, content: userMsg.content },
        ];

        let asstId = '';
        let asstContent = '';

        for await (const ev of streamAgUi(
          runtimeUrl,
          allMsgs,
          threadIdRef.current,
          abortCtrl.signal,
          azureRef.current,
        )) {
          const t = ev.type;

          // ── Text message lifecycle ──
          if (matchEvent(t, 'TextMessageStart')) {
            asstId = (ev.message_id as string) || crypto.randomUUID();
            asstContent = '';
            setMessages((prev) => [
              ...prev,
              { id: asstId, role: 'assistant', content: '', isStreaming: true },
            ]);
          } else if (matchEvent(t, 'TextMessageContent')) {
            asstContent += ev.delta as string;
            // Throttled — renders at most once per frame
            flushContent(asstId, asstContent);
          } else if (matchEvent(t, 'TextMessageEnd')) {
            // Flush any pending content before marking stream as done
            flushContent.flush();
            const id = asstId;
            setMessages((prev) =>
              prev.map((m) => (m.id === id ? { ...m, isStreaming: false } : m)),
            );
          }

          // ── Tool call indicators + 데이터 패널 재조회 신호 ──
          else if (matchEvent(t, 'ToolCallStart')) {
            const raw = ev as Record<string, unknown>;
            const id = String(raw.tool_call_id ?? raw.toolCallId ?? '');
            const name = String(raw.tool_call_name ?? raw.toolCallName ?? 'tool') || 'tool';
            pendingToolCallsRef.current.push({ id, name });
            setActiveTools((prev) => [...prev, name]);
            if (name === 'run_assessment') {
              options?.onAssessmentStart?.();
            }
            if (name === 'generate_terraform_code') {
              options?.onTerraformStart?.();
            }
          } else if (matchEvent(t, 'ToolCallEnd')) {
            const raw = ev as Record<string, unknown>;
            const endId = String(raw.tool_call_id ?? raw.toolCallId ?? '');
            const stack = pendingToolCallsRef.current;
            let completed: { id: string; name: string } | undefined;
            const idx = endId ? stack.findIndex((x) => x.id === endId) : -1;
            if (idx >= 0) {
              completed = stack.splice(idx, 1)[0];
            } else if (stack.length > 0) {
              completed = stack.shift();
            }
            setActiveTools((prev) => {
              if (!completed?.name) return prev.slice(1);
              const i = prev.indexOf(completed.name);
              if (i >= 0) return [...prev.slice(0, i), ...prev.slice(i + 1)];
              return prev.slice(1);
            });
            if (completed?.name === 'run_assessment') {
              window.dispatchEvent(new CustomEvent(CHAT_REFRESH_ASSESSMENTS_EVENT));
              options?.onAssessmentEnd?.();
            }
            if (completed?.name === 'generate_terraform_code') {
              window.dispatchEvent(new CustomEvent(CHAT_REFRESH_TERRAFORM_EVENT));
              options?.onTerraformEnd?.();
            }
          }

          // ── Errors ──
          else if (matchEvent(t, 'RunError')) {
            const msg = (ev.message as string) || '알 수 없는 오류가 발생했습니다.';
            setMessages((prev) => [
              ...prev,
              { id: crypto.randomUUID(), role: 'assistant', content: `⚠️ ${msg}` },
            ]);
          }
        }
      } catch (err: unknown) {
        if (err instanceof DOMException && err.name === 'AbortError') {
          // User cancelled — not an error
        } else {
          const msg = err instanceof Error ? err.message : String(err);
          setMessages((prev) => [
            ...prev,
            {
              id: crypto.randomUUID(),
              role: 'assistant',
              content: `⚠️ 연결 오류: ${msg}`,
            },
          ]);
        }
      } finally {
        flushContent.flush();
        setIsLoading(false);
        setActiveTools([]);
        pendingToolCallsRef.current = [];
        abortRef.current = null;
      }
    },
    [runtimeUrl],
  );

  const stop = useCallback(() => {
    abortRef.current?.abort();
  }, []);

  const clear = useCallback(() => {
    setMessages([]);
    threadIdRef.current = crypto.randomUUID();
  }, []);

  return { messages, isLoading, activeTools, sendMessage, stop, clear };
}
