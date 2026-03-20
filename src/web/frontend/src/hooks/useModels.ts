import { useQuery } from '@tanstack/react-query';

const BASE = '/api';

async function fetchJson<T>(url: string): Promise<T> {
  const resp = await fetch(url);
  if (!resp.ok) throw new Error(`${resp.status} ${resp.statusText}`);
  return resp.json() as Promise<T>;
}

export function useModels() {
  return useQuery<string[]>({
    queryKey: ['models'],
    queryFn: () => fetchJson<string[]>(`${BASE}/models`),
    staleTime: 30_000,
  });
}
