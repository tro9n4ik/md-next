import { useEffect, useId, useLayoutEffect, useRef } from 'react';
import type { ReactNode } from 'react';
import { X } from 'lucide-react';

export default function Drawer({ title, children, onClose, busy = false, dirty = false }: {
  title: string; children: ReactNode; onClose: () => void; busy?: boolean; dirty?: boolean;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  useLayoutEffect(() => {
    const element = dialog.current;
    const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    element?.showModal();
    const overflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => { element?.close(); document.body.style.overflow = overflow; if (opener?.isConnected) opener.focus(); };
  }, []);
  useEffect(() => {
    const scope = dialog.current;
    const notify = (value: boolean) => window.dispatchEvent(new CustomEvent('ui:unsaved-change', {detail: {scope, dirty: value}}));
    notify(dirty);
    return () => { notify(false); };
  }, [dirty]);
  return <dialog ref={dialog} data-unsaved-controlled aria-labelledby={titleId} className="ui-drawer" onCancel={event => {
    event.preventDefault(); if (!busy) onClose();
  }} onClick={event => {
    if (busy || event.target !== event.currentTarget) return;
    const rect = event.currentTarget.getBoundingClientRect();
    if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) onClose();
  }}>
    <header className="flex shrink-0 items-center justify-between gap-4 border-b border-neutral-200 p-5 sm:p-6">
      <h2 id={titleId} className="ui-card-title">{title}</h2>
      <button type="button" aria-label="Закрыть" disabled={busy} onClick={onClose} className="ui-button ui-button-secondary !min-h-9 !p-2"><X size={18} /></button>
    </header>
    <div className="min-h-0 flex-1 overflow-y-auto p-5 sm:p-6">{children}</div>
  </dialog>;
}
