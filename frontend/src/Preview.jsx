import React,{useEffect,useState} from 'react';
import {action} from './api.js';
import {Button,ChangesTable,Errors,Activity,dateText,display} from './components.jsx';

function columnName(index){let n=index+1,text='';while(n){text=String.fromCharCode(65+(n-1)%26)+text;n=Math.floor((n-1)/26)}return text}

export default function Preview({plan,busy,call,onBack}){
  const [mode,setMode]=useState('changes'),[sheet,setSheet]=useState('Предложение цен'),[offset,setOffset]=useState(0),[page,setPage]=useState({data:[],total:0}),[error,setError]=useState('');
  const [loading,setLoading]=useState(true);
  useEffect(()=>{let alive=true;setLoading(true);setError('');action('plan',{kind:mode,sheet,offset}).then(v=>alive&&setPage(v)).catch(e=>alive&&setError(e.message)).finally(()=>alive&&setLoading(false));return()=>{alive=false}},[mode,sheet,offset,plan.id]);
  return <section className="sd-page preview-page"><h2>Проверьте изменения</h2><p className="sd-muted">{dateText(plan.date)} · {plan.count} изменений · {plan.cleared} очисток</p><div className="sd-subnav"><Button onClick={()=>{setMode('changes');setOffset(0)}} aria-pressed={mode==='changes'}>Было → станет</Button><Button onClick={()=>{setMode('full');setOffset(0)}} aria-pressed={mode==='full'}>Весь результат</Button></div>{mode==='full'&&<label>Лист<select value={sheet} onChange={e=>{setSheet(e.target.value);setOffset(0)}}>{plan.sheets.map(s=><option key={s.name}>{s.name}</option>)}</select></label>}
    {loading?<Activity label="Загрузка предпросмотра…"/>:mode==='changes'?<ChangesTable changes={page.data}/>:<div className="sd-tablewrap full-result"><table><thead><tr><th>Строка</th>{Array.from({length:Math.max(0,...page.data.map(row=>row.length))},(_,i)=><th key={i}>{columnName(i)}</th>)}</tr></thead><tbody>{page.data.map((row,r)=><tr key={r}><th>{offset+r+1}</th>{row.map((v,c)=><td key={c}>{display(v)}</td>)}</tr>)}</tbody></table></div>}
    <div className="pagination"><Button disabled={offset===0} onClick={()=>setOffset(Math.max(0,offset-100))}>Предыдущие строки</Button><span>{page.total?`${offset+1}–${Math.min(offset+100,page.total)} из ${page.total}`:'Изменений нет'}</span><Button disabled={offset+100>=page.total} onClick={()=>setOffset(offset+100)}>Следующие строки</Button></div>
    {plan.warnings.length>0&&<Errors text={plan.warnings.join('\n')}/>} {error&&<Errors text={error}/>}
    <div className="sd-footer"><div className="sd-actions"><Button disabled={busy} onClick={onBack}>Вернуться к обновлению</Button><Button primary disabled={busy||plan.count===0} onClick={()=>call('write',{id:plan.id,checksum:plan.checksum})}>Подтвердить и записать</Button></div></div>
  </section>
}
