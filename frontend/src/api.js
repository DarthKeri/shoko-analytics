async function read(response){
  const result=await response.json();
  if(!response.ok)throw new Error(result.error||'Не удалось выполнить запрос.');
  return result;
}
const headers=()=>({'Content-Type':'application/json','X-Shoko-Token':window.SHOKO_TOKEN});
export const state=()=>fetch('/api/state',{headers:headers()}).then(read);
export const action=(action,values={})=>fetch('/api/action',{method:'POST',headers:headers(),body:JSON.stringify({action,...values})}).then(read);
export async function downloadCopy(id){
  const response=await fetch('/api/export-copy',{method:'POST',headers:headers(),body:JSON.stringify({id})});
  if(!response.ok)await read(response);
  download(await response.blob(),id+'.zip');
}
export function download(blob,name){const url=URL.createObjectURL(blob);const link=document.createElement('a');link.href=url;link.download=name;link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
