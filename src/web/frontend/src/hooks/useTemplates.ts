import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import type { SessionConfig, TemplateSummary } from '../types/config';

const BASE = '/api';

async function fetchJson<T>(url: string, opts?: RequestInit): Promise<T> {
  const resp = await fetch(url, opts);
  if (!resp.ok) throw new Error(`${resp.status} ${resp.statusText}`);
  return resp.json() as Promise<T>;
}

export function useTemplates(filter?: { type?: string; q?: string }) {
  const params = new URLSearchParams();
  if (filter?.type) params.set('type', filter.type);
  if (filter?.q) params.set('q', filter.q);
  const query = params.toString() ? `?${params}` : '';

  return useQuery<TemplateSummary[]>({
    queryKey: ['templates', filter],
    queryFn: () => fetchJson<TemplateSummary[]>(`${BASE}/templates${query}`),
    staleTime: 30_000,
  });
}

export function useTemplate(slug: string) {
  return useQuery<SessionConfig>({
    queryKey: ['templates', slug],
    queryFn: () => fetchJson<SessionConfig>(`${BASE}/templates/${slug}`),
    enabled: !!slug,
  });
}

export function useSaveTemplate() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (config: SessionConfig) =>
      fetchJson<TemplateSummary>(`${BASE}/templates`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(config),
      }),
    onSuccess: () => client.invalidateQueries({ queryKey: ['templates'] }),
  });
}

export function useDeleteTemplate() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (slug: string) =>
      fetch(`${BASE}/templates/${slug}`, { method: 'DELETE' }),
    onSuccess: () => client.invalidateQueries({ queryKey: ['templates'] }),
  });
}
