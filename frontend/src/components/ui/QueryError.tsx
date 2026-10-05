export default function QueryError({ error, retry }: { error: Error | null; retry: () => unknown }) {
  return <div role="alert" className="ui-card ui-panel text-red-700"><p>{error?.message || 'Не удалось загрузить данные'}</p><button onClick={retry} className="ui-button ui-button-secondary mt-3">Повторить загрузку</button></div>;
}
