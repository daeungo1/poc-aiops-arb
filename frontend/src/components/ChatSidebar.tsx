/**
 * Lightweight chat sidebar — replaces CopilotKit CopilotSidebar.
 *
 * Uses native AG-UI SSE protocol via useAgUiChat hook.
 * Renders markdown responses with react-markdown (no mermaid/shiki overhead).
 */

import { useState, useRef, useEffect, memo } from 'react';
import { useAgUiChat, type ChatMessage } from '../hooks/useAgUiChat';
import { MarkdownViewer } from './MarkdownViewer';
import { useAssessmentRun } from '../context/AssessmentRunContext';
import { useTerraformRun } from '../context/TerraformRunContext';

// ── Tool name label map ──────────────────────────────────────────
const TOOL_LABELS: Record<string, string> = {
  get_subscription_info: '구독 정보 조회',
  list_azure_resources: '리소스 목록 조회',
  list_checklists: '체크리스트 목록 조회',
  get_checklist_detail: '체크리스트 상세 조회',
  run_assessment: '리소스 평가 실행',
  get_latest_assessments: '최근 평가 결과 조회',
  search_assessments: '평가 결과 검색',
  get_resource_detail: '리소스 상세 조회',
  generate_terraform_code: 'Terraform 코드 생성',
};

// ── ChatSidebar ──────────────────────────────────────────────────

interface ChatSidebarProps {
  /** 미지정 시 로컬 개발이면 `http://localhost:5100`, 아니면 동일 출처 상대 경로. */
  runtimeUrl?: string;
}

export function ChatSidebar({ runtimeUrl }: ChatSidebarProps) {
  const base = runtimeUrl ?? '';
  const { startRun, finishRun } = useAssessmentRun();
  const { startRun: startTerraformRun, finishRun: finishTerraformRun } = useTerraformRun();
  const { messages, isLoading, activeTools, sendMessage, stop, clear } =
    useAgUiChat(base, {
      onAssessmentStart: () => startRun(),
      onAssessmentEnd: () => finishRun(null),
      onTerraformStart: () => startTerraformRun(),
      onTerraformEnd: () => finishTerraformRun(),
    });
  const [input, setInput] = useState('');
  const bottomRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const [isOpen, setIsOpen] = useState(false);
  const prevIsLoadingRef = useRef(isLoading);

  // auto-scroll
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, activeTools]);

  // auto-resize textarea
  useEffect(() => {
    if (textareaRef.current) {
      textareaRef.current.style.height = 'auto';
      textareaRef.current.style.height = `${Math.min(textareaRef.current.scrollHeight, 128)}px`;
    }
  }, [input]);

  // 실행 완료 시 챗봇이 닫혀 있으면 자동으로 열기
  useEffect(() => {
    if (prevIsLoadingRef.current && !isLoading && !isOpen) {
      setIsOpen(true);
    }
    prevIsLoadingRef.current = isLoading;
  }, [isLoading, isOpen]);

  // ── External Message Event ──────────────────────────────────────
  useEffect(() => {
    const handleExternalMessage = (e: Event) => {
      const customEvent = e as CustomEvent<{ message: string }>;
      if (customEvent.detail?.message) {
        setIsOpen(true); // 메시지 전송 시 사이드바 열기
        sendMessage(customEvent.detail.message);
      }
    };
    window.addEventListener('CHAT_SEND_EXTERNAL_MESSAGE', handleExternalMessage);
    return () => window.removeEventListener('CHAT_SEND_EXTERNAL_MESSAGE', handleExternalMessage);
  }, [sendMessage]);

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (input.trim()) {
      sendMessage(input);
      setInput('');
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSubmit(e);
    }
  };

  // ── Collapsed toggle button ──
  if (!isOpen) {
    return (
      <button
        onClick={() => setIsOpen(true)}
        className="fixed right-4 bottom-4 w-14 h-14 bg-azure-blue text-white rounded-full shadow-lg
                   flex items-center justify-center hover:bg-azure-dark transition-colors z-[80] text-xl"
        title={isLoading ? '실행 중… (클릭하여 열기)' : '챗봇 열기'}
      >
        💬
        {isLoading && (
          <span className="absolute -top-1 -right-1 flex h-4 w-4">
            <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-yellow-400 opacity-75" />
            <span className="relative inline-flex rounded-full h-4 w-4 bg-yellow-400" />
          </span>
        )}
      </button>
    );
  }

  // ── Main panel ──
  return (
    <div className="fixed right-0 top-0 bottom-0 z-[80] w-[400px] min-w-[340px] border-l border-gray-200 bg-white flex flex-col shadow-2xl">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b bg-azure-blue text-white shrink-0">
        <h2 className="font-semibold text-sm flex items-center gap-2">
          <span>🤖</span> AIOps 챗봇
          {isLoading && (
            <span className="flex items-center gap-1 text-[11px] font-normal text-yellow-200">
              <span className="w-1.5 h-1.5 rounded-full bg-yellow-300 animate-pulse" />
              실행 중
            </span>
          )}
        </h2>
        <div className="flex gap-1">
          <button
            onClick={clear}
            disabled={isLoading}
            className="text-xs px-2 py-1 rounded hover:bg-azure-dark/60 transition-colors disabled:opacity-40"
            title="대화 초기화"
          >
            🗑️
          </button>
          <button
            onClick={() => setIsOpen(false)}
            className="text-xs px-2 py-1 rounded hover:bg-azure-dark/60 transition-colors"
            title={isLoading ? '닫기 (백그라운드에서 계속 실행됩니다)' : '닫기'}
          >
            ✕
          </button>
        </div>
      </div>

      {/* Messages */}
      <div className="flex-1 overflow-y-auto px-4 py-3 space-y-3">
        {messages.length === 0 && <WelcomeMessage />}

        {messages.map((msg) => (
          <MessageBubble key={msg.id} message={msg} />
        ))}

        {/* Tool indicator */}
        {activeTools.length > 0 && (
          <div className="flex items-center gap-2 text-xs text-gray-500 py-1">
            <span className="inline-block w-4 h-4 border-2 border-azure-blue border-t-transparent rounded-full animate-spin" />
            <span>{TOOL_LABELS[activeTools[0]] ?? activeTools[0]} 실행 중…</span>
          </div>
        )}

        {/* Thinking dots */}
        {isLoading && activeTools.length === 0 && messages.at(-1)?.role === 'user' && (
          <div className="text-gray-400 text-sm animate-pulse">● ● ●</div>
        )}

        <div ref={bottomRef} />
      </div>

      {/* Input */}
      <form onSubmit={handleSubmit} className="border-t p-3 shrink-0">
        <div className="flex gap-2 items-end">
          <textarea
            ref={textareaRef}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="메시지를 입력하세요…"
            className="flex-1 resize-none border rounded-xl px-3 py-2 text-sm
                       focus:outline-none focus:ring-2 focus:ring-azure-blue/40
                       max-h-32 leading-snug"
            rows={1}
            disabled={isLoading}
          />
          {isLoading ? (
            <button
              type="button"
              onClick={stop}
              className="shrink-0 w-9 h-9 flex items-center justify-center
                         bg-red-500 text-white rounded-xl text-sm
                         hover:bg-red-600 transition-colors"
              title="중단"
            >
              ■
            </button>
          ) : (
            <button
              type="submit"
              disabled={!input.trim()}
              className="shrink-0 w-9 h-9 flex items-center justify-center
                         bg-azure-blue text-white rounded-xl text-sm
                         hover:bg-azure-dark disabled:opacity-40
                         disabled:cursor-not-allowed transition-colors"
              title="전송"
            >
              ↑
            </button>
          )}
        </div>
      </form>
    </div>
  );
}

