const JSON_HEADERS = { "Content-Type": "application/json" };

async function parseJsonResponse(response) {
  const payload = await response.json();
  if (!response.ok) {
    throw new Error(payload.message ?? "Request failed.");
  }
  return payload;
}

export async function analyzeArticle(request) {
  const response = await fetch("/analyze", {
    method: "POST",
    headers: JSON_HEADERS,
    body: JSON.stringify(request),
  });
  return parseJsonResponse(response);
}

export async function fetchHistory(limit = 10) {
  const response = await fetch(`/history?limit=${encodeURIComponent(limit)}`);
  return parseJsonResponse(response);
}

export async function fetchMetrics() {
  const response = await fetch("/metrics");
  return parseJsonResponse(response);
}

/**
 * Opens an SSE connection to /feed.
 * Returns { close(): void } handle.
 */
export function connectToFeed({ onEvent, onOpen, onError }) {
  const source = new EventSource("/feed");

  source.onopen = () => {
    if (onOpen) onOpen();
  };

  source.onmessage = (msg) => {
    try {
      const data = JSON.parse(msg.data);
      onEvent(data);
    } catch {
      /* ignore malformed frames */
    }
  };

  source.onerror = () => {
    if (onError) onError();
  };

  return { close: () => source.close() };
}

export async function fetchLiveSentiment() {
  const response = await fetch("/sentiment/live");
  return parseJsonResponse(response);
}

export async function fetchCorrelation(offset = 0, limit = 50) {
  const response = await fetch(`/correlation?offset=${offset}&limit=${limit}`);
  return parseJsonResponse(response);
}
