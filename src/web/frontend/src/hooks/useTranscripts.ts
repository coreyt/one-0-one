import { useQuery } from '@tanstack/react-query';

const BASE = '/api';

async function fetchJson<T>(url: string): Promise<T> {
  const resp = await fetch(url);
  if (!resp.ok) throw new Error(`${resp.status} ${resp.statusText}`);
  return resp.json() as Promise<T>;
}

export interface TranscriptSummary {
  id: string;
  title: string;
  setting?: string;
  date: string;
  agent_count: number;
  turn_count: number;
}

export function useTranscripts(filter?: { q?: string; type?: string; page?: number }) {
  const params = new URLSearchParams();
  if (filter?.q) params.set('q', filter.q);
  if (filter?.type) params.set('type', filter.type);
  if (filter?.page) params.set('page', String(filter.page));
  const query = params.toString() ? `?${params}` : '';

  return useQuery<TranscriptSummary[]>({
    queryKey: ['transcripts', filter],
    queryFn: () => fetchJson<TranscriptSummary[]>(`${BASE}/transcripts${query}`),
    staleTime: 60_000,
  });
}

export function useTranscript(id: string) {
  return useQuery({
    queryKey: ['transcripts', id],
    queryFn: () => fetchJson<Record<string, unknown>>(`${BASE}/transcripts/${id}`),
    enabled: !!id,
  });
}
