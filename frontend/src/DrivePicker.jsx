import React,{useEffect,useState} from 'react';
import {action} from './api.js';
import {Button,Modal,Spinner} from './components.jsx';
export default function DrivePicker({kind,value,onChange,disabled}){
  const [open,setOpen]=useState(false),[query,setQuery]=useState(''),[items,setItems]=useState([]),[next,setNext]=useState(''),[loading,setLoading]=useState(false),[error,setError]=useState('');
  const folder=kind==='folders';
  async function load(token=''){
    setLoading(true);setError('');
    try{const result=await action('google-items',{kind,query,page_token:token});setItems(old=>token?[...old,...result.items]:result.items);setNext(result.next)}catch(e){setError(e.message)}finally{setLoading(false)}
  }
  useEffect(()=>{if(open)load()},[open]);
  return <><div className="file-field-controls"><input type="text" aria-label={folder?'Папка резервных копий':'Целевая Google-таблица'} placeholder={folder?'Ссылка на папку Google Drive':'Ссылка на Google-таблицу'} value={value||''} disabled={disabled} onChange={e=>onChange(e.target.value)}/><Button disabled={disabled} onClick={()=>{setQuery('');setOpen(true)}}>Выбрать</Button></div>
    {open&&<Modal title={folder?'Папка резервных копий':'Целевая таблица'} onClose={()=>!loading&&setOpen(false)} actions={<Button disabled={loading} onClick={()=>setOpen(false)}>Закрыть</Button>}>
      <form className="drive-search" onSubmit={e=>{e.preventDefault();load()}}><input autoFocus type="text" aria-label="Название" placeholder="Найти по названию" value={query} disabled={loading} onChange={e=>setQuery(e.target.value)}/><Button disabled={loading} onClick={()=>load()}>Найти</Button></form>
      {error&&<p role="alert" className="picker-error">{error}</p>}
      <div className="drive-items" aria-busy={loading}>{items.map(item=><button type="button" key={item.id} disabled={loading} onClick={()=>{onChange(folder?'https://drive.google.com/drive/folders/'+item.id:'https://docs.google.com/spreadsheets/d/'+item.id+'/edit');setOpen(false)}}><span>{item.name}</span><span aria-hidden="true">→</span></button>)}{!loading&&!error&&!items.length&&<p className="sd-muted">{folder?'Папки не найдены':'Таблицы не найдены'}</p>}</div>
      {loading&&<p role="status"><Spinner/> Загрузка…</p>}{next&&<Button disabled={loading} onClick={()=>load(next)}>Показать ещё</Button>}
    </Modal>}
  </>
}
