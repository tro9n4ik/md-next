import { useEffect, useRef, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { ArrowRight, ArrowUpRight, BookOpen, ChevronRight, Clock3, Compass, Search, ShieldCheck, Stethoscope, X } from 'lucide-react';
import PageLayout from '../components/ui/PageLayout';
import { findWikiArticles, wikiArticles, wikiCategories } from '../data/wiki';

export default function HelpPage() {
  const [params, setParams] = useSearchParams();
  const [query, setQuery] = useState('');
  const [category, setCategory] = useState('all');
  const visible = findWikiArticles(query, category);
  const requested = params.get('article');
  const previousArticle = useRef(requested);
  useEffect(() => {
    if (previousArticle.current === requested) return;
    previousArticle.current = requested;
    document.getElementById('wiki-article-title')?.focus({preventScroll: true});
    document.getElementById('wiki-reader')?.scrollIntoView({block: 'start'});
  }, [requested]);
  const article = visible.find(item => item.id === requested) || visible[0];
  const missing = requested && !wikiArticles.some(item => item.id === requested);
  const readingMinutes = article ? Math.max(1, Math.ceil(article.points.flat().join(' ').split(/\s+/).length / 160)) : 0;
  function openArticle(id: string) {
    setQuery(''); setCategory('all'); setParams({article: id});
  }
  return <PageLayout icon={BookOpen} title="Wiki" description="Знания о панели: от первого подключения до уверенного управления сетью">
    <div className="wiki-page">
      <section className="wiki-hero ui-card">
        <div className="wiki-hero-copy"><span className="wiki-eyebrow"><BookOpen size={14} />MD-NEXT / БАЗА ЗНАНИЙ</span>
          <h2>Разобраться. Настроить. Проверить.</h2><p>Пошаговые инструкции, объяснения параметров и помощь в поиске причин. Всё, что нужно для работы с панелью, — в одном месте.</p>
          <div className="wiki-search"><Search size={20} aria-hidden="true" /><input type="search" value={query} onChange={event => setQuery(event.target.value)} aria-label="Поиск по Wiki" placeholder="Например: лимит трафика, SNI, Gemini…" />{query && <button type="button" aria-label="Очистить поиск" onClick={() => setQuery('')}><X size={17} /></button>}</div>
          <p className="wiki-search-hint">Поиск по названиям, объяснениям и шагам настройки</p>
        </div>
        <div className="wiki-hero-mark" aria-hidden="true"><BookOpen size={66} strokeWidth={1} /><span>{wikiArticles.length} статей</span><small>5 разделов · на русском</small></div>
      </section>
      <div className="wiki-shortcuts">
        {[{id: 'quickstart', title: 'Начать с нуля', text: 'Первое подключение за несколько шагов', icon: Compass}, {id: 'diagnostics', title: 'Разобраться с проблемой', text: 'От симптома к проверке и причине', icon: Stethoscope}, {id: 'account', title: 'Защитить доступ', text: 'Пароль, 2FA и безопасность аккаунта', icon: ShieldCheck}].map(({id, title, text, icon: Icon}) => <button type="button" key={id} className="wiki-shortcut ui-card" onClick={() => openArticle(id)}><span className="wiki-shortcut-icon"><Icon size={21} /></span><span><b>{title}</b><small>{text}</small></span><ArrowUpRight size={17} /></button>)}
      </div>
      <div className="wiki-categories" role="group" aria-label="Темы Wiki">
        <button type="button" aria-pressed={category === 'all'} onClick={() => setCategory('all')}>Все темы <span>{wikiArticles.length}</span></button>
        {wikiCategories.map(item => <button type="button" key={item.id} aria-pressed={category === item.id} onClick={() => setCategory(item.id)} title={item.description}>{item.title}<span>{wikiArticles.filter(article => article.category === item.id).length}</span></button>)}
      </div>
      <p className="wiki-result-count" role="status">{query.trim() ? `Найдено статей: ${visible.length}` : `В справочнике: ${visible.length} статей`}</p>
      {!visible.length ? <section className="ui-card wiki-empty"><Search size={32} /><h2>Ответ пока не найден</h2><p>Попробуйте более короткий запрос или выберите другую тему.</p><button type="button" className="ui-button ui-button-secondary" onClick={() => {setQuery(''); setCategory('all');}}>Показать все статьи</button></section> : <div className="wiki-layout">
        <nav className="wiki-index ui-card" aria-label="Статьи Wiki">{wikiCategories.map(group => {
          const articles = visible.filter(item => item.category === group.id);
          return articles.length > 0 && <div key={group.id} className="wiki-index-group"><p>{group.title}</p>{articles.map(item => <Link key={item.id} to={{pathname: '/wiki', search: `?article=${item.id}`}} aria-current={article?.id === item.id ? 'page' : undefined}><span>{item.title}</span><ChevronRight size={14} /></Link>)}</div>;
        })}</nav>
        {article && <article id="wiki-reader" className="wiki-reader ui-card">
          {missing && <p className="wiki-missing" role="status">Статья по этой ссылке не найдена. Выберите нужную в оглавлении.</p>}
          <header className="wiki-article-header"><div className="wiki-article-meta"><span>{wikiCategories.find(item => item.id === article.category)?.title}</span><span><Clock3 size={13} />{readingMinutes} мин чтения</span></div><h2 id="wiki-article-title" tabIndex={-1}>{article.title}</h2><p>{article.intro}</p>
            <div className="wiki-article-actions">{article.path !== '/wiki' && <Link to={article.path} className="ui-button ui-button-primary">Открыть в панели <ArrowUpRight size={15} /></Link>}<Link to={`/wiki?article=${article.id}`} className="wiki-permalink">Постоянная ссылка на статью <ArrowUpRight size={14} /></Link></div>
          </header>
          <nav className="wiki-outline" aria-label="В этой статье"><b>В этой статье</b>{article.points.map(([title], index) => <button key={title} type="button" onClick={() => {const section = document.getElementById(`wiki-${article.id}-${index}`); section?.scrollIntoView({block: 'start'}); section?.focus({preventScroll: true});}}>{String(index + 1).padStart(2, '0')}<span>{title}</span></button>)}</nav>
          <div className="wiki-article-body">{article.points.map(([title, text], index) => <section key={title} id={`wiki-${article.id}-${index}`} tabIndex={-1}><div className="wiki-step-number">{String(index + 1).padStart(2, '0')}</div><div><h3>{title}</h3><p>{text}</p></div></section>)}</div>
          <footer className="wiki-related"><h3>Читайте также</h3>{wikiArticles.filter(item => item.category === article.category && item.id !== article.id).slice(0, 3).map(item => <button type="button" key={item.id} onClick={() => openArticle(item.id)}>{item.title}<ArrowRight size={15} /></button>)}<a href="https://github.com/tro9n4ik/md-next" target="_blank" rel="noopener noreferrer">Документация проекта на GitHub<ArrowUpRight size={15} /></a></footer>
        </article>}
      </div>}
    </div>
  </PageLayout>;
}
