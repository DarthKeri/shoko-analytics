import React from 'react';
import {Button,Spinner} from './components.jsx';

export default function GoogleConnection({form,set,busy,data,field,check,status,save,request}){
  const oauth=form.google_auth_mode==='oauth';
  const current=oauth&&form.google_oauth_client===data.config.google_oauth_client&&data.config.google_auth_mode==='oauth';
  const connected=current&&data.google_auth?.connected;
  const builtIn=Boolean(data.google_auth?.built_in_client);
  const signing=data.state.phase==='connecting'&&data.state.operation==='google';
  async function connect(){if(await save())await request('google-auth',{step:'connect'})}
  return <div className="settings-subgroup google-connection"><h3>Вход в Google</h3>
    <label>Способ подключения<select value={form.google_auth_mode||'oauth'} disabled={busy} onChange={e=>set('google_auth_mode',e.target.value)}><option value="oauth">Мой Google-аккаунт</option><option value="service_account">Сервисный аккаунт</option></select></label>
    {oauth?<>
      <p className="sd-muted settings-hint">{builtIn?'Вход настроен для Shoko Analytics. Нажмите кнопку и выберите аккаунт в браузере Google. Отдельный файл выбирать не нужно.':'Для этой сборки нужен OAuth-клиент приложения. Его можно указать в дополнительных настройках ниже.'}</p>
      <div className="google-account" role="status"><span>{signing?<Spinner/>:connected?'✓':'—'}</span><div><strong>{signing?'Ожидаем вход в браузере':connected?data.google_auth.email:'Аккаунт не подключён'}</strong><small>{connected?'Вход сохранён на этом компьютере':signing?'Выбери аккаунт и разреши Drive и Google Sheets':'Войди в аккаунт, в котором будут храниться копии'}</small></div></div>
      <div className="sd-actions"><Button primary disabled={busy} onClick={connect}>{connected?'Войти в другой аккаунт':'Войти через Google'}</Button>{connected&&<Button disabled={busy} onClick={()=>request('google-auth',{step:'disconnect'})}>Выйти из аккаунта</Button>}</div>
      {!busy&&data.state.operation==='google'&&data.state.detail&&<p className="sd-muted settings-hint" role="status">{data.state.detail}</p>}
      <details className="google-help"><summary>Дополнительные настройки Google</summary>
        <p>Обычно менять эти настройки не нужно. Собственный OAuth-клиент заменяет настройку, включённую в сборку.</p>
        {field('Собственный OAuth-клиент (необязательно)','google_oauth_client','json','google_oauth_client.json')}
        <Button disabled={busy} onClick={()=>check('credentials')}>Проверить настройку входа</Button>{status('credentials')}
<details className="google-help"><summary>Где взять OAuth-файл?</summary><ol><li>В Google Cloud включи Google Drive API и Google Sheets API.</li><li>В Google Auth Platform настрой Branding и Audience. Для тестирования добавь свою почту в Test users.</li><li>В Clients создай OAuth-клиент типа <strong>Desktop app</strong> и скачай JSON.</li><li>Выбери скачанный файл здесь и нажми «Войти через Google».</li></ol><p>Для доступа к существующей таблице и папке по ссылкам Google запросит разрешения на Drive и таблицы. Пароль вводится только на странице Google.</p><Button disabled={busy} onClick={()=>request('open-google-setup')}>Открыть Google Cloud</Button></details></details>

    </>:<>
      {field('Ключ сервисного аккаунта Google','credentials','json','credentials.json')}
      <Button disabled={busy} onClick={()=>check('credentials')}>Проверить credentials.json</Button>{status('credentials')}
      <p className="sd-muted settings-hint">Для сохранения копий в обычную папку «Моего диска» выбери «Мой Google-аккаунт».</p>
    </>}
  </div>;
}
