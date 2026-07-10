/**
 * AG-UI Client for TypeScript/React.
 *
 * Provides a lightweight client to interact with an AG-UI (Agentic UI)
 * server via Server-Sent Events (SSE).
 */

export interface AgUiMessage {
  id: string;
  role: 'user' | 'assistant' | 'system';
  content: string;
}

export interface AgUiEvent {
  type: string;
  [key: string]: any;
}

/**
 * Streams events from an AG-UI server.
 *
 * @param url The runtime URL (base server URL).
 * @param messages Message history to send.
 * @param threadId Unique ID for the conversation thread.
 * @param signal AbortSignal to cancel the request.
 * @param azure Optional tenant + subscription for X-Azure-* headers.
 */
export async function* streamAgUi(
  url: string,
  messages: AgUiMessage[],
  threadId: string,
  signal?: AbortSignal,
  azure?: { tenantId: string; subscriptionId: string; subscriptionName?: string } | null
): AsyncIterableIterator<AgUiEvent> {
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    Accept: 'text/event-stream',
    'X-Thread-Id': threadId,
  };
  if (azure?.tenantId && azure?.subscriptionId) {
    headers['X-Azure-Tenant-Id'] = azure.tenantId;
    headers['X-Azure-Subscription-Id'] = azure.subscriptionId;
    if (azure.subscriptionName?.trim()) {
      headers['X-Azure-Subscription-Name'] = encodeURIComponent(azure.subscriptionName.trim());
    }
  }

  const base = (url || "").replace(/\/$/, "");
  const chatUrl = base ? `${base}/api/chat` : "/api/chat";
  const response = await fetch(chatUrl, {

    method: 'POST',
    headers,
    body: JSON.stringify({ messages }),
    signal,
    credentials: 'include',
  });

  if (!response.ok) {
    const errorText = await response.text();
    throw new Error(`AG-UI Server Error: ${response.status} ${errorText}`);
  }

  const reader = response.body?.getReader();
  if (!reader) {
    throw new Error('Response body is null');
  }

  const decoder = new TextDecoder();
  let buffer = '';

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split('\n');
      buffer = lines.pop() || '';

      for (const line of lines) {
        const trimmed = line.trim();
        if (!trimmed || !trimmed.startsWith('data: ')) continue;

        const dataStr = trimmed.slice(6);
        if (dataStr === '[DONE]') break;

        try {
          const event = JSON.parse(dataStr) as AgUiEvent;
          yield event;
        } catch (e) {
          console.warn('Failed to parse AG-UI event data:', dataStr, e);
        }
      }
    }
  } finally {
    reader.releaseLock();
  }
}

/**
 * Checks if an event type matches a specific pattern.
 *
 * The server might send types like "TextMessageStart" or "message_start".
 * This helper provides a unified way to match them.
 */
export function matchEvent(actual: string, target: string): boolean {
  if (!actual || !target) return false;
  
  const norm = (s: string) => s.toLowerCase().replace(/_/g, '').replace(/event$/, '');
  
  // Exact match
  if (actual === target) return true;
  
  // Normalized match (e.g., text_message_start vs TextMessageStart)
  if (norm(actual) === norm(target)) return true;
  
  return false;
}
