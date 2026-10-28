import { useCallback, useEffect, useId, useLayoutEffect, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import { AlertTriangle, CircleHelp, X } from 'lucide-react';
import { ConfirmationContext } from '../../contexts/ConfirmationContext';
import type { ConfirmationOptions, Confirm } from '../../contexts/ConfirmationContext';

type Request = ConfirmationOptions & { id: number; resolve: (accepted: boolean) => void };

function ConfirmationDialog({ request, settle }: { request: Request; settle: (accepted: boolean) => void }) {
  const dialog = useRef<HTMLDialogElement>(null);
  const cancel = useRef<HTMLButtonElement>(null);
  const titleId = useId();
  const descriptionId = useId();
  useLayoutEffect(() => {
    const element = dialog.current;
    const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    element?.showModal();
    cancel.current?.focus();
    return () => { element?.close(); if (opener?.isConnected) opener.focus(); };
  }, []);
  const Icon = request.tone === 'warning' || request.tone === 'danger' ? AlertTriangle : CircleHelp;
  return <dialog ref={dialog} role="alertdialog" aria-modal="true" aria-labelledby={titleId} aria-describedby={descriptionId} className="ui-confirmation" data-tone={request.tone || 'default'} onCancel={event => { event.preventDefault(); settle(false); }} onClick={event => {
    if (event.target !== event.currentTarget) return;
    const rect = event.currentTarget.getBoundingClientRect();
    if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) settle(false);
  }}>
    <div className="confirmation-content"><span className="confirmation-symbol"><Icon size={24} strokeWidth={1.7} /></span><button type="button" className="confirmation-close" aria-label="Закрыть подтверждение" onClick={() => settle(false)}><X size={19} /></button><h2 id={titleId}>{request.title}</h2><p id={descriptionId}>{request.description}</p></div>
    <div className="confirmation-actions"><button type="button" ref={cancel} className="ui-button ui-button-secondary" onClick={() => settle(false)}>{request.cancelText || 'Отмена'}</button><button type="button" className="ui-button ui-button-primary confirmation-accept" onClick={() => settle(true)}>{request.confirmText || 'Подтвердить'}</button></div>
  </dialog>;
}

export default function ConfirmationProvider({ children }: { children: ReactNode }) {
  const requests = useRef<Request[]>([]);
  const sequence = useRef(0);
  const [current, setCurrent] = useState<Request | null>(null);
  const confirm = useCallback<Confirm>(options => new Promise<boolean>(resolve => {
    const request = { ...options, id: ++sequence.current, resolve };
    requests.current.push(request);
    if (requests.current.length === 1) setCurrent(request);
  }), []);
  const settle = useCallback((id: number, accepted: boolean) => {
    if (requests.current[0]?.id !== id) return;
    const request = requests.current.shift();
    if (!request) return;
    setCurrent(requests.current[0] || null);
    request.resolve(accepted);
  }, []);
  useEffect(() => () => { requests.current.splice(0).forEach(request => request.resolve(false)); }, []);
  return <ConfirmationContext.Provider value={confirm}>{children}{current && <ConfirmationDialog key={current.id} request={current} settle={accepted => settle(current.id, accepted)} />}</ConfirmationContext.Provider>;
}
