import React from 'react';
import {Activity,Button} from './components.jsx';

const preparation=['Проверяем файлы и доступ','Читаем исходные файлы Excel','Получаем выгрузку Гаража','Читаем таблицу и рассчитываем изменения'];
const writing=['Проверяем актуальность плана','Создаём и проверяем резервную копию','Записываем подтверждённые изменения','Сверяем записанные данные'];
const sources={prostoi:'Средние цены и простои',drivers:'Начисления',target:'Целевая таблица',csv:'Выгрузка Гаража',credentials:'Файл доступа Google',backup:'Папка резервных копий'};
export default function ActivityStatus({state,pending,elapsed,onCancelGoogle}){
  if(!pending&&!['preparing','writing','checking','connecting'].includes(state.phase))return null;
  const google=state.phase==='connecting'&&state.operation==='google';
  const label=google?'Вход в Google':{preparing:'Подготовка обновления',writing:'Обновление данных',checking:'Проверка подключения',connecting:'Подключение Telegram'}[state.phase]||'Выполняем действие';
  const detail=google?state.detail:state.phase==='preparing'?preparation[state.step]:state.phase==='writing'?(state.detail||writing[state.step]):state.phase==='checking'?sources[state.current_source]:'';
  return <Activity label={label} detail={detail||'Пожалуйста, подождите…'} elapsed={pending&&state.phase==='idle'?undefined:elapsed}>{google&&<Button disabled={pending} onClick={onCancelGoogle}>Отменить вход</Button>}</Activity>;
}
