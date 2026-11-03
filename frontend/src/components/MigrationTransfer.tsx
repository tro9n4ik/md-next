import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Download, Server, ShieldCheck } from 'lucide-react';
import { apiFetch } from '../utils/api';
import { useConfirm } from '../contexts/ConfirmationContext';

type Job = {phase: string; stage?: string; message: string; error_code?: string; exit_code?: number; updated_at?: number; history?: {stage:string; message:string; at:number}[]; target_ready?: boolean; source_fenced: boolean; activation_started: boolean; target?: {host: string; port: number; fingerprint: string}};
type Package = {name: string; size: number};
const active = new Set(['connecting','preparing','exporting','restoring','verifying']);
async function read<T>(path: string, body?: unknown): Promise<T> {
  const response = await apiFetch('/api/v1/migration'+path, body ? {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)} : {});
  const data = await response.json();
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : data.detail?.code && data.detail?.message ? `${data.detail.code}: ${data.detail.message}` : 'Операция переезда не выполнена');
  return data;
}
export default function MigrationTransfer() {
  const cache=useQueryClient(); const confirm=useConfirm();
  const [host,setHost]=useState(''); const [port,setPort]=useState('22'); const [email,setEmail]=useState('');
  const [fingerprint,setFingerprint]=useState(''); const [trusted,setTrusted]=useState(false);
  const [sshPassword,setSshPassword]=useState(''); const [password,setPassword]=useState(''); const [repeat,setRepeat]=useState('');
  const [protocols,setProtocols]=useState(false); const [notice,setNotice]=useState('');
  const job=useQuery<Job>({queryKey:['migration-job'],queryFn:()=>read('/jobs'),refetchInterval:q=>active.has(q.state.data?.phase || '')?3000:15000,retry:false});
  const packages=useQuery<Package[]>({queryKey:['migration-packages'],queryFn:()=>read('/packages'),retry:false});
  const scan=useMutation({mutationFn:()=>read<{fingerprint:string}>('/fingerprint',{host,port:Number(port)}),onSuccess:data=>{setFingerprint(data.fingerprint);setTrusted(false);},onError:e=>setNotice(e.message)});
  const launch=useMutation({mutationFn:(action:string)=>read<Job>('/jobs',{action,host,port:Number(port),email,fingerprint,ssh_password:sshPassword,password,confirm:true,confirm_protocol_tests:protocols}),onSuccess:data=>{cache.setQueryData(['migration-job'],data);setNotice('Этап запущен. Закрытие страницы не останавливает работу.');},onError:e=>setNotice(e.message),onSettled:()=>{setSshPassword('');setPassword('');setRepeat('');}});
  const pack=useMutation({mutationFn:()=>read<Package>('/packages',{password}),onSuccess:()=>{cache.invalidateQueries({queryKey:['migration-packages']});setNotice('Пробный зашифрованный комплект создан. Сохраните его пароль отдельно.');},onError:e=>setNotice(e.message),onSettled:()=>{setPassword('');setRepeat('');}});
  const busy=active.has(job.data?.phase || '') || launch.isPending || pack.isPending || scan.isPending;
  const valid=host.trim() && /^\d+$/.test(port) && Number(port)>0 && Number(port)<=65535 && trusted && fingerprint && sshPassword && password.length>=12 && password===repeat;
  async function start(action:string) {
    const descriptions:Record<string,string>={prepare:'На чистом новом VPS будут установлены компоненты и восстановлена пробная копия. Рабочий сервер и DNS не меняются.',finalize:'Клиентские подключения на старом VPS прервутся. Службы остановятся, панель перейдёт в режим чтения. Финальный снимок будет перенесён на новый сервер. DNS пока не меняется.',activate:'Новый VPS начнёт обслуживать клиентов и Telegram-бота. Старый останется изолированным. Переключите DNS вручную; автоматическая отмена после активации недоступна.',cancel:'Целевой сервер вернётся к исходной изолированной установке. Старые службы возобновят работу. Доступно только до начала активации; DNS нужно проверить вручную.'};
    if (await confirm({title:action==='finalize'?'Начать финальный перенос?':action==='activate'?'Активировать новый VPS?':action==='cancel'?'Отменить переезд?':'Подготовить новый VPS?',description:descriptions[action],confirmText:'Подтвердить',tone:action==='prepare'?'default':'warning'})) launch.mutate(action);
  }
  async function download(name:string) {
    try {const response=await apiFetch('/api/v1/migration/packages/'+name);if(!response.ok)throw new Error('Не удалось скачать комплект');const url=URL.createObjectURL(await response.blob());const link=document.createElement('a');link.href=url;link.download=name;link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}catch(e){setNotice((e as Error).message);}
  }
  return <section className="ui-card ui-panel" data-unsaved-controlled>
    <h3 className="ui-card-title flex items-center gap-2"><Server size={19}/>Подготовка и перенос</h3>
    <p className="migration-note">Нужен чистый Debian/Ubuntu VPS и SSH-доступ root. Механизм экспериментальный: сквозной запуск на отдельном VPS ещё не проверен.</p>
    <div role="status" aria-live="polite" className="migration-job-status"><b>{job.data?.source_fenced?'Исходная панель изолирована':'Состояние переезда'}</b><p>{job.data?.message || job.error?.message || 'Получаем состояние…'}</p>
      {job.data?.error_code && <p><strong>Код: {job.data.error_code}</strong>{job.data.exit_code !== undefined && ` · завершение процесса: ${job.data.exit_code}`}</p>}
      {job.data?.stage && <p>Этап: <code>{job.data.stage}</code></p>}
      {job.data?.updated_at && <p>Последнее сообщение: <time>{new Date(job.data.updated_at*1000).toLocaleTimeString()}</time></p>}
      {active.has(job.data?.phase || '') && <p>Работа выполняется в фоне. Повторный запуск не нужен.</p>}
      {job.error && job.data && <p role="alert">Не удалось обновить состояние. Последние данные могут быть устаревшими.</p>}
      {job.data?.activation_started && <p>Активация уже началась. Перед любым возвратом установите, какой сервер принял новые данные.</p>}
    </div>
    {!!job.data?.history?.length && <details open className="migration-job-status"><summary>Журнал подготовки VPS и переноса</summary><ol aria-label="Журнал переезда" style={{maxHeight:280,overflowY:'auto'}}>{job.data.history.map((item,index)=><li key={`${item.at}-${index}`}><time>{new Date(item.at*1000).toLocaleTimeString()}</time> · {item.message}</li>)}</ol><p className="migration-note">Показываются безопасные сообщения этапов. Полный вывод с возможными ключами и паролями доступен только root на новом VPS.</p></details>}
    <fieldset disabled={busy} className="migration-target-fields">
      <label>IP нового VPS<input className="migration-input" value={host} onChange={e=>{setHost(e.target.value);setFingerprint('');setTrusted(false);setSshPassword('');}} placeholder="Публичный IPv4 или IPv6" autoComplete="off"/></label>
      <label>SSH-порт<input className="migration-input" type="number" min={1} max={65535} value={port} onChange={e=>{setPort(e.target.value);setFingerprint('');setTrusted(false);}}/></label>
      <label>Email администратора<input className="migration-input" type="email" value={email} onChange={e=>setEmail(e.target.value)} placeholder="Для подготовки новой установки"/></label>
      <div className="migration-full-row flex flex-wrap gap-3"><button className="ui-button ui-button-secondary" disabled={!host || scan.isPending} onClick={()=>{setNotice('');scan.mutate();}}><ShieldCheck size={16}/>{scan.isPending?'Получаем ключ…':'Получить SSH-отпечаток'}</button>{job.data?.target && <button className="ui-button ui-button-secondary" onClick={()=>{setHost(job.data!.target!.host);setPort(String(job.data!.target!.port));setFingerprint(job.data!.target!.fingerprint);setTrusted(false);}}>Использовать подготовленный VPS</button>}</div>
      {fingerprint && <div className="migration-full-row"><code className="migration-fingerprint">{fingerprint}</code><label className="migration-checkbox"><input type="checkbox" checked={trusted} onChange={e=>setTrusted(e.target.checked)}/>Я сверил отпечаток с консолью VPS или данными провайдера</label><p className="migration-note">Само получение ключа не подтверждает подлинность сервера. При несовпадении закреплённого ключа пароль не отправляется.</p></div>}
      <label>SSH-пароль root<input className="migration-input" type="password" value={sshPassword} onChange={e=>setSshPassword(e.target.value)} autoComplete="new-password"/></label>
      <label>Пароль комплекта<input className="migration-input" type="password" minLength={12} maxLength={256} value={password} onChange={e=>setPassword(e.target.value)} autoComplete="new-password"/></label>
      <label>Повторите пароль комплекта<input className="migration-input" type="password" value={repeat} onChange={e=>setRepeat(e.target.value)} autoComplete="new-password"/></label>
      <p className="migration-note migration-full-row">Используйте один пароль комплекта на всех этапах и сохраните его отдельно. После запроса поля паролей очищаются. SSH-пароль временно передаётся защищённому фоновому процессу и удаляется после чтения; в журнал не записывается.</p>
      <div className="migration-full-row flex flex-wrap gap-3"><button className="ui-button ui-button-primary" disabled={!valid || !email || job.data?.phase==='unavailable' || !!job.data?.target || job.data?.source_fenced || job.data?.activation_started} onClick={()=>start('prepare')}>1. Подготовить VPS</button><button className="ui-button ui-button-secondary" disabled={!valid || !job.data?.target_ready || job.data.activation_started} onClick={()=>start('finalize')}>2. Финальная синхронизация</button></div>
      <label className="migration-checkbox migration-full-row"><input type="checkbox" checked={protocols} onChange={e=>setProtocols(e.target.checked)}/>Я проверил каждый используемый протокол с внешнего устройства на новом IP</label>
      <div className="migration-full-row flex flex-wrap gap-3"><button className="ui-button ui-button-primary" disabled={!valid || !protocols || job.data?.phase!=='awaiting_activation' || job.data.activation_started} onClick={()=>start('activate')}>3. Активировать новый VPS</button><button className="ui-button ui-button-secondary" disabled={!valid || !job.data?.target || job.data.activation_started} onClick={()=>start('cancel')}>Отменить до активации</button></div>
    </fieldset>
    <div className="migration-footer"><div><b>Зашифрованный комплект отдельно</b><p>Полная база и конфигурации, включая ключи. Снимок работающего сервера годится для пробного восстановления; для активации нужен финальный снимок после остановки записей.</p></div><button className="ui-button ui-button-secondary" disabled={busy || password.length<12 || password!==repeat || job.data?.phase==='unavailable' || job.data?.source_fenced} onClick={()=>pack.mutate()}>Создать комплект</button></div>
    {packages.data?.map(item=><div className="migration-package" key={item.name}><span>{item.name} · {(item.size/1024/1024).toFixed(1)} МБ</span><button className="ui-button ui-button-secondary" onClick={()=>download(item.name)}><Download size={15}/>Скачать</button></div>)}
    {notice && !(notice.startsWith('Этап запущен') && ['error','reboot_required','prepared','awaiting_activation','activated','cancelled'].includes(job.data?.phase || '')) && <p role="status" className="migration-note">{notice}</p>}
  </section>;
}
