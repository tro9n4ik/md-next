import { apiFetch } from './api';

const RETRIES = 2;

class PanelResponseError extends Error {
  readonly status: number;

  constructor(status: number) {
    super(`HTTP ${status}`);
    this.status = status;
  }
}

export function panelPolling<T>(path: string) {
  return {
    queryFn: async ({ signal }: { signal: AbortSignal }): Promise<T> => {
      const response = await apiFetch(path, { signal: AbortSignal.any([signal, AbortSignal.timeout(8000)]) });
      if (!response.ok) throw new PanelResponseError(response.status);
      return response.json();
    },
    retry: (failureCount: number, error: Error) =>
      failureCount < RETRIES &&
      (!(error instanceof PanelResponseError) || error.status >= 500 || error.status === 429),
    retryDelay: (attempt: number) => 1000 * 2 ** Math.min(attempt, RETRIES),
    refetchInterval: ({ state }: { state: { status: string } }) => state.status === 'error' ? 30000 : 15000,
  };
}

export function panelPollNotice(query: { data: unknown; failureCount: number; isError: boolean }) {
  if (!query.isError && query.failureCount === 0) return '';
  if (query.isError || query.failureCount > RETRIES) {
    return query.data === undefined
      ? 'Не удалось обновить данные панели.'
      : 'Не удалось обновить данные панели. Показаны последние полученные данные.';
  }
  return query.data === undefined
    ? 'Повторяем запрос к панели.'
    : 'Повторяем запрос к панели. Показаны последние полученные данные.';
}
