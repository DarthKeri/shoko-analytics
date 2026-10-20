import React,{useEffect,useRef} from 'react';
import {Button} from './components.jsx';
import AccountCard from './AccountCard.jsx';
export default function GoogleConnection({form,busy,data,save,request}){
  const current=form.google_auth_mode==='oauth'&&form.google_oauth_client===data.config.google_oauth_client;
  const connected=current&&data.google_auth?.connected;
  const signing=data.state.phase==='connecting'&&data.state.operation==='google'&&data.state.auth_step!=='profile';
  const requested=useRef(false);
  useEffect(()=>{if(connected&&!data.plan&&!data.google_auth.profile_loaded&&!busy&&!requested.current){requested.current=true;request('google-auth',{step:'profile'})}},[connected,busy,data.google_auth?.profile_loaded]);
  async function connect(){
    const values={target_mode:'google',google_auth_mode:'oauth',google_oauth_client:'',credentials:''};
    if(form.target_mode!=='google')values.target='';
    if(await save(values))await request('google-auth',{step:'connect'});
  }
  return <>
    <AccountCard service="Google" account={current?data.google_auth:{}} loading={signing}/>
    <div className="sd-actions"><Button primary disabled={busy} onClick={connect}>{connected?'Сменить аккаунт':'Войти через Google'}</Button>{connected&&<Button disabled={busy} onClick={()=>request('google-auth',{step:'disconnect'})}>Выйти</Button>}</div>
  </>
}
