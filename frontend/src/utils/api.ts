export async function apiFetch(input: RequestInfo | URL, init: RequestInit = {}, mutationScope?: string): Promise<Response> {
  const mutation = ['PUT', 'PATCH', 'DELETE', 'POST'].includes((init.method || 'GET').toUpperCase()) && !/\/(login|test|check|diagnostics|extend|reset|telegram-code|backups)$/.test(String(input));
  const detail: { scope: Element | null } = { scope: mutationScope ? document.getElementById(mutationScope) : null };
  if (mutation) window.dispatchEvent(new CustomEvent('api:mutation-start', { detail }));
  const token = localStorage.getItem('token');
  const headers = new Headers(init.headers || {});

  if (token && !headers.has('Authorization')) {
    headers.set('Authorization', `Bearer ${token}`);
  }

  const response = await fetch(input, {
    ...init,
    headers,
  });

  const urlStr = typeof input === 'string' ? input : input.toString();
  if (mutation && response.ok) window.dispatchEvent(new CustomEvent('api:mutation-saved', { detail }));

  // При неверном пароле на эндпоинте /login не очищаем токен и не шлем auth:unauthorized
  if ((response.status === 401 || response.status === 403) && !urlStr.includes('/api/v1/auth/login')) {
    localStorage.removeItem('token');
    window.dispatchEvent(new Event('auth:unauthorized'));
  }

  return response;
}
