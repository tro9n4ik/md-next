import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Server } from 'lucide-react';
import { apiFetch } from '../utils/api';
import { useConfirm } from '../contexts/ConfirmationContext';

type Job = {phase: string; action?: string; host?: string; port?: number; stage?: string; message: string; error_code?: string; exit_code?: number; updated_at?: number; history?: {stage:string; message:string; at:number}[]; target_ready?: boolean; source_fenced: boolean; activation_started: boolean; target?: {host: string; port: number; fingerprint: string}};
const active = new Set(['connecting','preparing','rebooting','exporting','restoring','verifying']);
async function read<T>(path: string, body?: unknown): Promise<T> {
  const response = await apiFetch('/api/v1/migration'+path, body ? {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)} : {});
  const data = await response.json();
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : data.detail?.code && data.detail?.message ? `${data.detail.code}: ${data.detail.message}` : 'Операция переезда не выполнена');
  return data;
}
export default function MigrationTransfer() {
  const cache=useQueryClient(); const confirm=useConfirm();
  const [hostInput,setHost]=useState<string|null>(null); const [portInput,setPort]=useState<string|null>(null); const [email,setEmail]=useState('');
  const [fingerprintInput,setFingerprint]=useState<string|null>(null); const [trusted,setTrusted]=useState(false);
  const [sshPassword,setSshPassword]=useState(''); const [password,setPassword]=useState(''); const [repeat,setRepeat]=useState('');
  const [protocols,setProtocols]=useState(false); const [notice,setNotice]=useState('');
  const job=useQuery<Job>({queryKey:['migration-job'],queryFn:()=>read('/jobs'),refetchInterval:q=>active.has(q.state.data?.phase || '')?3000:15000,retry:false});
  const host=hostInput ?? job.data?.target?.host ?? job.data?.host ?? '';
  const port=portInput ?? String(job.data?.target?.port ?? job.data?.port ?? 22);
  const fingerprint=fingerprintInput ?? job.data?.target?.fingerprint ?? '';
  const scan=useMutation({mutationFn:()=>read<{fingerprint:string}>('/fingerprint',{host,port:Number(port)}),onSuccess:data=>{setFingerprint(data.fingerprint);setTrusted(false);},onError:e=>setNotice(e.message)});
  const launch=useMutation({mutationFn:(action:string)=>read<Job>('/jobs',{action,host,port:Number(port),email,fingerprint,ssh_password:sshPassword,password,confirm:true,confirm_protocol_tests:protocols}),onSuccess:data=>{cache.setQueryData(['migration-job'],data);setNotice('Этап запущен. Закрытие страницы не останавливает работу.');},onError:e=>setNotice(e.message),onSettled:()=>{setSshPassword('');setPassword('');setRepeat('');}});
  const busy=active.has(job.data?.phase || '') || launch.isPending || scan.isPending;
  const valid=host.trim() && /^\d+$/.test(port) && Number(port)>0 && Number(port)<=65535 && trusted && fingerprint && sshPassword && password.length>=12 && password===repeat;
  async function start(action:string) {
    const descriptions:Record<string,string>={prepare:'На новом VPS обновится ОС. При необходимости мастер перезагрузит его и дождётся SSH. Затем установит чистый проверенный билд GitHub, создаст зашифрованный бэкап и выполнит пробное восстановление в изолированной панели. Рабочий сервер и DNS продолжат обслуживать подключения.',finalize:'Клиентские подключения на старом VPS прервутся. Службы остановятся, панель перейдёт в режим чтения. Финальный снимок будет перенесён на новый сервер. DNS пока не меняется.',activate:'Новый VPS начнёт обслуживать клиентов и Telegram-бота. Старый останется изолированным. Переключите DNS вручную; автоматическая отмена после активации недоступна.',cancel:'Целевой сервер вернётся к исходной изолированной установке. Старые службы возобновят работу. Доступно только до начала активации; DNS нужно проверить вручную.'};
    if (await confirm({title:action==='finalize'?'Начать финальный перенос?':action==='activate'?'Активировать новый VPS?':action==='cancel'?'Отменить переезд?':'Подготовить новый VPS?',description:descriptions[action],confirmText:'Подтвердить',tone:action==='prepare'?'default':'warning'})) launch.mutate(action);
  }
  const completed=job.data?.phase==='activated'?3:job.data?.phase==='awaiting_activation' || job.data?.action==='activate'?2:job.data?.phase==='prepared' || job.data?.action==='finalize'?1:0;
  const missing=!host.trim()?'Укажите IP нового VPS':!email && !job.data?.target?'Укажите email администратора':!fingerprint?'Заполните адрес и порт для проверки SSH-отпечатка':!trusted?'Подтвердите SSH-отпечаток':!sshPassword?'Введите SSH-пароль':password.length<12?'Пароль комплекта: минимум 12 символов':password!==repeat?'Пароли комплекта не совпадают':'';
  return <section className="ui-card ui-panel" data-unsaved-controlled>
    <h3 className="ui-card-title flex items-center gap-2"><Server size={19}/>Подготовка и перенос</h3>
    <p className="migration-note">Нужен чистый Debian/Ubuntu VPS и SSH-доступ root. Перед активацией проверьте подключения на новом IP. Сквозное испытание нового механизма ещё не завершено.</p>
    <ol className="migration-steps" aria-label="Этапы переноса">{['Подготовка VPS','Финальная синхронизация','Активация'].map((title,i)=><li key={title} data-complete={i<completed} aria-current={i===completed?'step':undefined}><span>{i+1}</span>{title}</li>)}</ol>
    <div role="status" aria-live="polite" className="migration-job-status"><b>{job.data?.source_fenced?'Исходная панель изолирована':'Состояние переезда'}</b><p>{job.data?.message || job.error?.message || 'Получаем состояние…'}</p>
      {job.data?.error_code && <p><strong>Код: {job.data.error_code}</strong>{job.data.exit_code !== undefined && ` · завершение процесса: ${job.data.exit_code}`}</p>}
      {active.has(job.data?.phase || '') && <div className="migration-progress" role="progressbar" aria-label="Выполнение этапа"><span/></div>}
      {job.data?.updated_at && <p>Последнее сообщение: <time>{new Date(job.data.updated_at*1000).toLocaleTimeString()}</time></p>}
      {active.has(job.data?.phase || '') && <p>Работа выполняется в фоне. Повторный запуск не нужен.</p>}
      {job.error && job.data && <p role="alert">Не удалось обновить состояние. Последние данные могут быть устаревшими.</p>}
      {job.data?.phase==='error' && job.data.target && !job.data.activation_started && <p>Для новой чистой попытки используйте отмену до активации ниже.</p>}
      {job.data?.activation_started && <p>Активация уже началась. Перед любым возвратом установите, какой сервер принял новые данные.</p>}
    </div>
    <fieldset disabled={busy} className="migration-target-fields">
      <label>IP нового VPS<input className="migration-input" value={host} onBlur={()=>{if(host.trim() && !fingerprint)scan.mutate();}} onChange={e=>{setHost(e.target.value);setFingerprint('');setTrusted(false);setSshPassword('');}} placeholder="Публичный IPv4 или IPv6" autoComplete="off"/></label>
      <label>SSH-порт<input className="migration-input" type="number" min={1} max={65535} value={port} onBlur={()=>{if(host.trim() && !fingerprint)scan.mutate();}} onChange={e=>{setPort(e.target.value);setFingerprint('');setTrusted(false);}}/></label>
      <label>Email администратора<input className="migration-input" type="email" value={email} onChange={e=>setEmail(e.target.value)} placeholder="Для подготовки новой установки"/></label>
      <p className="migration-note migration-full-row">{scan.isPending?'Получаем SSH-отпечаток…':'Отпечаток загружается после заполнения адреса и порта.'}</p>
      {fingerprint && <div className="migration-full-row"><code className="migration-fingerprint">{fingerprint}</code><label className="migration-checkbox"><input type="checkbox" checked={trusted} onChange={e=>setTrusted(e.target.checked)}/>Я сверил отпечаток с консолью VPS или данными провайдера</label><p className="migration-note">Само получение ключа не подтверждает подлинность сервера. При несовпадении закреплённого ключа пароль не отправляется.</p></div>}
      <label>SSH-пароль root<input className="migration-input" type="password" value={sshPassword} onChange={e=>setSshPassword(e.target.value)} autoComplete="new-password"/></label>
      <label>Пароль комплекта<input className="migration-input" type="password" minLength={12} maxLength={256} value={password} onChange={e=>setPassword(e.target.value)} autoComplete="new-password"/></label>
      <label>Повторите пароль комплекта<input className="migration-input" type="password" value={repeat} onChange={e=>setRepeat(e.target.value)} autoComplete="new-password"/></label>
      <p className="migration-note migration-full-row">Используйте один пароль комплекта на всех этапах и сохраните его отдельно. После запроса поля паролей очищаются. SSH-пароль временно передаётся защищённому фоновому процессу и удаляется после чтения; в журнал не записывается.</p>
      <div className="migration-full-row flex flex-wrap gap-3"><button className="ui-button ui-button-primary" disabled={!valid || !email || job.data?.phase==='unavailable' || !!job.data?.target || job.data?.source_fenced || job.data?.activation_started} onClick={()=>start('prepare')}>1. Подготовить VPS</button><button className="ui-button ui-button-secondary" disabled={!valid || !job.data?.target_ready || job.data.activation_started} onClick={()=>start('finalize')}>2. Финальная синхронизация</button></div>
      <label className="migration-checkbox migration-full-row"><input type="checkbox" checked={protocols} onChange={e=>setProtocols(e.target.checked)}/>Я проверил каждый используемый протокол с внешнего устройства на новом IP</label>
      <div className="migration-full-row flex flex-wrap gap-3"><button className="ui-button ui-button-primary" disabled={!valid || !protocols || job.data?.phase!=='awaiting_activation' || job.data.activation_started} onClick={()=>start('activate')}>3. Активировать новый VPS</button><button className="migration-cancel-link" disabled={!valid || !job.data?.target || job.data.activation_started} onClick={()=>start('cancel')}>Отменить до активации</button></div>
    </fieldset>
    {missing && !busy && <p className="migration-note" role="status">{missing}</p>}
    {notice && !(notice.startsWith('Этап запущен') && ['error','reboot_required','prepared','awaiting_activation','activated','cancelled'].includes(job.data?.phase || '')) && <p role="status" className="migration-note">{notice}</p>}
  </section>;
}
