/** Real UI + offline API fixtures. Never connects to a panel or node. */
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const root = path.resolve(__dirname, '../..');
const version = fs.readFileSync(path.join(root, 'backend/VERSION'), 'utf8').trim();
const dist = path.join(root, 'frontend/dist');
const assets = path.join(root, 'docs/assets');
const GiB = 1024 ** 3;
const epoch = Date.parse('2026-10-28T07:30:00Z');
const iso = offset => new Date(epoch + offset).toISOString();
const groups = [
  {id:1,name:'Семья',color:'emerald',count:2},
  {id:2,name:'Работа',color:'blue',count:2},
  {id:3,name:'Друзья',color:'violet',count:1},
];
const clients = ['Александр','Мария','Рабочий ноутбук','Команда','Дмитрий','Тестовый клиент'].map((name,i) => {
  const group = groups[Math.floor(i / 2)] || null;
  return {id:i+1,name,email:`demo${i+1}@example.com`,phone:'',is_active:i!==5,
    traffic_total:(i+1)*23.4*GiB,traffic_up:(i+1)*2.4*GiB,traffic_down:(i+1)*21*GiB,traffic_limit:0,
    expires_at:iso((i===1?2:20+i)*86400000),monthly_traffic_limit:200*GiB,
    monthly_traffic_used:(i+1)*12.8*GiB,cdn_monthly_traffic_limit:25*GiB,cdn_monthly_traffic_used:(i+1)*1.2*GiB,
    cdn_quota_exhausted:false,access_allowed:i!==5,blocked_reason:i===5?'disabled':null,
    traffic_period_end:iso(20*86400000),connection_status:i<3?'online':'offline',
    connected_protocols:i<3?[i===2?'awg':'vless_reality_tcp']:[],last_seen_at:iso(-(i+1)*180000),
    can_delete:true,can_pause:true,can_change_group:true,group_id:group?.id||null,group,
    profiles:['vless_reality_tcp','vless_xhttp_reality','hysteria2','awg'].map((kind,k)=>({id:i*10+k+1,kind,is_enabled:true})),
  };
});
// Correct group totals: the last client deliberately has no group.
clients[5].group=null; clients[5].group_id=null;
const nodes = [
  {id:1,name:'Нидерланды',host:'198.51.100.10',port:24443,country_code:'NL',ping_ms:44},
  {id:2,name:'Польша',host:'203.0.113.20',port:443,country_code:'PL',ping_ms:52},
  {id:3,name:'Германия',host:'192.0.2.30',port:24443,country_code:'DE',ping_ms:61},
].map((node,priority)=>({...node,protocol:'vless',priority,is_active:true,is_enabled:true,status:'healthy'}));
const health={status:'ok',checks:['Xray','Nginx','AmneziaWG','Telegram-бот'].map((name,i)=>({key:`service${i}`,name,status:'ok',description:'Демонстрационное состояние службы'}))};
const protocol={profiles:['vless_reality_tcp','vless_xhttp_reality','vless_xhttp_tls','hysteria2','awg'].map(kind=>({kind,enabled:true})),ports:{vless_xhttp_reality:2053,hysteria2:443},paths:{vless_xhttp_reality:'/',vless_xhttp_tls:'/demo-xhttp'},reality:{server_address:'panel.example.test',target:'www.example.com:443',server_name:'www.example.com',fingerprint:'firefox',short_id:'abcd1234',public_key:'A'.repeat(43),private_key_set:true,flow:'xtls-rprx-vision'},modes:{vless_xhttp_reality:'auto',vless_xhttp_tls:'auto'}};
const presets=[
  ['gemini','Gemini и Google AI Studio',['google.com','googleapis.com','googleusercontent.com','gstatic.com','gemini.app.google'],true],
  ['chatgpt','ChatGPT и OpenAI',['openai.com','chatgpt.com','oaistatic.com','oaiusercontent.com','oai.com','sora.com'],true],
  ['claude','Claude (Anthropic)',['anthropic.com','claude.ai','claudeusercontent.com'],false],
  ['ai-services','Perplexity, Copilot и Grok',['perplexity.ai','copilot.microsoft.com','githubcopilot.com','x.ai','grok.com'],false],
  ['hill-climb-racing-2','Hill Climb Racing 2',['fingersoft.com','fingersoft.net','playhcr.com'],true],
].map(([key,title,domains,selected])=>({key,title,domains,description:'Выбранные домены используют выход WARP активной ноды.',state:{total:selected?domains.length:0,missing:0,extra:0}}));
const failures=[];let statsTick=0;
function fixture(url,method) {
  const p=url.pathname;
  if(p==='/api/v1/auth/me')return {id:1,username:'demo_admin',role:'admin',is_active:true,totp_enabled:true,view_all_clients:true,can_issue_subscriptions:true,can_delete_subscriptions:true,can_pause_subscriptions:true};
  if(p==='/api/v1/system/info')return {hostname:'md-next-demo',version};
  if(p==='/api/v1/system/health')return health;
  if(p==='/api/v1/system/stats'){
    const wave=[.55,.7,.62,.9,.78,.58,.73,.95,.67,.81,.76,.9];
    const scale=wave[statsTick++%wave.length];
    return {cpu_percent:7.4,memory:{used:650*1024**2,total:2*GiB},disk:{used:7.3*GiB,total:40*GiB},uptime:9*86400+5*3600,net_speed:{rx_bytes_per_sec:scale*900*1024,tx_bytes_per_sec:scale*540*1024}};
  }
  if(p==='/api/v1/system/traffic')return {last_30_days:{total:492*GiB},today:{total:18.4*GiB}};
  if(p==='/api/v1/clients')return clients;
  if(p==='/api/v1/client-groups')return {groups,total:clients.length,ungrouped_count:1};
  if(p==='/api/v1/events')return [
    ['Клиент добавлен','clients'],['Проверка нод завершена','nodes'],['Правила WARP сохранены','warp'],['Резервная копия создана','backup'],
  ].map(([message,category],i)=>({id:4-i,ts:iso(-i*120000),level:'info',category:({clients:'client',nodes:'node',backup:'system'})[category]||category,message}));
  if(p==='/api/v1/nodes')return nodes;
  if(p==='/api/v1/cluster/route')return {server:{hostname:'md-next-demo',public_ip:'192.0.2.10'},vpn:{xray:true,awg:true},active_node:{...nodes[0],ip:nodes[0].host},manual_direct:false,next_candidate:{...nodes[1],ip:nodes[1].host},failover_mode:'auto',fallback_action:'direct'};
  if(p==='/api/v1/cluster/failover')return {mode:'auto',ping_threshold_ms:300,failure_count:3,interval_s:15,failback:true,failback_stable_checks:3,cooldown_s:30,fallback_action:'direct'};
  if(p==='/api/v1/nodes/invites')return [];
  if(p==='/api/v1/warp/status')return {enabled:true,controls_ready:true,node_id:1,name:'Нидерланды',ipv4_only:true};
  if(p==='/api/v1/warp/test'&&method==='POST')return {ip:'198.51.100.40',country:'NL',warp:'on'};
  if(p==='/api/v1/warp/presets')return {presets};
  if(p==='/api/v1/warp/gemini/monitor')return {enabled:true,node_id:1,node_name:'Нидерланды',result:{status:'page_loaded',message:'Страница Gemini открылась. Доступ к генерации и страна аккаунта не проверены.',google_country:'DE',google_country_source:'google_signin_region',google_region_status:'detected',google_region_message:'Регион по полю страницы входа Google, не страна аккаунта.',country:'NL',ip:'198.51.100.40',warp:'on',checked_at:epoch/1000}};
  if(p==='/api/v1/settings/protocols')return protocol;
  if(p==='/api/v1/settings/protocols/sni/check'&&method==='POST')return {checked_at:iso(0),note:'Демонстрационные результаты проверки.',results:[{domain:'www.example.com',target:'www.example.com:443',eligible:true,ip:'203.0.113.50',tls_version:'TLSv1.3',alpn:'h2',latency_ms:29,http_status:200,reason:'Прошёл базовую проверку'},{domain:'redirect.example.com',target:'redirect.example.com:443',eligible:false,http_status:301,reason:'Сайт перенаправляет запрос; выберите конечный домен'}]};
  if(p==='/api/v1/users')return [{id:2,username:'operator',is_active:true,view_all_clients:false,totp_enabled:true,can_issue_subscriptions:true,can_delete_subscriptions:false,can_pause_subscriptions:true},{id:3,username:'support',is_active:true,view_all_clients:true,totp_enabled:true,can_issue_subscriptions:true,can_delete_subscriptions:false,can_pause_subscriptions:false}];
  if(p==='/api/v1/settings/telegram')return {token_set:true,token_masked:'скрыт',admin_id:'123456789',proxy_url:'',notify_node_down:true,notify_failover:true,notify_quota:true,bot_status:'running',bot_error:null,use_node:true,node_id:1};
  if(p==='/api/v1/operations/templates')return [{id:'demo_month',name:'Месяц · 200 ГБ',period:'month',monthly_traffic_limit:200*GiB}];
  throw new Error(`Missing fixture: ${method} ${p}`);
}
(async()=>{
  const server=http.createServer((req,res)=>{
    const pathname=new URL(req.url,'http://localhost').pathname;
    const filename=path.resolve(dist,'.'+(pathname==='/'?'/index.html':pathname));
    if(!filename.startsWith(dist+path.sep)){res.writeHead(403).end();return;}
    try{res.setHeader('Content-Type',filename.endsWith('.js')?'text/javascript':filename.endsWith('.css')?'text/css':filename.endsWith('.svg')?'image/svg+xml':'text/html');res.end(fs.readFileSync(filename));}catch{res.writeHead(404).end();}
  });
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  const base=`http://127.0.0.1:${server.address().port}`;
  const runtime=process.env.CHROMIUM_RUNTIME_DIR;
  const browser=await chromium.launch({headless:true,...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE?{executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE}:{}),args:['--no-sandbox','--disable-dev-shm-usage'],env:{...process.env,...(runtime?{LD_LIBRARY_PATH:runtime,FONTCONFIG_PATH:path.join(runtime,'fonts')}:{})}});
  const captures={};
  try{
    const context=await browser.newContext({viewport:{width:1600,height:1100},deviceScaleFactor:1,timezoneId:'Asia/Krasnoyarsk',locale:'ru-RU',colorScheme:'light'});
    await context.addInitScript(()=>{localStorage.setItem('token','demo-only');localStorage.setItem('theme','light');localStorage.setItem('sidebar-collapsed','false');});
    await context.route('**/*',async route=>{
      const url=new URL(route.request().url());
      if(url.origin!==base){failures.push('External request blocked: '+url.origin);return route.abort();}
      if(!url.pathname.startsWith('/api/'))return route.continue();
      try{await route.fulfill({status:200,contentType:'application/json',body:JSON.stringify(fixture(url,route.request().method()))});}catch(error){failures.push(error.message);await route.fulfill({status:501,contentType:'application/json',body:'{"detail":"Missing demo fixture"}'});}
    });
    const page=await context.newPage();page.on('pageerror',error=>failures.push(error.message));
    await page.clock.install({time:new Date(epoch)});
    async function open(route){await page.goto(base+'/#'+route);await page.getByRole('heading',{level:1}).waitFor();await page.waitForLoadState('networkidle');await page.evaluate(()=>document.fonts.ready);}
    async function capture(name,route,{drawer=false}={}){
      await page.evaluate(()=>window.scrollTo(0,0));
      await page.clock.runFor(50);
      if(!drawer){assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),`Horizontal overflow: ${name}`);}
      assert.equal(await page.getByRole('alert').count(),0,`Error shown in ${name}`);
      await page.evaluate(({version,drawer})=>{
        let note=document.getElementById('demo-capture-label');
        if(!note){note=document.createElement('div');note.id='demo-capture-label';(drawer?document.querySelector('dialog[open]'):document.body).append(note);}
        note.style.display='none';
        const imageHeight=Math.max(innerHeight,document.documentElement.scrollHeight);
        note.textContent=`Демонстрационные данные · MD-Next ${version}`;
        Object.assign(note.style,{position:'absolute',display:'block',top:`${imageHeight-34}px`,bottom:'auto',right:'14px',zIndex:'99999',padding:'5px 10px',borderRadius:'8px',fontSize:'11px',fontFamily:'sans-serif',background:'#123a30',color:'#fff',pointerEvents:'none'});
      },{version,drawer});
      const filename=path.join(assets,name);await page.screenshot({path:filename,type:'jpeg',quality:90,fullPage:true,animations:'disabled'});
      captures[name]={route,theme:await page.evaluate(()=>document.documentElement.dataset.theme),viewport:{width:1600,height:1100}};
      console.log('Captured '+name);
    }
    await open('/');
    for(let i=0;i<12;i++){await page.clock.setSystemTime(new Date(epoch+i*5000));await page.getByRole('button',{name:'Обновить',exact:true}).click();await page.waitForLoadState('networkidle');}
    await capture('dashboard-demo.jpg','/');
    await page.getByRole('button',{name:'Тёмная тема',exact:true}).click();await capture('dashboard-dark-demo.jpg','/');
    await page.getByRole('button',{name:'Светлая тема',exact:true}).click();
    await open('/clients');await page.getByText('Рабочий ноутбук',{exact:true}).waitFor();await capture('clients-demo.jpg','/clients');
    await open('/nodes');await page.getByRole('button',{name:'Добавить ноду',exact:true}).waitFor();await capture('nodes-demo.jpg','/nodes');
    await open('/warp');await page.getByText('Доступ к Gemini не подтверждён',{exact:true}).waitFor();await page.getByRole('button',{name:'Проверить IPv4',exact:true}).click();await page.getByText('198.51.100.40',{exact:true}).waitFor();await page.locator('.warp-result-details > summary').click();await capture('warp-demo.jpg','/warp');
    await open('/protocols');await page.getByLabel('Reality SNI',{exact:true}).waitFor();await page.locator('[data-sni-scanner] > summary').click();await page.getByLabel('Домены для проверки').fill('www.example.com\nredirect.example.com');await page.getByRole('button',{name:'Проверить домены',exact:true}).click();await page.getByRole('button',{name:'Выбрать SNI: www.example.com'}).waitFor();await capture('protocols-demo.jpg','/protocols');
    await open('/settings/users');await page.getByRole('button',{name:'Настроить operator',exact:true}).click();await page.getByRole('dialog').waitFor();await capture('subadmins-demo.jpg','/settings/users',{drawer:true});await page.getByRole('dialog').getByRole('button',{name:'Закрыть',exact:true}).click();
    await open('/settings/telegram');await page.getByText('Шаблоны подписок',{exact:true}).waitFor();await capture('telegram-demo.jpg','/settings/telegram');
    assert.deepEqual(failures,[],'All fixtures and runtime checks must pass');
    const files=Object.fromEntries(Object.keys(captures).sort().map(name=>[name,crypto.createHash('sha256').update(fs.readFileSync(path.join(assets,name))).digest('hex')]));
    fs.writeFileSync(path.join(assets,'screenshots.json'),JSON.stringify({version,source_base_commit:require('node:child_process').execFileSync('git',['rev-parse','HEAD'],{cwd:root,encoding:'utf8'}).trim(),demo:true,generated_from_worktree:true,captured_at:iso(0),files,captures},null,2)+'\n');
    await context.close();
  }finally{await browser.close();server.close();}
})().catch(error=>{console.error(error.stack);process.exit(1);});
