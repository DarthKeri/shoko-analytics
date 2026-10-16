import React from 'react';
import {Spinner} from './components.jsx';
export default function AccountCard({account={},service,loading=false}){
  const photo=/^data:image\/(jpeg|png|webp);base64,/.test(account.avatar||'')?account.avatar:'';
  return <div className="account-card" role="status">
    <span className="account-avatar">{photo?<img src={photo} alt="Фото аккаунта"/>:loading?<Spinner/>:<span aria-hidden="true">{(account.name||account.email||service).slice(0,1).toUpperCase()}</span>}</span>
    <div><strong>{loading?'Подключение…':account.connected?(account.name||account.email||service):`${service} не подключён`}</strong>{account.connected&&<span>{account.name&&account.email?account.email:'✓ Подключён'}</span>}</div>
  </div>
}
