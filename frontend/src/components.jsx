import React,{useEffect,useRef} from 'react';
import {action} from './api.js';

export function Button({primary=false,children,...props}){return <button type="button" className={'sd-button'+(primary?' sd-primary':'')} {...props}>{children}</button>}
export function FileField({label,value,onChange,kind='xlsx',disabled=false,onError}){
  async function choose(){try{const result=await action('choose',{kind});if(result.path)onChange(result.path)}catch(error){onError(error.message)}}
  return <label className="file-field">{label}<div className="file-field-controls"><input type="text" value={value||''} disabled={disabled} onChange={e=>onChange(e.target.value)} placeholder={kind==='folder'?'Путь к папке':'Полный путь к файлу'}/><Button disabled={disabled} onClick={choose}>Выбрать</Button></div></label>
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
