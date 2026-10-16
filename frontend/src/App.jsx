import React,{useEffect,useRef,useState} from 'react';
import {Database,Archive,Settings as SettingsIcon,Terminal,ArrowLeft,ArrowRight} from 'lucide-react';
import * as api from './api.js';
import {Button,Errors,Modal,ChangesTable,Activity,Spinner,fileName,dateText,timeText} from './components.jsx';
import Settings from './Settings.jsx';
import Preview from './Preview.jsx';
import logo from './logo.png';
import Copies from './Copies.jsx';
import ActivityStatus from './ActivityStatus.jsx';

const today=()=>new Date().toLocaleDateString('sv-SE',{timeZone:'Europe/Moscow'});
const names={prostoi:'Простои',drivers:'Начисления',target:'Целевая таблица',csv:'Яндекс Гараж'};
const steps=['Проверка файлов и доступа','Чтение исходных данных','Проверка CSV','Подготовка изменений'];
const writeSteps=['Проверка актуальности','Создание резервной копии','Запись изменений','Сверка результата'];
const menus=[['update','Обновление',Database],['copies','Копии',Archive],['developer','Разработчик',Terminal],['settings','Настройки',SettingsIcon]];

export default function App(){
  const [data,setData]=useState(null),[fatal,setFatal]=useState(''),[uiError,setUiError]=useState(''),[route,setRoute]=useState({page:'update',tab:'start'}),[date,setDate]=useState(today),[wizard,setWizard]=useState(false),[modal,setModal]=useState(null),[autoScroll,setAutoScroll]=useState(true),[tick,setTick]=useState(0),[pending,setPending]=useState(false);
  const [previewAppearance,setPreviewAppearance]=useState(null);
  const appearance=previewAppearance||data?.config.appearance||'classic';
  const routes=useRef([{page:'update',tab:'start'}]),routeIndex=useRef(0),initialized=useRef(false),lastPhase=useRef('idle'),logRef=useRef(null),mainRef=useRef(null);
  useEffect(()=>{mainRef.current?.scrollTo({top:0})},[route.page,route.tab,wizard]);
  const refresh=async()=>{const value=await api.state();setData(value);return value};
  useEffect(()=>{refresh().then(v=>{if(!initialized.current){initialized.current=true;setWizard(!v.config.initialized);setDate(v.config.last_date||today())}}).catch(e=>setFatal(e.message))},[]);
  useEffect(()=>{const timer=setInterval(()=>{refresh().catch(e=>setFatal(e.message));setTick(v=>v+1)},1500);return()=>clearInterval(timer)},[]);
  useEffect(()=>{if(autoScroll&&logRef.current)logRef.current.scrollTop=logRef.current.scrollHeight},[data?.logs,autoScroll]);
  function navigate(page,tab=route.tab,internal=false){if(data?.state.phase==='writing'&&!internal)return;const next={page,tab};if(page===route.page&&tab===route.tab)return;routes.current=routes.current.slice(0,routeIndex.current+1);routes.current.push(next);routeIndex.current++;setRoute(next);setUiError('')}
  function traverse(delta){if(data?.state.phase==='writing')return;let index=routeIndex.current+delta;while(index>=0&&index<routes.current.length&&routes.current[index].page==='developer'&&!data?.config.developer)index+=delta;if(index>=0&&index<routes.current.length){routeIndex.current=index;setRoute(routes.current[index]);setUiError('')}}
  useEffect(()=>{if(!data)return;const phase=data.state.phase;if(phase!==lastPhase.current){if(phase==='preview')navigate('preview');if(phase==='writing'||phase==='done')navigate('progress',route.tab,true);lastPhase.current=phase}},[data?.state.phase]);
  useEffect(()=>{if(data&&!data.config.developer&&route.page==='developer')navigate('settings')},[data?.config.developer]);
  async function call(action,values={}){setPending(true);setUiError('');try{const result=await api.action(action,values);await refresh();return result}catch(e){setUiError(e.message);return null}finally{setPending(false)}}
  if(fatal)return <div id="shoko-dark" data-appearance={appearance}><main><h2>Не удалось подключиться к приложению</h2><Errors text={fatal}/><Button onClick={()=>location.reload()}>Повторить подключение</Button></main></div>;
  if(!data)return <div id="shoko-dark" data-appearance={appearance}><main><Activity label="Загрузка приложения…"/></main></div>;
  const phase=data.state.phase,busy=pending||['preparing','writing','checking','connecting'].includes(phase),writing=phase==='writing';
  const checkResult=key=>{const result=data.checks[key];return result?.date!==date?null:result};
  const checkErrors=Object.keys(data.checks).map(checkResult).filter(c=>c&&!c.ok).map(c=>c.message).join('\n');
  const problems=uiError||(data.state.operation==='check'?checkErrors:data.state.error||checkErrors)||(!Object.keys(data.checks).length?'Сначала проверьте источники':['prostoi','drivers'].some(key=>!checkResult(key))?'Проверьте источники для выбранной даты':'');
  const elapsed=data.state.started?Math.max(0,Math.floor((Date.now()-new Date(data.state.started).getTime())/1000)):0;
  async function prepare(){await call('prepare',{url:'',date})}
  async function openCopy(id){const result=await call('copy',{id});if(result)setModal({kind:'copy',...result})}
  const progress=(items)=> <div className="progress-list">{items.map((title,i)=><div className="sd-step" key={title}><span>{title}</span><span>{data.state.step>i?'✓ Готово':data.state.step===i?<><Spinner/> Выполняется</>:'Ожидает'}</span></div>)}</div>;
  const navigation=<div className="sd-route-controls" aria-label="История переходов"><button className="sd-route-button" aria-label="Назад" disabled={writing||routeIndex.current===0} onClick={()=>traverse(-1)}><ArrowLeft aria-hidden="true"/></button><button className="sd-route-button" aria-label="Вперёд" disabled={writing||routeIndex.current===routes.current.length-1} onClick={()=>traverse(1)}><ArrowRight aria-hidden="true"/></button></div>;
  return <div id="shoko-dark" data-appearance={appearance}><div className="sd-shell"><aside aria-label="Навигация"><div className="sd-brand"><span className="sd-logo-frame"><img className="sd-logo" src={logo} alt="Taxi Shokolad"/></span><span>Shoko<span className="sd-brand-sub">Analytics</span></span></div>{menus.filter(([key])=>key!=='developer'||data.config.developer).map(([key,label,Icon])=><button key={key} className={'sd-nav'+(key==='settings'?' sd-settings-nav':'')} aria-current={(route.page===key||(['preview','progress'].includes(route.page)&&key==='update'))?'page':undefined} disabled={writing} onClick={()=>{setWizard(false);navigate(key)}}><Icon aria-hidden="true"/>{label}</button>)}</aside><main ref={mainRef} aria-busy={busy}>{navigation}<ActivityStatus state={data.state} pending={pending} elapsed={elapsed} onCancelGoogle={()=>call('cancel-google')}/>
    {wizard?<Settings key="wizard" onPreviewAppearance={setPreviewAppearance} data={data} refresh={refresh} wizard onFinish={saved=>{setDate(saved.config.last_date||today());setWizard(false);navigate('update')}}/>:<>
      {route.page==='update'&&<section className="sd-page" data-screen="update"><div className="sd-head"><h2>Обновление данных</h2><Button disabled={busy} onClick={()=>navigate('settings')}>Настроить источники</Button></div>
        <div className="sd-form update-inputs manual-inputs">
          <label>Дата обработки<input type="date" value={date} onChange={e=>setDate(e.target.value)} disabled={busy}/></label>
        </div>
        {phase==='preparing'?<><h3>Подготовка</h3>{progress(steps)}</>:<div className="update-sources">{[...Object.keys(names),...(data.config.target_mode==='google'?['backup']:[])].map(key=>{
          const result=checkResult(key);
          const subtitle=key==='target'?(data.config.target_mode==='google'?'Google Sheets':fileName(data.config.target)):key==='backup'?'Google Drive':fileName(data.config[key]);
          return <div className="sd-source" key={key}><div className="source-heading"><strong>{key==='backup'?'Резервная копия':names[key]}</strong><div className="sd-source-file">{subtitle}</div></div>
            <div className="sd-source-state" role="status"><span className="sd-mark">{result?(result.ok?'✓':'✕'):'—'}</span><span>{result?result.message:'Не проверено для выбранной даты'}</span></div>
            <div className="source-actions">{key==='csv'&&<Button disabled={busy} onClick={async()=>{const chosen=await call('choose',{kind:'csv'});if(chosen?.path)await call('settings',{values:{csv:chosen.path}})}}>Выбрать CSV</Button>}<Button disabled={busy} onClick={()=>call('check',{source:key,date})}>Проверить</Button></div>
          </div>
        })}</div>}
        <Errors text={problems}/>
        <div className="sd-footer"><div className="sd-actions"><Button disabled={busy} onClick={()=>call('check',{date})}>Проверить статус</Button><div className="sd-launch">{phase==='preparing'?<Button onClick={()=>setModal({kind:'cancel'})}>Отменить подготовку</Button>:<Button primary data-action="prepare" disabled={busy} onClick={prepare}>Обновление</Button>}</div></div></div>
      </section>}
      {route.page==='preview'&&data.plan&&<Preview plan={data.plan} busy={busy} call={call} onBack={()=>navigate('update')}/>}
      {route.page==='preview'&&!data.plan&&<section className="sd-page"><h2>План недоступен</h2><Button onClick={()=>navigate('update')}>Вернуться к обновлению</Button></section>}
      {route.page==='progress'&&<section className="sd-page"><h2>{writing?'Обновление данных':phase==='done'?'Обновление завершено':'Результат обновления'}</h2>{writing?progress(writeSteps):<>{phase==='done'?<p>✓ Записанные значения проверены. Копия сохранена.</p>:<Errors text={data.state.error||'Проверьте журнал операции во вкладке разработчика'}/>}<div className="sd-footer"><div className="sd-actions"><Button onClick={()=>navigate('update')}>Вернуться к обновлению</Button><Button onClick={()=>navigate('copies')}>Открыть копии</Button></div></div></>}</section>}
      {route.page==='copies'&&<Copies data={data} busy={busy} call={call} openCopy={openCopy} error={uiError} onError={setUiError}/>}
      {route.page==='settings'&&<Settings onPreviewAppearance={setPreviewAppearance} data={data} refresh={refresh} onRestart={()=>setWizard(true)}/>}
      {route.page==='developer'&&data.config.developer&&<section className="sd-page"><h2>Разработчик</h2><label htmlFor="log-field">Журнал</label><textarea ref={logRef} id="log-field" className="sd-log" value={data.logs} readOnly spellCheck={false}/><div className="sd-actions"><Button onClick={()=>{navigator.clipboard?.writeText(data.logs).catch(()=>{logRef.current.select();setUiError('Текст выделен. Нажмите Ctrl+C.')})}}>Копировать журнал</Button><Button onClick={()=>api.download(new Blob([data.logs],{type:'text/plain;charset=utf-8'}),'shoko-log.txt')}>Сохранить журнал</Button><label className="sd-switch"><input type="checkbox" checked={autoScroll} onChange={e=>setAutoScroll(e.target.checked)}/>Автопрокрутка</label></div><div className="sd-diagnostics"><h3>Проверки</h3><Button disabled={busy} onClick={()=>call('check',{date})}>Проверить все источники</Button></div>{uiError&&<Errors text={uiError}/>}</section>}
    </>}
  </main></div>
    {modal?.kind==='cancel'&&<Modal title="Отменить подготовку?" onClose={()=>setModal(null)} actions={<><Button onClick={()=>setModal(null)}>Продолжить работу</Button><Button primary onClick={()=>{setModal(null);call('cancel')}}>Отменить подготовку</Button></>}><p>Журнал сохранится. Таблица не изменится.</p></Modal>}
    {modal?.kind==='copy'&&<Modal title="Резервная копия" onClose={()=>setModal(null)} actions={<>{modal.manifest.kind==='google-drive'?modal.manifest.cloud_id&&<Button onClick={()=>call('open-cloud-copy',{id:modal.manifest.id})}>Открыть в Google</Button>:<Button onClick={()=>api.downloadCopy(modal.manifest.id).catch(e=>setUiError(e.message))}>Скачать копию</Button>}<Button onClick={()=>call('open-folder',{id:modal.manifest.id})}>Открыть папку</Button><Button onClick={()=>setModal(null)}>Закрыть</Button></>}><p>{timeText(modal.manifest.created)}</p><p className="sd-muted modal-url">{modal.manifest.target}</p>{modal.changes?.length>0&&<ChangesTable changes={modal.changes}/>}<p className="sd-source-file">{modal.manifest.kind==='google-drive'?(modal.manifest.status==='verified'?'Полная копия и текущие комментарии проверены перед записью':modal.manifest.error||'Копирование ещё не подтверждено'):'Локальная копия проверена по контрольной сумме'}</p></Modal>}
  </div>
}
