import { apiFetch } from './api';

export async function groupRequest<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await apiFetch(url, init);
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(typeof body.detail === 'string' ? body.detail : 'Не удалось сохранить изменения');
  }
  return response.status === 204 ? undefined as T : response.json();
}
