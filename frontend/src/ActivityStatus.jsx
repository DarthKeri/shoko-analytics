import React from 'react';
import {Activity,Button} from './components.jsx';

const preparation=['Проверяем файлы и доступ','Читаем исходные файлы Excel','Проверяем выбранный CSV','Читаем таблицу и рассчитываем изменения'];
const writing=['Проверяем актуальность плана','Создаём и проверяем резервную копию','Записываем подтверждённые изменения','Сверяем записанные данные'];
const sources={prostoi:'Средние цены и простои',drivers:'Начисления',target:'Целевая таблица',csv:'Выгрузка Гаража',credentials:'Файл доступа Google',backup:'Папка резервных копий'};
export default function ActivityStatus({state,pending,elapsed,onCancelGoogle}){
  if(!pending&&!['preparing','writing','checking','connecting'].includes(state.phase))return null;
  const google=state.phase==='connecting'&&state.operation==='google';
  const profile=google&&state.auth_step==='profile';
  const diagnostic=state.operation==='diagnostics';
  const label=diagnostic?'Диагностика':google?(profile?'Загрузка аккаунта Google':'Вход в Google'):{preparing:'Подготовка обновления',writing:'Обновление данных',checking:'Проверка подключения',connecting:'Подключение'}[state.phase]||'Выполняем действие';
  const detail=diagnostic?state.detail:google?state.detail:state.phase==='preparing'?(state.detail||preparation[state.step]):state.phase==='writing'?(state.detail||writing[state.step]):state.phase==='checking'?sources[state.current_source]:'';
  return <Activity label={label} detail={detail||'Пожалуйста, подождите…'} elapsed={pending&&state.phase==='idle'?undefined:elapsed}>{google&&!profile&&<Button disabled={pending} onClick={onCancelGoogle}>Отменить вход</Button>}</Activity>;
}
