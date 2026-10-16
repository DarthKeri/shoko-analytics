import React,{useEffect,useRef,useState} from 'react';
import {ChartNoAxesCombined,ChartColumn} from 'lucide-react';

const dayText=(value,options={day:'2-digit',month:'long'})=>new Date(`${value}T12:00:00+03:00`).toLocaleDateString('ru-RU',{...options,timeZone:'Europe/Moscow'});
function smooth(points){
  if(!points.length)return '';
  const slopes=points.slice(1).map((p,i)=>(p.y-points[i].y)/(p.x-points[i].x));
  const tangents=points.map((_,i)=>i===0?slopes[0]||0:i===points.length-1?slopes.at(-1)||0:slopes[i-1]*slopes[i]<=0?0:2*slopes[i-1]*slopes[i]/(slopes[i-1]+slopes[i]));
  return points.reduce((path,p,i)=>{if(!i)return `M${p.x},${p.y}`;const prev=points[i-1],dx=(p.x-prev.x)/3;return `${path} C${prev.x+dx},${prev.y+tangents[i-1]*dx} ${p.x-dx},${p.y-tangents[i]*dx} ${p.x},${p.y}`},'');
}

export default function ActivityChart({activity,type,onTypeChange,saving=false}){
  const box=useRef(null),[width,setWidth]=useState(600),[selected,setSelected]=useState(null);
  useEffect(()=>{const observer=new ResizeObserver(entries=>setWidth(Math.max(200,entries[0].contentRect.width)));observer.observe(box.current);return()=>observer.disconnect()},[]);
  const days=activity?.days||[],height=220,left=42,right=14,top=28,bottom=height-37;
  const maximum=Math.max(1,...days.flatMap(d=>[d.launches,d.updates])),ceiling=maximum<5?maximum+1:Math.ceil(maximum/5)*5;
  const ticks=[...new Set(Array.from({length:Math.min(5,ceiling)+1},(_,i)=>Math.round(i*ceiling/Math.min(5,ceiling))))];
  const step=(width-left-right)/Math.max(1,days.length),x=i=>left+step*(i+.5),y=value=>bottom-(bottom-top)*value/ceiling;
  const line=key=>smooth(days.map((d,i)=>({x:x(i),y:y(d[key])}))),bar=Math.min(16,step*.29);
  const detail=days[selected]||days.at(-1),empty=!days.some(d=>d.launches||d.updates),spacing=width<390?5:2;
  const select=event=>{const bounds=event.currentTarget.getBoundingClientRect();setSelected(Math.max(0,Math.min(days.length-1,Math.floor(((event.clientX-bounds.left)*width/bounds.width-left)/step))))};
  return <section className="activity-chart" aria-label="Статистика использования приложения">
    <header className="activity-chart-head"><div><h3>Активность приложения</h3><p>{days.length?`${dayText(days[0].date,{day:'2-digit',month:'2-digit'})} — ${dayText(days.at(-1).date)}`:'Последние 14 дней'}</p><div className={'activity-legend '+(type==='bars'?'legend-bars':'')}><span><i className="legend-launches"/>Запуски приложения</span><span><i/>Обновления</span></div></div>
      <div className="chart-switch" role="group" aria-label="Вид графика"><button type="button" aria-pressed={type==='line'} disabled={saving} onClick={()=>onTypeChange('line')}><ChartNoAxesCombined aria-hidden="true"/>Линии</button><button type="button" aria-pressed={type==='bars'} disabled={saving} onClick={()=>onTypeChange('bars')}><ChartColumn aria-hidden="true"/>Столбцы</button></div>
    </header>
    <div className="activity-plot" ref={box}>
      <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label={`Запуски приложения и успешно завершённые обновления за последние 14 дней. ${detail?`${dayText(detail.date)}: запусков ${detail.launches}, обновлений ${detail.updates}.`:''}`}>
        <text className="activity-unit" x={left} y={13}>событий / день</text>
        {ticks.map(value=><g key={value}><line className="activity-grid" x1={left} x2={width-right} y1={y(value)} y2={y(value)}/><text className="activity-tick" x={left-12} y={y(value)+4} textAnchor="end">{value}</text></g>)}
        {type==='line'?<><path className="activity-area" d={days.length?`${line('updates')} L${x(days.length-1)},${bottom} L${x(0)},${bottom} Z`:''}/><path className="activity-launch-line" d={line('launches')}/><path className="activity-update-line" d={line('updates')}/>{days.map((d,i)=><circle className="activity-point" key={d.date} cx={x(i)} cy={y(d.updates)} r="3"/>)}</>:days.map((d,i)=><g key={d.date}><rect className="activity-launch-bar" x={x(i)-bar-1} y={y(d.launches)} width={bar} height={bottom-y(d.launches)} rx="2"/><rect className="activity-update-bar" x={x(i)+1} y={y(d.updates)} width={bar} height={bottom-y(d.updates)} rx="2"/></g>)}
        {days.map((d,i)=>(i%spacing===0||i===days.length-1)&&!(i===days.length-2)&&<text className="activity-tick" key={d.date} x={x(i)} y={bottom+25} textAnchor="middle">{dayText(d.date,{day:'2-digit',month:'2-digit'})}</text>)}
        {selected!==null&&days[selected]&&<><line className="activity-guide" x1={x(selected)} x2={x(selected)} y1={top} y2={bottom}/><circle className="activity-point" cx={x(selected)} cy={y(days[selected].updates)} r="5"/></>}
        {empty&&<text className="activity-tick" x={(width+left-right)/2} y={(top+bottom)/2} textAnchor="middle">Обновлений пока нет</text>}
        <rect x={left} y={top} width={Math.max(0,width-left-right)} height={bottom-top} fill="transparent" onPointerMove={select} onPointerDown={select} onPointerLeave={()=>setSelected(null)}/>
      </svg>
    </div>
    <p className="activity-detail">{detail?`${dayText(detail.date)} · Запуски: ${detail.launches} · Обновления: ${detail.updates}`:'Нет данных'}</p>
    {activity?.error&&<p className="activity-warning" role="status">{activity.error}</p>}
  </section>;
}
