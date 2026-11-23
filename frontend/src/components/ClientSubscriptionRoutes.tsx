import { useEffect, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Globe2, RefreshCw } from 'lucide-react';
import { exitRequest } from '../utils/clientExit';
import type { ExitNode, ExitRoute } from '../utils/clientExit';
import CountryFlag from './ui/CountryFlag';

type Choices = { enabled: boolean; node_ids: number[] };
type Routes = Choices & { nodes: ExitNode[]; route: ExitRoute };

export default function ClientSubscriptionRoutes({ clientId }: { clientId: number }) {
  const queryClient = useQueryClient();
  const queryKey = ['clientSubscriptionRoutes', clientId];
  const query = useQuery<Routes>({ queryKey,
    queryFn: () => exitRequest(`/api/v1/clients/${clientId}/subscription-routes`), refetchInterval: 15000 });
  const [draft, setDraft] = useState<Choices>();
  const [notice, setNotice] = useState('');
  const section = useRef<HTMLElement>(null);
  const saved = query.data;
  const selected = draft || saved;
  const dirty = Boolean(draft && saved && (draft.enabled !== saved.enabled ||
    JSON.stringify([...draft.node_ids].sort((a, b) => a - b)) !== JSON.stringify([...saved.node_ids].sort((a, b) => a - b))));
  useEffect(() => {
    const scope = section.current;
    const notify = (value: boolean) => window.dispatchEvent(new CustomEvent('ui:unsaved-change', { detail: { scope, dirty: value } }));
    notify(dirty);
    return () => { notify(false); };
  }, [dirty]);
  const mutation = useMutation({
    mutationFn: () => exitRequest<Routes>(`/api/v1/clients/${clientId}/subscription-routes`, {
      method: 'PUT', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ enabled: selected!.enabled, node_ids: selected!.node_ids }),
    }),
    onSuccess: data => {
      queryClient.setQueryData(queryKey, data);
      setDraft(undefined);
      setNotice('Сохранено. Обновите подписку в VPN-приложении, чтобы получить список стран.');
      void queryClient.invalidateQueries({ queryKey: ['clientProfiles', clientId] });
    },
    onError: (error: Error) => setNotice(error.message),
  });
  const change = (value: Choices) => { setDraft(value); setNotice(''); };

  return <section ref={section} data-unsaved-controlled className="ui-card ui-panel access-country-picker" aria-labelledby="subscription-routes-title">
    <h2 id="subscription-routes-title" className="ui-card-title flex items-center gap-2"><Globe2 size={20} />Страны в подписке</h2>
    <p className="mt-2 text-sm text-neutral-500">Разрешите этому клиенту самостоятельно выбирать выход в интернет в Happ или другом VPN-приложении.</p>
    {!saved || !selected ? <div className="mt-4" role="status">
      {query.isError ? query.error.message : 'Загрузка стран…'}
      {query.isError && <button className="ui-button ui-button-secondary" onClick={() => query.refetch()}><RefreshCw size={15} />Повторить</button>}
    </div> : <>
      {query.isError && <p className="mt-3 text-sm text-amber-700" role="alert">Не удалось обновить список нод. <button onClick={() => query.refetch()}>Повторить</button></p>}
      <label className="access-country-toggle">
        <input type="checkbox" checked={selected.enabled} disabled={mutation.isPending}
          onChange={event => change({ ...selected, enabled: event.target.checked })} />
        Добавлять другие страны в подписку
      </label>
      {selected.enabled && <div className="access-country-grid">
        {saved.nodes.map(node => <label key={node.id} className="access-country-option">
          <input type="checkbox" checked={selected.node_ids.includes(node.id)}
            disabled={mutation.isPending || (!node.selectable && !selected.node_ids.includes(node.id))}
            onChange={event => change({ ...selected, node_ids: event.target.checked
              ? [...selected.node_ids, node.id] : selected.node_ids.filter(id => id !== node.id) })} />
          <CountryFlag code={node.country_code} />
          <span className="min-w-0 break-words">{node.name}
            <small className="block text-neutral-500">{!node.selectable ? 'Выключена или не настроена'
              : node.status === 'healthy' ? 'Доступна' : 'Нет связи — проверьте ноду'}</small>
          </span>
        </label>)}
        {selected.node_ids.filter(id => !saved.nodes.some(node => node.id === id)).map(id => <label key={id} className="flex items-center gap-3 text-sm">
          <input type="checkbox" checked disabled={mutation.isPending}
            onChange={() => change({ ...selected, node_ids: selected.node_ids.filter(item => item !== id) })} />
          Нода №{id} удалена
        </label>)}
        {!saved.nodes.some(node => node.selectable) && <p className="text-sm text-amber-700">Сначала подключите выходные ноды в разделе «Узлы».</p>}
      </div>}
      <details className="access-explanation"><summary>Что изменится для клиента</summary><p>Основной маршрут сохраняется. Дополнительные страны доступны для разрешённых VLESS, XHTTP, Hysteria 2 и «Обхода БС». Трафик всех стран входит в общий лимит клиента. AmneziaWG и Telegram MTProto используют основной маршрут.</p></details>
      <div className="ui-actionbar mt-4">
        <p role={mutation.isError ? 'alert' : 'status'} className={mutation.isError ? 'text-sm text-red-700' : 'text-sm text-neutral-500'}>
          {notice || 'По умолчанию дополнительные страны отключены. Выберите только те ноды, которые разрешены клиенту.'}
        </p>
        <button className="ui-button ui-button-primary" disabled={!dirty || mutation.isPending ||
          (selected.enabled && (!selected.node_ids.length || selected.node_ids.some(id => !saved.nodes.find(node => node.id === id)?.selectable)))}
          onClick={() => mutation.mutate()}>{mutation.isPending ? 'Сохраняем…' : 'Сохранить страны'}</button>
      </div>
    </>}
  </section>;
}
