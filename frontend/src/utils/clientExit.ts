import { useQuery } from '@tanstack/react-query';
import { apiFetch } from './api';

export type ExitNode = {
  id: number; name: string; country_code: string | null; is_enabled: boolean;
  status: string; ping_ms: number | null; selectable: boolean;
  assigned_clients?: number; following_clients?: number;
};
export type ExitRoute = { mode: 'common' | 'node'; node_id: number | null; node: ExitNode | null; status: string };
export type CommonExit = { mode: 'node' | 'direct' | 'unavailable'; node: ExitNode | null };
export type ExitOptions = { nodes: ExitNode[]; common: CommonExit; common_clients: number; total_clients: number };
export type RouteData = ExitOptions & { route: ExitRoute };

export async function exitRequest<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await apiFetch(path, options);
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(typeof body.detail === 'string' ? body.detail : 'Не удалось загрузить маршруты');
  }
  return response.json();
}

export function useExitOptions(enabled: boolean) {
  return useQuery<ExitOptions>({ queryKey: ['clientExitOptions'], queryFn: () => exitRequest('/api/v1/clients/exit-options'), enabled, staleTime: 15000 });
}
