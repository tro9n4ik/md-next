import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Server } from 'lucide-react';
import { apiFetch } from '../utils/api';

type Job={phase:string;stage?:string;message:string;error_code?:string;managed?:boolean;source_fenced?:boolean;activation_started?:boolean;can_reconcile?:boolean;target?:{host:string;port:number;fingerprint:string}};
const active=new Set(['connecting','preparing','rebooting','exporting','restoring','verifying','activating','acknowledging','reconciling','cancelling']);
const stageLabels:Record<string,string>={source_preflight:'Проверка исходной установки',connecting:'Подключение к новому VPS',preparing:'Подготовка VPS',rebooting:'Перезагрузка VPS',exporting:'Создание защищённого снимка',restoring:'Восстановление снимка',verifying:'Проверка нового VPS',activating:'Активация',acknowledging:'Подтверждение завершения',reconciling:'Проверка результата активации',cancelling:'Отмена подготовки',completed:'Завершено'};
async function read<T>(path:string,body?:unknown):Promise<T>{
 const response=await apiFetch('/api/v1/migration'+path,body?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}:{});
 const data=await response.json();
 if(!response.ok)throw new Error(typeof data.detail==='string'?data.detail:data.detail?.message || 'Не удалось выполнить действие');
 return data;
}
export default function MigrationTransfer(){
 const cache=useQueryClient();
 const [host,setHost]=useState('');const [password,setPassword]=useState('');const [notice,setNotice]=useState('');
 const job=useQuery<Job>({queryKey:['migration-job'],queryFn:()=>read('/jobs'),refetchInterval:q=>active.has(q.state.data?.phase || '')?3000:15000,retry:false});
 const launch=useMutation({mutationFn:async(action:string)=>{
  let target=job.data?.target;
  if(action==='prepare'){
   const key=await read<{fingerprint:string}>('/fingerprint',{host:host.trim(),port:22});
   target={host:host.trim(),port:22,fingerprint:key.fingerprint};
  }
  if(!target)throw new Error('Не найден подготовленный VPS');
  return read<Job>('/jobs',{...target,action,confirm:true,confirm_protocol_tests:action==='commit',...(action==='prepare'?{ssh_password:password}:{})});
 },onSuccess:data=>{cache.setQueryData(['migration-job'],data);setNotice('');},onError:e=>setNotice(e.message),onSettled:()=>setPassword('')});
 const busy=launch.isPending || active.has(job.data?.phase || '');
 const ready=['prepared','awaiting_activation'].includes(job.data?.phase || '');
 const bound=!!job.data?.target;
 const locked=!!job.data?.activation_started;
 return <section className="ui-card ui-panel" data-unsaved-controlled>
  <h3 className="ui-card-title flex items-center gap-2"><Server size={19}/>Переезд на новый VPS</h3>
  <p className="migration-note">Укажите чистый Debian/Ubuntu VPS. Мастер установит проверенный билд, создаст защищённый бэкап и восстановит данные. Текущий сервер продолжит работать до подтверждения.</p>
  <div className="migration-job-status" role="status" aria-live="polite">
   <b>{job.data?.phase==='activated'?'Переезд завершён':ready?'Новый VPS готов к переключению':'Состояние переезда'}</b>
   <p>{job.data?.message || job.error?.message || 'Укажите новый сервер'}</p>
   {job.data?.stage && stageLabels[job.data.stage] && <p className="migration-note">Этап: {stageLabels[job.data.stage]}</p>}
   {job.data?.error_code && <p>Код: <strong>{job.data.error_code}</strong></p>}
   {busy && <div className="migration-progress" role="progressbar" aria-label="Выполнение этапа"><span/></div>}
   {job.error && <p role="alert">Не удалось обновить статус. Не запускайте повторное действие до восстановления связи.</p>}
  </div>
  {!bound && !locked && <fieldset disabled={busy} className="migration-target-fields">
   <label>IP нового VPS<input className="migration-input" value={host} onChange={e=>setHost(e.target.value)} autoComplete="off" placeholder="Публичный IP"/></label>
   <label>SSH-пароль root<input className="migration-input" type="password" value={password} onChange={e=>setPassword(e.target.value)} autoComplete="new-password"/></label>
   <p className="migration-note migration-full-row">SSH-порт 22. Ключ сервера закрепляется при первом подключении; при его смене мастер остановится. Получение ключа автоматически не является независимой проверкой сервера.</p>
   <div className="migration-full-row"><button className="ui-button ui-button-primary" disabled={busy || !host.trim() || !password || !!job.error || job.data?.phase==='unavailable'} onClick={()=>launch.mutate('prepare')}>Начать переезд</button></div>
  </fieldset>}
  {bound && !locked && <section className="migration-confirmation" aria-label="Подтверждение переезда">
   <h4 className="ui-card-title">{ready?'Подтвердить переключение?':'Подготовка нового VPS'}</h4>
   <p className="migration-note">Нажимая «Подтвердить», вы подтверждаете проверку VPN-подключений на новом IP. Мастер выполнит финальную синхронизацию и активирует новый сервер. Возможен короткий перерыв.</p>
   <p><strong>После успешного подтверждения замените IP в DNS-записях ваших доменов на {job.data?.target?.host}.</strong></p>
   {!job.data?.managed && <p role="alert">Это попытка старой версии мастера. Для её отмены требуется прежний пароль комплекта или помощь администратора.</p>}
   <div className="migration-confirmation-actions"><button className="ui-button ui-button-primary" disabled={busy || !ready || !job.data?.managed || !!job.error} onClick={()=>launch.mutate('commit')}>Подтвердить</button><button className="ui-button ui-button-secondary" disabled={busy || !job.data?.managed || !!job.error} onClick={()=>launch.mutate('cancel')}>Отменить</button></div>
  </section>}
  {locked && job.data?.phase!=='activated' && <div className="migration-confirmation">
   <p role="alert">Активация уже началась. Проверка завершения не запускает активацию повторно. Автоматическая отмена запрещена.</p>
   {job.data?.can_reconcile ? <button className="ui-button ui-button-secondary" disabled={busy || !!job.error} onClick={()=>launch.mutate('reconcile')}>Проверить завершение</button> : <p className="migration-note">Для попытки старой версии или утраченной сессии требуется ручная проверка состояния серверов.</p>}
  </div>}
  {notice && <p role="alert" className="migration-note">{notice}</p>}
 </section>;
}
