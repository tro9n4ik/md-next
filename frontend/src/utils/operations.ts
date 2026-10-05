import { apiFetch } from './api';

export async function request<T>(path: string, body?: unknown, method = body === undefined ? 'GET' : 'POST'): Promise<T> {
  const response = await apiFetch(path, { method, ...(body === undefined ? {} : { headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }) });
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    throw new Error(typeof data.detail === 'string' ? data.detail : 'Не удалось выполнить запрос');
  }
  return response.json();
}

export function saveFile(data: BlobPart, name: string, type = 'application/json') {
  const url = URL.createObjectURL(new Blob([data], { type }));
  const link = document.createElement('a'); link.href = url; link.download = name; link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export async function downloadBackup(name: string) {
  const response = await apiFetch(`/api/v1/operations/backups/${encodeURIComponent(name)}`);
  if (!response.ok) throw new Error('Не удалось скачать копию');
  saveFile(await response.blob(), name, 'application/octet-stream');
}
