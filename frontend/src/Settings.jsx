import React,{useEffect,useState} from 'react';
import {action} from './api.js';
import {Button,FileField,Errors,Activity,Spinner,Tabs,timeText} from './components.jsx';
import GoogleConnection from './GoogleConnection.jsx';

const today=()=>new Date().toLocaleDateString('sv-SE',{timeZone:'Europe/Moscow'});

export default function Settings({data,refresh,wizard=false,onFinish,onRestart,onPreviewAppearance}){
  const [form,setForm]=useState(data.config),[confirmed,setConfirmed]=useState(Boolean(data.target_allowed));
  const [error,setError]=useState(''),[message,setMessage]=useState(''),[step,setStep]=useState(0);
  const [root,setRoot]=useState(data.root),[pending,setPending]=useState(false),[checking,setChecking]=useState('');
  const [tab,setTab]=useState(data.state.operation==='google'?'google':'files');
  const [auth,setAuth]=useState({api_id:'',api_hash:'',phone:'',code:'',password:''});
  useEffect(()=>{onPreviewAppearance?.(form.appearance||'classic');return()=>onPreviewAppearance?.(null)},[form.appearance,onPreviewAppearance]);
  const busy=pending||['preparing','writing','checking','connecting'].includes(data.state.phase);
  const set=(key,value)=>{setForm(f=>({...f,[key]:value}));setError('');setMessage('');if(['target','target_mode'].includes(key))setConfirmed(false)};

  async function save(){
    setError('');setMessage('');setPending(true);
    try{
      let values=form,copyConfirmed=confirmed;
      if(wizard&&step===0&&root!==data.root){
        const moved=await action('root',{path:root});
        values=moved.config;copyConfirmed=Boolean(moved.target_allowed);setRoot(moved.root);await refresh();
      }
      const saved=await action('settings',{values:{...values,test_copy_confirmed:copyConfirmed}});
      setForm(saved.config);setConfirmed(Boolean(saved.target_allowed));await refresh();
      setMessage('Настройки сохранены');return saved;
    }catch(e){setError(e.message);return null}finally{setPending(false)}
  }
  async function request(name,values={}){
    setError('');setPending(true);
    try{const result=await action(name,values);await refresh();return result}
    catch(e){setError(e.message);return null}finally{setPending(false)}
  }
  async function check(source){
    const saved=await save();if(!saved)return;
    setMessage('');setChecking(source);
    await request('check',{source,date:saved.config.last_date||today()});
  }
  async function closeSetup(){
    const saved=await save();if(!saved)return;
    if(await request('finish-setup'))onFinish(saved);
  }
  async function next(){if(step===3){await closeSetup();return}if(await save())setStep(s=>s+1)}
  async function createCopy(){
    setError('');setPending(true);
    try{
      const selected=await action('choose',{kind:'xlsx'});if(!selected.path)return;
      const result=await action('create-copy',{source:selected.path});
      set('target',result.path);setConfirmed(true);setMessage('Создана отдельная локальная копия');await refresh();
    }catch(e){setError(e.message)}finally{setPending(false)}
  }
  const visible=n=>wizard?step===n:({0:'general',1:'files',2:'google',3:'telegram'}[n]===tab);
  const field=(label,key,kind='xlsx',example='')=><FileField label={label} kind={kind} example={example} value={form[key]} disabled={busy} onChange={v=>set(key,v)} onError={setError}/>;
  const status=source=>{
    const result=data.checks[source];
    const dependencies={prostoi:['prostoi','last_date'],drivers:['drivers','last_date'],target:['target','target_mode','credentials'],credentials:['credentials'],backup:['google_backup_folder','target','credentials'],csv:['csv','telegram_mode','telegram_bot','telegram_timeout']};
    for(const key of ['target','credentials','backup'])dependencies[key].push('google_auth_mode','google_oauth_client');
    const edited=dependencies[source].some(key=>form[key]!==data.config[key])||(['prostoi','drivers'].includes(source)&&result?.date!==(form.last_date||today()));
    const running=checking===source&&data.state.phase==='checking';
    if(!running&&(!result||edited))return null;
    return <div className="settings-check" role="status" aria-live="polite" data-source={source}>{running?<span><Spinner/> Проверка…</span>:<><span>{result.ok?'✓':'✕'} {result.message}</span><small>{timeText(result.checked)}</small></>}</div>;
  };
  return <section className="sd-page settings-page"><h2>{wizard?'Настройка приложения':'Настройки'}</h2>
    {!wizard&&<Tabs label="Разделы настроек" value={tab} onChange={setTab} disabled={busy} items={[{id:'files',label:'Excel'},{id:'google',label:'Google'},{id:'telegram',label:'Telegram'},{id:'general',label:'Общие'}]}/>}
    {pending&&<Activity label={checking?'Запуск проверки…':'Сохранение настроек…'}/>}
    {wizard&&<div className="wizard-steps" aria-label="Этапы настройки">{['Рабочая папка','Исходные файлы','Целевая копия','Выгрузка Гаража'].map((title,i)=><span key={title} aria-current={step===i?'step':undefined}>{i+1}. {title}</span>)}</div>}
    {visible(0)&&<div className="settings-group appearance-group"><h3 id="appearance-heading">Вид интерфейса</h3>
      <div className="appearance-options" role="radiogroup" aria-labelledby="appearance-heading">{[{id:'classic',title:'Стандартный',description:'Текущий чёрный интерфейс'},{id:'apple',title:'Apple HIG',description:'Графитовые панели и сгруппированные элементы'}].map(item=><label key={item.id} className="appearance-option" data-selected={(form.appearance||'classic')===item.id}>
        <input type="radio" name="appearance" value={item.id} checked={(form.appearance||'classic')===item.id} disabled={busy} onChange={()=>set('appearance',item.id)}/>
        <span className={'appearance-sample appearance-sample-'+item.id} aria-hidden="true"><i/><span><b/><b/><b/></span></span><span className="appearance-copy"><strong>{item.title}</strong><small>{item.description}</small></span>
      </label>)}</div><p className="sd-muted settings-hint">Вид меняется сразу. Чтобы сохранить выбор, нажмите «Сохранить настройки».</p></div>}
    {visible(0)&&<div className="settings-group"><h3>Рабочая папка</h3>
      {wizard&&!data.config.initialized?<FileField label="Расположение данных приложения" value={root} kind="folder" disabled={busy} onChange={setRoot} onError={setError}/>:<div className="sd-setting"><div className="sd-setting-copy">{data.root}</div><Button disabled={busy} onClick={()=>request('open-folder')}>Открыть папку</Button></div>}
      <p className="sd-muted">Здесь сохраняются настройки, планы, копии таблиц и журнал.</p></div>}
    {visible(1)&&<div className="settings-group"><h3>Исходные файлы Excel</h3>
      <p className="sd-muted settings-hint">Программа читает эти файлы. Резервные копии создаются для таблицы, в которую записываются изменения.</p>
      {field('Средние цены и простои','prostoi','xlsx','Средние_цены_простои_2026.xlsx')}
      <Button disabled={busy} onClick={()=>check('prostoi')}>Проверить простои</Button>{status('prostoi')}
      {field('Начисления','drivers','xlsx','Начисление_10-26.xlsx')}
      <Button disabled={busy} onClick={()=>check('drivers')}>Проверить начисления</Button>{status('drivers')}
      <label>Дата обработки<input type="date" value={form.last_date||''} disabled={busy} onInput={e=>set('last_date',e.target.value)}/></label><p className="sd-muted settings-hint">Начисления проверяются за предыдущий день относительно даты обработки. Например, для 15 октября нужен лист «14».</p></div>}
    {visible(2)&&<div className="settings-group"><h3>Целевая копия</h3>
      <label>Расположение таблицы<select value={form.target_mode} onChange={e=>set('target_mode',e.target.value)} disabled={busy}><option value="local">Локальная книга Excel</option><option value="google">Google Sheets</option></select></label>
      {form.target_mode==='local'?<>{field('Копия книги с листом «Предложение цен»','target','xlsx','Анализ_конкурентов_копия.xlsx')}<Button disabled={busy} onClick={createCopy}>Создать копию из шаблона</Button></>:<>
        <label>Ссылка или ID тестовой таблицы<input type="text" value={form.target} disabled={busy} onChange={e=>set('target',e.target.value)}/></label>
        <GoogleConnection form={form} set={set} busy={busy} data={data} field={field} check={check} status={status} save={save} request={request}/>
        <div className="settings-subgroup"><h3>Резервные копии в Google Drive</h3><label>Папка для резервных копий<input type="text" placeholder="https://drive.google.com/drive/folders/…" value={form.google_backup_folder||''} disabled={busy} onChange={e=>set('google_backup_folder',e.target.value)}/></label>
          <p className="sd-muted settings-hint">Перед записью сохраняется вся Google-таблица с текущими комментариями. У подключённого аккаунта должен быть доступ к выбранной папке, а в проекте — включён Google Drive API.</p>
          <Button disabled={busy} onClick={()=>check('backup')}>Проверить папку копий</Button>{status('backup')}
        </div>
      </>}
      <label className="sd-switch copy-confirm"><input type="checkbox" checked={confirmed} onChange={e=>setConfirmed(e.target.checked)} disabled={busy}/>Это копия для тестов; разрешаю запись в неё</label>
      <Button disabled={busy} onClick={()=>check('target')}>Проверить целевую таблицу</Button>{status('target')}</div>}
    {visible(3)&&<div className="settings-group"><h3>Выгрузка Гаража</h3>
      <label>Получение файла<select value={form.telegram_mode} onChange={e=>set('telegram_mode',e.target.value)} disabled={busy}><option value="manual">Выбрать CSV вручную</option><option value="auto">Получать через Telegram</option></select></label>
      {form.telegram_mode==='manual'?field('CSV из бота','csv','csv'):<>
        <label>Имя бота<input type="text" placeholder="@имя_бота" value={form.telegram_bot} disabled={busy} onChange={e=>set('telegram_bot',e.target.value)}/></label>
        <label>Ожидание ответа, секунд<input type="number" min="15" max="150" value={form.telegram_timeout} disabled={busy} onChange={e=>set('telegram_timeout',e.target.value)}/></label>
        <details className="telegram-auth"><summary>Подключить свой аккаунт Telegram</summary>{['api_id','api_hash','phone'].map((key,i)=><label key={key}>{['API ID','API Hash','Телефон'][i]}<input type={key==='api_hash'?'password':'text'} autoComplete="off" value={auth[key]} disabled={busy} onChange={e=>setAuth({...auth,[key]:e.target.value})}/></label>)}
          <p className="sd-muted">API ID и API Hash получают на my.telegram.org. Аккаунт обращается к боту как обычный пользователь.</p>
          <Button disabled={busy} onClick={()=>request('telegram',{step:'code',values:auth})}>Получить код</Button>
          <label>Код Telegram<input type="text" autoComplete="off" value={auth.code} disabled={busy} onChange={e=>setAuth({...auth,code:e.target.value})}/></label>
          {data.state.telegram_auth?.password_required&&<label>Пароль двухфакторной защиты<input type="password" autoComplete="off" value={auth.password} disabled={busy} onChange={e=>setAuth({...auth,password:e.target.value})}/></label>}
          <Button disabled={busy} onClick={async()=>{if(await request('telegram',{step:'sign_in',values:auth}))setAuth({...auth,api_hash:'',code:'',password:''})}}>Подтвердить вход</Button>
          {data.state.telegram_auth?.connected&&<p>✓ Telegram подключён</p>}
        </details></>}
      <Button disabled={busy} onClick={()=>check('csv')}>Проверить {form.telegram_mode==='manual'?'CSV':'Telegram'}</Button>{status('csv')}</div>}
    {!wizard&&tab==='general'&&<div className="settings-group"><h3>Дополнительно</h3><label className="sd-switch"><input type="checkbox" checked={form.developer} disabled={busy} onChange={e=>set('developer',e.target.checked)}/>Показывать вкладку разработчика</label>
      <Button disabled={busy} onClick={async()=>{if(await save())onRestart()}}>Повторить первичную настройку</Button></div>}
    {(error||data.state.error)&&<Errors text={error||data.state.error}/>}
    <div className="sd-footer settings-footer"><span role="status" className="settings-feedback">{message||(!wizard&&'Настройки всех разделов сохраняются вместе')}</span><div className="sd-actions">{wizard?<>
      {step>0&&<Button disabled={busy} onClick={()=>setStep(s=>s-1)}>Назад</Button>}
      <Button disabled={busy} onClick={closeSetup}>Сохранить и выйти</Button>
      <Button primary disabled={busy} onClick={next}>{step===3?'Сохранить настройки':'Продолжить'}</Button>
    </>:<Button primary disabled={busy} onClick={save}>Сохранить настройки</Button>}</div></div>
  </section>;
}
