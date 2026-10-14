import React,{useState} from 'react';
import * as api from './api.js';
import {Button,Errors,Tabs,timeText,fileName} from './components.jsx';

export default function Copies({data,busy,call,openCopy,error,onError}){
  const [tab,setTab]=useState('google');
  const groups={google:data.copies.filter(x=>x.kind==='google-drive'),excel:data.copies.filter(x=>x.kind==='local'),snapshots:data.copies.filter(x=>!['google-drive','local'].includes(x.kind))};
  const items=groups[tab];
  return <section className="sd-page copies-page"><h2>Копии</h2>
    <Tabs label="Типы резервных копий" value={tab} onChange={setTab} items={[{id:'google',label:'Google Drive',count:groups.google.length},{id:'excel',label:'Excel',count:groups.excel.length},{id:'snapshots',label:'Снимки',count:groups.snapshots.length}]}/>
    <p className="sd-muted settings-hint">{tab==='google'?'Полные Google-таблицы с текущими комментариями, сохранённые перед записью.':tab==='excel'?'Оригиналы изменяемых книг Excel перед записью.':'Служебные записи и снимки, созданные предыдущими версиями приложения.'}</p>
    {items.length?<div className="sd-tablewrap"><table><thead><tr><th>Создана</th><th>Содержимое</th><th>Действия</th></tr></thead><tbody>{items.map(item=><tr key={item.id}><td>{timeText(item.created)}</td><td>{tab==='google'?<><strong>{item.cloud_name||'Google-таблица'}</strong><div className="sd-source-file">{item.status==='verified'?'✓ Копия проверена':item.status==='failed'?'✕ Копирование не завершено':'Копирование…'}</div>{item.comment_count!==undefined&&<div className="sd-source-file">Текущих комментариев: {item.comment_count}</div>}{item.error&&<div className="sd-source-file">{item.error}</div>}</>:item.kind==='local'?fileName(item.target):item.kind==='google'?'Прежний локальный снимок Google':item.kind==='new-file'?'Создание рабочей книги':'Снимок'}</td><td><div className="copy-actions">{tab==='google'?<>{item.cloud_id&&<Button disabled={busy} onClick={()=>call('open-cloud-copy',{id:item.id})}>Открыть таблицу</Button>}<Button disabled={busy} onClick={()=>openCopy(item.id)}>Подробности</Button></>:<><Button disabled={busy} onClick={()=>openCopy(item.id)}>Подробности</Button><Button disabled={busy} onClick={()=>api.downloadCopy(item.id).catch(e=>onError(e.message))}>Скачать</Button></>}</div></td></tr>)}</tbody></table></div>:<div className="sd-empty"><span>Копий пока нет</span><p>{tab==='google'?'Первая копия появится перед подтверждённым обновлением Google-таблицы.':tab==='excel'?'Копия появится перед записью изменений в книгу Excel.':'Снимков этого типа пока нет.'}</p></div>}
    {error&&<Errors text={error}/>}
  </section>;
}
