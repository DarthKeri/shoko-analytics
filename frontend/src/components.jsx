import React,{useEffect,useRef,useId} from 'react';
import {action} from './api.js';

export function Button({primary=false,children,...props}){return <button type="button" className={'sd-button'+(primary?' sd-primary':'')} {...props}>{children}</button>}
export function Spinner(){return <span className="sd-spinner" aria-hidden="true"/>}
export function Activity({label,detail='',elapsed,children}){return <div className="sd-activity" role="status"><Spinner/><div><strong>{label}</strong>{detail&&<span>{detail}</span>}</div>{elapsed!==undefined&&<small aria-hidden="true">{elapsed} с</small>}{children}<div className="sd-activity-track" aria-hidden="true"><span/></div></div>}
export function Tabs({label,items,value,onChange,disabled=false}){
  const id=useId();
  function keyboard(e,index){let next;if(e.key==='ArrowRight')next=(index+1)%items.length;else if(e.key==='ArrowLeft')next=(index-1+items.length)%items.length;else if(e.key==='Home')next=0;else if(e.key==='End')next=items.length-1;else return;e.preventDefault();onChange(items[next].id);e.currentTarget.parentElement.querySelectorAll('button')[next]?.focus()}
  return <div className="sd-subnav compact-tabs" role="tablist" aria-label={label}>{items.map((item,index)=><button key={item.id} id={`${id}-${item.id}`} type="button" role="tab" aria-selected={value===item.id} tabIndex={value===item.id?0:-1} disabled={disabled} onKeyDown={e=>keyboard(e,index)} onClick={()=>onChange(item.id)}>{item.label}{item.count!==undefined&&<span className="tab-count">{item.count}</span>}</button>)}</div>;
}
export function FileField({label,value,onChange,kind='xlsx',example='',disabled=false,onError}){
  async function choose(){try{const result=await action('choose',{kind});if(result.path)onChange(result.path)}catch(error){onError(error.message)}}
  return <label className="file-field">{label}<div className="file-field-controls"><input type="text" value={value||''} disabled={disabled} onChange={e=>onChange(e.target.value)} placeholder={kind==='folder'?'Путь к папке':'Полный путь к файлу'}/><Button disabled={disabled} onClick={choose}>Выбрать</Button></div>{example&&<span className="sd-field-example">Пример: «{example}»</span>}</label>
}
export function Errors({text}){return <div className="sd-problems"><div className="sd-error-heading">Ошибки</div><div role="status" aria-live="polite" className="problem-text">{text||'Ошибок не обнаружено'}</div></div>}
export function Modal({title,children,onClose,actions}){
  const box=useRef(null);
  useEffect(()=>{const previous=document.activeElement;box.current?.querySelector('button,input')?.focus();return()=>previous?.isConnected&&previous.focus()},[]);
  function keydown(e){if(e.key==='Escape'){onClose?.();return}if(e.key==='Tab'){const items=[...box.current.querySelectorAll('button,input,select,textarea,a[href]')].filter(x=>!x.disabled);if(e.shiftKey&&document.activeElement===items[0]){e.preventDefault();items.at(-1)?.focus()}else if(!e.shiftKey&&document.activeElement===items.at(-1)){e.preventDefault();items[0]?.focus()}}}
  return <div className="sd-modal"><section ref={box} onKeyDown={keydown} className="sd-dialog" role="dialog" aria-modal="true" aria-label={title}><h3>{title}</h3>{children}<div className="sd-actions modal-actions">{actions||<Button onClick={onClose}>Закрыть</Button>}</div></section></div>
}
export function dateText(value){if(!value)return '';const [y,m,d]=value.slice(0,10).split('-');return `${d}.${m}.${y}`}
export function timeText(value){return value?new Date(value).toLocaleString('ru-RU',{timeZone:'Europe/Moscow'}):''}
export function fileName(value){return value?value.split(/[\\/]/).at(-1):'Файл не выбран'}
export function display(value){return value===''||value===null||value===undefined?'—':String(value)}
export function ChangesTable({changes}){return <div className="sd-tablewrap"><table><thead><tr><th>Лист / ячейка</th><th>Было</th><th>Станет</th></tr></thead><tbody>{changes.map((c,i)=><tr key={i}><td>{c.sheet}<span className="cell-address">{c.cell}</span></td><td>{display(c.before)}</td><td>{display(c.after)}</td></tr>)}</tbody></table></div>}
