import React from 'react';
import {Button,Errors,timeText} from './components.jsx';
export default function Copies({data,busy,call,openCopy,error}){
  const items=data.copies.filter(x=>x.kind==='google-drive');
  return <section className="sd-page copies-page"><h2>Копии Google Drive</h2>
    {items.length?<div className="sd-tablewrap"><table><thead><tr><th>Создана</th><th>Таблица</th><th>Действия</th></tr></thead><tbody>{items.map(item=><tr key={item.id}><td>{timeText(item.created)}</td><td><strong>{item.cloud_name||'Google-таблица'}</strong><div className="sd-source-file">{item.status==='verified'?'✓ Копия проверена':item.status==='failed'?'✕ Копирование не завершено':'Копирование…'}</div>{item.comment_count!==undefined&&<div className="sd-source-file">Комментариев: {item.comment_count}</div>}{item.error&&<div className="sd-source-file">{item.error}</div>}</td><td><div className="copy-actions">{item.cloud_id&&<Button disabled={busy} onClick={()=>call('open-cloud-copy',{id:item.id})}>Открыть таблицу</Button>}<Button disabled={busy} onClick={()=>openCopy(item.id)}>Подробности</Button></div></td></tr>)}</tbody></table></div>:<div className="sd-empty"><span>Копий пока нет</span><p>Копия появится перед подтверждённой записью в Google-таблицу.</p></div>}
    {error&&<Errors text={error}/>}
  </section>
}
