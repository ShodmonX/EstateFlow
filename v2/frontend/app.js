'use strict';
const el=id=>document.getElementById(id);
const token=()=>el('token').value;
async function call(path,method='GET',body){const response=await fetch(path,{method,headers:{'Authorization':'Bearer '+token(),'Content-Type':'application/json'},body:body===undefined?undefined:JSON.stringify(body)});const value=await response.json();if(!response.ok)throw new Error(value.detail||'Request failed');return value;}
function status(value){el('status').textContent=value;}
function bind(id,fn){if(el(id))el(id).onclick=async()=>{try{await fn();}catch(error){status(error.message);}};}
bind('search',async()=>{const price=el('price').value;const data=await call('/api/search/announcements'+(price?'?max_price='+encodeURIComponent(price):''));el('results').replaceChildren();for(const item of data.items){const card=document.createElement('article');card.className='card';const h=document.createElement('h2');h.textContent=item.listing.district||'Synthetic listing';const p=document.createElement('p');p.textContent=item.listing.description;const cost=document.createElement('p');cost.className='price';cost.textContent=(item.listing.price??'Price pending')+' '+(item.listing.currency||'');card.append(h,p,cost);el('results').append(card);}status(data.metadata.total+' synthetic matches');});
bind('save',async()=>{const criteria=el('price').value?{max_price:Number(el('price').value)}:{};const result=await call('/api/webapp/filters','POST',{name:'Synthetic saved search',criteria});el('saved').textContent=JSON.stringify(result,null,2);status('Command accepted. Inspect operation '+result.operation_id);});
bind('filters',async()=>{el('saved').textContent=JSON.stringify(await call('/api/webapp/filters'),null,2);});
bind('inspect',async()=>{el('saved').textContent=JSON.stringify(await call('/api/admin/state/'+encodeURIComponent(el('owner').value)+'/'+encodeURIComponent(el('kind').value)),null,2);status('Owner projection loaded');});
bind('submit',async()=>{const result=await call('/commands/'+encodeURIComponent(el('owner').value),'POST',JSON.parse(el('command').value));el('results').textContent=JSON.stringify(result,null,2);status('Accepted operation '+result.operation_id);});
