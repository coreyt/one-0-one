import { useMutation, useQuery } from '@tanstack/react-query';
import type { SessionConfig } from '../types/config';

const BASE = '/api';

async function fetchJson<T>(url: string, opts?: RequestInit): Promise<T> {
  const resp = await fetch(url, opts);
  if (!resp.ok) throw new Error(`${resp.status} ${resp.statusText}`);
  return resp.json() as Promise<T>;
}

export function useSession(sessionId: string) {
  return useQuery({
    queryKey: ['sessions', sessionId],
    queryFn: () => fetchJson<Record<string, unknown>>(`${BASE}/sessions/${sessionId}`),
    enabled: !!sessionId,
    refetchInterval: false,
  });
}

export function useStartSession() {
  return useMutation({
    mutationFn: (config: SessionConfig) =>
      fetchJson<{ session_id: string }>(`${BASE}/sessions`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(config),
      }),
  });
}

export function usePauseSession() {
  return useMutation({
    mutationFn: (sessionId: string) =>
      fetchJson(`${BASE}/sessions/${sessionId}/pause`, { method: 'POST' }),
  });
}

export function useResumeSession() {
  return useMutation({
    mutationFn: (sessionId: string) =>
      fetchJson(`${BASE}/sessions/${sessionId}/resume`, { method: 'POST' }),
  });
}

export function useInjectMessage() {
  return useMutation({
    mutationFn: ({ sessionId, text, channel_id }: { sessionId: string; text: string; channel_id: string }) =>
      fetchJson(`${BASE}/sessions/${sessionId}/inject`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text, channel_id }),
      }),
  });
}

export function useEndSession() {
  return useMutation({
    mutationFn: (sessionId: string) =>
      fetchJson(`${BASE}/sessions/${sessionId}/end`, { method: 'POST' }),
  });
}
