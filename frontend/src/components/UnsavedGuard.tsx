import { useEffect, useRef, useState } from 'react';
import { useBlocker, useLocation } from 'react-router-dom';

export default function UnsavedGuard() {
  const location = useLocation(); const [dirty, setDirty] = useState(false);
  const sections = useRef(new Set<Element>()); const lastSection = useRef<Element | null>(null);
  const blocker = useBlocker(dirty);
  useEffect(() => { sections.current.clear(); setDirty(false); }, [location.pathname]);
  useEffect(() => {
    const editable = /\/(settings|dns|routing|protocols|telegram|warp|bypass)$|\/clients\/\d+\/access$/.test(location.pathname);
    function targetSection(event: Event) {
      const target = event.target instanceof Element ? event.target : null;
      return target?.closest('[data-editable], .ui-card') || null;
    }
    function remember(event: Event) { lastSection.current = targetSection(event); }
    function change(event: Event) {
      const target = event.target instanceof Element ? event.target : null;
      if (target?.closest('[data-autosave]')) return;
      if (!target || (!editable && !target.closest('[data-editable]'))) return;
      if (target.matches('input[readonly], textarea[readonly], input[type=file]')) return;
      const section = targetSection(event);
      if (section) { sections.current.add(section); setDirty(true); }
    }
    function click(event: Event) { remember(event); const target = event.target instanceof Element ? event.target : null; if (target?.closest('[role=switch]')) change(event); }
    function start(event: Event) { const detail = (event as CustomEvent).detail; detail.scope ??= lastSection.current; }
    function saved(event: Event) { const scope = (event as CustomEvent).detail.scope; if (scope) sections.current.delete(scope); setDirty(sections.current.size > 0); }
    function unload(event: BeforeUnloadEvent) { if (sections.current.size) { event.preventDefault(); event.returnValue = ''; } }
    function logout(event: Event) { if (sections.current.size && !window.confirm('Есть несохранённые изменения. Выйти из панели?')) (event as CustomEvent).detail.cancelled = true; }
    document.addEventListener('input', change, true); document.addEventListener('change', change, true); document.addEventListener('click', click, true); document.addEventListener('submit', remember, true);
    window.addEventListener('api:mutation-start', start); window.addEventListener('api:mutation-saved', saved); window.addEventListener('beforeunload', unload);
    window.addEventListener('ui:before-logout', logout);
    return () => { document.removeEventListener('input', change, true); document.removeEventListener('change', change, true); document.removeEventListener('click', click, true); document.removeEventListener('submit', remember, true); window.removeEventListener('api:mutation-start', start); window.removeEventListener('api:mutation-saved', saved); window.removeEventListener('beforeunload', unload); window.removeEventListener('ui:before-logout', logout); };
  }, [location.pathname]);
  useEffect(() => {
    if (blocker.state === 'blocked') {
      if (window.confirm('Есть несохранённые изменения. Уйти со страницы?')) { sections.current.clear(); setDirty(false); blocker.proceed(); }
      else blocker.reset();
    }
  }, [blocker]);
  return null;
}
