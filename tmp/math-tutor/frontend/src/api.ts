/**
 * 后端 API 封装
 */

const BASE_URL = '';

async function postSSE(
  url: string,
  body: object,
  onChunk: (chunk: string) => void,
  onDone: () => void,
  onError: (err: Error) => void,
) {
  try {
    const response = await fetch(`${BASE_URL}${url}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });

    if (!response.ok || !response.body) {
      throw new Error(`HTTP ${response.status}: ${response.statusText}`);
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split('\n\n');
      buffer = lines.pop() || '';

      for (const line of lines) {
        const trimmed = line.trim();
        if (!trimmed.startsWith('data: ')) continue;
        const data = trimmed.slice(6).trim();
        if (data === '[DONE]') {
          onDone();
          return;
        }
        onChunk(data);
      }
    }

    onDone();
  } catch (err) {
    onError(err instanceof Error ? err : new Error(String(err)));
  }
}

export async function chat(
  message: string,
  onChunk: (chunk: string) => void,
  onDone: () => void,
  onError: (err: Error) => void,
) {
  return postSSE('/api/chat', { message }, onChunk, onDone, onError);
}

export async function generateQuestions(
  topic: string,
  count: number,
  difficulty: string,
  onChunk: (chunk: string) => void,
  onDone: () => void,
  onError: (err: Error) => void,
) {
  return postSSE(
    '/api/generate',
    { topic, count, difficulty },
    onChunk,
    onDone,
    onError,
  );
}

export async function resetSession(): Promise<void> {
  const response = await fetch(`${BASE_URL}/api/reset`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({}),
  });
  if (!response.ok) {
    throw new Error(`HTTP ${response.status}: ${response.statusText}`);
  }
}