// ── Sub-components ───────────────────────────────────────────────

function WelcomeMessage() {
  return (
    <div className="text-gray-400 text-sm text-center mt-6 space-y-3">
      <p className="font-medium text-gray-500">
        Azure 리소스 평가를 도와드리겠습니다
      </p>
      <div className="grid grid-cols-1 gap-1.5 text-left max-w-[260px] mx-auto">
        {[
          ['📌', 'Azure 구독/리소스 조회'],
          ['📝', '체크리스트 확인'],
          ['🔬', '리소스 평가 실행'],
          ['📊', '평가 결과 조회/검색'],
          ['🏗️', 'Terraform 코드 생성'],
        ].map(([icon, label]) => (
          <span key={label} className="flex items-center gap-2">
            <span>{icon}</span>
            <span>{label}</span>
          </span>
        ))}
      </div>
    </div>
  );
}

const MessageBubble = memo(function MessageBubble({ message }: { message: ChatMessage }) {
  const isUser = message.role === 'user';

  return (
    <div className={`flex ${isUser ? 'justify-end' : 'justify-start'}`}>
      <div
        className={`max-w-[85%] rounded-2xl px-4 py-2.5 text-sm leading-relaxed ${
          isUser
            ? 'bg-azure-blue text-white'
            : 'bg-gray-100 text-gray-800'
        }`}
      >
        {isUser ? (
          <p className="whitespace-pre-wrap">{message.content}</p>
        ) : (
          <div className="markdown-body">
            {message.isStreaming ? (
              /* While streaming: render lightweight plain text so the main
                 thread stays free for page transitions and user interactions.
                 The full MarkdownViewer kicks in once the stream finishes. */
              <StreamingText content={message.content} />
            ) : (
              <MarkdownViewer content={message.content} />
            )}
            {message.isStreaming && (
              <span className="inline-block w-1.5 h-4 bg-azure-blue/60 animate-pulse ml-0.5 align-text-bottom rounded-sm" />
            )}
          </div>
        )}
      </div>
    </div>
  );
});

/** Lightweight plain-text renderer used during streaming.
 *  Avoids react-markdown / syntax-highlighter overhead on every frame. */
function StreamingText({ content }: { content: string }) {
  return (
    <div className="whitespace-pre-wrap break-words">
      {content}
    </div>
  );
}
