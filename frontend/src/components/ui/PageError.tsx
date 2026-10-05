export default function PageError() {
  return <section role="alert" className="ui-card ui-panel space-y-4"><h1 className="text-xl font-semibold">Не удалось открыть страницу</h1><p className="text-sm text-neutral-600">Попробуйте загрузить её снова. Если ошибка повторится, проверьте журнал сервера.</p><button onClick={() => window.location.reload()} className="ui-button ui-button-primary">Повторить загрузку</button><a className="ui-button ui-button-secondary ml-3" href="#/">К обзору</a></section>;
}
