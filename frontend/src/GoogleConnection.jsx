import React,{useEffect,useRef} from 'react';
import {Button} from './components.jsx';
import AccountCard from './AccountCard.jsx';
export default function GoogleConnection({form,set,busy,data,field,check,status,save,request}){
  const current=form.google_auth_mode==='oauth'&&form.google_oauth_client===data.config.google_oauth_client;
  const connected=current&&data.google_auth?.connected;
  const signing=data.state.phase==='connecting'&&data.state.operation==='google'&&data.state.auth_step!=='profile';
  const requested=useRef(false);
  useEffect(()=>{if(connected&&!data.plan&&!data.google_auth.profile_loaded&&!busy&&!requested.current){requested.current=true;request('google-auth',{step:'profile'})}},[connected,busy,data.google_auth?.profile_loaded]);
  async function connect(){if(await save({target_mode:'google',google_auth_mode:'oauth'}))await request('google-auth',{step:'connect'})}
  return <>
    <AccountCard service="Google" account={current?data.google_auth:{}} loading={signing}/>
    <div className="sd-actions"><Button primary disabled={busy} onClick={connect}>{connected?'Сменить аккаунт':'Войти через Google'}</Button>{connected&&<Button disabled={busy} onClick={()=>request('google-auth',{step:'disconnect'})}>Выйти</Button>}</div>
    <details className="google-help"><summary>Дополнительно</summary>
      <label>Способ подключения<select value={form.google_auth_mode||'oauth'} disabled={busy} onChange={e=>set('google_auth_mode',e.target.value)}><option value="oauth">Google-аккаунт</option><option value="service_account">Сервисный аккаунт</option></select></label>
      {form.google_auth_mode==='oauth'?field('OAuth-клиент приложения','google_oauth_client','json'):field('Ключ сервисного аккаунта','credentials','json','credentials.json')}
      <Button disabled={busy} onClick={()=>check('credentials')}>Проверить настройку входа</Button>{status('credentials')}
      <label>Расположение таблицы<select value={form.target_mode} disabled={busy} onChange={e=>set('target_mode',e.target.value)}><option value="google">Google Sheets</option><option value="local">Локальная книга Excel</option></select></label>
    </details>
  </>
}
