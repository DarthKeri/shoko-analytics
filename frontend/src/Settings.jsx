import React,{useEffect,useState,useRef} from 'react';
import {action} from './api.js';
import {Button,FileField,Errors,Activity,Spinner,Tabs,Modal,timeText} from './components.jsx';
import GoogleConnection from './GoogleConnection.jsx';
import DrivePicker from './DrivePicker.jsx';

const today=()=>new Date().toLocaleDateString('sv-SE',{timeZone:'Europe/Moscow'});

export default function Settings({data,refresh,wizard=false,onFinish,onRestart,onReset,onPreviewAppearance}){
  const [form,setForm]=useState(()=>({...data.config})),[confirmed,setConfirmed]=useState(Boolean(data.target_allowed));
  const [error,setError]=useState(''),[message,setMessage]=useState(''),[step,setStep]=useState(0);
  const [root,setRoot]=useState(data.root),[pending,setPending]=useState(false),[checking,setChecking]=useState('');
  const [resetOpen,setResetOpen]=useState(false);
  const pageRef=useRef(null);
  const [tab,setTab]=useState(data.state.operation==='google'?'google':'files');
  useEffect(()=>{pageRef.current?.closest('main')?.scrollTo({top:0})},[tab,step]);
  useEffect(()=>{onPreviewAppearance?.(form.appearance||'classic');return()=>onPreviewAppearance?.(null)},[form.appearance,onPreviewAppearance]);
  const busy=pending||['preparing','writing','checking','connecting'].includes(data.state.phase);
  const set=(key,value)=>{setForm(f=>({...f,[key]:value}));setError('');setMessage('');if(['target','target_mode'].includes(key))setConfirmed(false)};

  async function save(overrides={}){
    setError('');setMessage('');setPending(true);
    try{
      let values={...form,...overrides},copyConfirmed=confirmed;
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
  async function next(){if(step===2){await closeSetup();return}if(await save())setStep(s=>s+1)}
  async function resetSettings(){
    setError('');setPending(true);
    try{
      const saved=await action('reset-settings',{confirmed:true});
      setForm(saved.config);setConfirmed(false);setResetOpen(false);
      if(onReset)onReset(saved);else await refresh();
    }catch(e){setResetOpen(false);setError(e.message);await refresh().catch(()=>{})}finally{setPending(false)}
  }
  const visible=n=>wizard?step===n:({0:'general',1:'files',2:'google'}[n]===tab);
  const field=(label,key,kind='xlsx',example='')=><FileField label={label} kind={kind} example={example} value={form[key]} disabled={busy} onChange={v=>set(key,v)} onError={setError}/>;
  const status=source=>{
    const result=data.checks[source];
    const dependencies={prices:['prices','last_date'],fleet:['drivers','drivers_previous','last_date'],prostoi:['prostoi','last_date'],drivers:['drivers','drivers_previous','last_date'],target:['target','target_mode','credentials'],credentials:['credentials'],backup:['google_backup_folder','target','credentials'],csv:['csv']};
    dependencies.csv.push('target','target_mode','credentials','google_auth_mode','google_oauth_client');
    for(const key of ['target','credentials','backup'])dependencies[key].push('google_auth_mode','google_oauth_client');
    const edited=dependencies[source].some(key=>form[key]!==data.config[key])||(['prices','fleet','prostoi','drivers'].includes(source)&&result?.date!==(form.last_date||today()));
    const running=checking===source&&data.state.phase==='checking';
    if(!running&&(!result||edited))return null;
    return <div className="settings-check" role="status" aria-live="polite" data-source={source}>{running?<span><Spinner/> Проверка…</span>:<><span>{result.ok?'✓':'✕'} {result.message}</span><small>{timeText(result.checked)}</small></>}</div>;
  };
  return <section ref={pageRef} className="sd-page settings-page"><h2>{wizard?'Настройка приложения':'Настройки'}</h2>
    {!wizard&&<Tabs label="Разделы настроек" value={tab} onChange={setTab} disabled={busy} items={[{id:'files',label:'Файлы'},{id:'google',label:'Google'},{id:'general',label:'Общие'}]}/>}
    {pending&&<Activity label={checking?'Запуск проверки…':'Сохранение настроек…'}/>}
    {wizard&&<div className="wizard-steps" aria-label="Этапы настройки">{['Рабочая папка','Исходные файлы','Целевая копия'].map((title,i)=><span key={title} aria-current={step===i?'step':undefined}>{i+1}. {title}</span>)}</div>}
    {visible(0)&&<div className="settings-group appearance-group"><h3 id="appearance-heading">Вид интерфейса</h3>
      <div className="appearance-options" role="radiogroup" aria-labelledby="appearance-heading">{[{id:'classic',title:'Стандартный',description:'Текущий чёрный интерфейс'},{id:'apple',title:'Apple HIG',description:'Графитовые панели и сгруппированные элементы'}].map(item=><label key={item.id} className="appearance-option" data-selected={(form.appearance||'classic')===item.id}>
        <input type="radio" name="appearance" value={item.id} checked={(form.appearance||'classic')===item.id} disabled={busy} onChange={()=>set('appearance',item.id)}/>
        <span className={'appearance-sample appearance-sample-'+item.id} aria-hidden="true"><i/><span><b/><b/><b/></span></span><span className="appearance-copy"><strong>{item.title}</strong><small>{item.description}</small></span>
      </label>)}</div></div>}
    {visible(0)&&<div className="settings-group"><h3>Рабочая папка</h3>
      {wizard&&!data.config.initialized?<FileField label="Расположение данных приложения" value={root} kind="folder" disabled={busy} onChange={setRoot} onError={setError}/>:<div className="sd-setting"><div className="sd-setting-copy">{data.root}</div><Button disabled={busy} onClick={()=>request('open-folder')}>Открыть папку</Button></div>}
      <p className="sd-muted">Здесь сохраняются настройки, планы, копии таблиц и журнал.</p></div>}
    {visible(1)&&<div className="settings-group"><h3>Исходные файлы</h3>
      <label className="settings-date">Дата обработки<input type="date" value={form.last_date||today()} disabled={busy} onChange={e=>set('last_date',e.target.value)}/></label>
      {field('Приказ цен','prices','xlsx','Приказ_цены_2026.xlsx')}
      <Button disabled={busy} onClick={()=>check('prices')}>Проверить приказ цен</Button>{status('prices')}
      {field('Начисления за месяц отчётного дня','drivers','xlsx','Начисление_10-26.xlsx')}
      <Button disabled={busy} onClick={()=>check('drivers')}>Проверить акционных водителей</Button>{status('drivers')}
      {field('Начисления за предыдущий месяц — при необходимости','drivers_previous','xlsx','Начисление_09-26.xlsx')}
      <Button disabled={busy} onClick={()=>check('fleet')}>Проверить состав и простои</Button>{status('fleet')}
      {field('Выгрузка Гаража CSV','csv','csv','moskva_2026-10-15.csv')}
      <Button disabled={busy} onClick={()=>check('csv')}>Проверить CSV</Button>{status('csv')}
    </div>}
    {visible(2)&&<div className="settings-group"><h3>Аккаунт Google</h3>
      <GoogleConnection form={form} busy={busy} data={data} save={save} request={request}/>
      {form.target_mode==='google'&&form.google_auth_mode==='oauth'&&data.google_auth?.connected&&form.google_oauth_client===data.config.google_oauth_client&&<>
        <div className="settings-subgroup"><h3>Целевая таблица</h3>
          <DrivePicker kind="sheets" value={form.target} disabled={busy} onChange={v=>set('target',v)}/>
          <label className="sd-switch copy-confirm"><input type="checkbox" checked={confirmed} onChange={e=>setConfirmed(e.target.checked)} disabled={busy}/>Разрешено редактирование</label>
          <Button disabled={busy} onClick={()=>check('target')}>Проверить таблицу</Button>{status('target')}
        </div>
        <div className="settings-subgroup"><h3>Папка резервных копий</h3>
          <DrivePicker kind="folders" value={form.google_backup_folder} disabled={busy} onChange={v=>set('google_backup_folder',v)}/>
          <Button disabled={busy} onClick={()=>check('backup')}>Проверить папку</Button>{status('backup')}
        </div>
      </>}
    </div>}
    {!wizard&&tab==='general'&&<div className="settings-group"><h3>Дополнительно</h3><label className="sd-switch"><input type="checkbox" checked={form.developer} disabled={busy} onChange={e=>set('developer',e.target.checked)}/>Показывать вкладку разработчика</label>
      <Button disabled={busy} onClick={async()=>{if(await save())onRestart()}}>Повторить первичную настройку</Button>
      <Button disabled={busy} onClick={()=>setResetOpen(true)}>Удалить все настройки</Button></div>}
    {resetOpen&&<Modal title="Удалить все настройки?" onClose={()=>{if(!pending)setResetOpen(false)}} actions={<><Button disabled={busy} onClick={()=>setResetOpen(false)}>Отмена</Button><Button primary disabled={busy} onClick={resetSettings}>{pending?'Удаление…':'Удалить все настройки'}</Button></>}><p>Будут удалены пути к файлам, выбранная таблица, папка резервных копий, сохранённый вход Google и настройки интерфейса.</p><p>Потребуется повторная настройка. Рабочая папка, таблицы, резервные копии и журналы сохранятся.</p></Modal>}
    {(error||data.state.error)&&<Errors text={error||data.state.error}/>}
    <div className="sd-footer settings-footer"><span role="status" className="settings-feedback">{message}</span><div className="sd-actions">{wizard?<>
      {step>0&&<Button disabled={busy} onClick={()=>setStep(s=>s-1)}>Назад</Button>}
      <Button disabled={busy} onClick={closeSetup}>Сохранить и выйти</Button>
      <Button primary disabled={busy} onClick={next}>{step===2?'Сохранить настройки':'Продолжить'}</Button>
    </>:<Button primary disabled={busy} onClick={()=>save()}>Сохранить настройки</Button>}</div></div>
  </section>;
}
