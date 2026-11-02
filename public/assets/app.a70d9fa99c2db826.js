(()=>{'use strict';
const body=document.body,demo=body.dataset.mode==='demo',toastEl=document.getElementById('toast');let toastTimer;
function openGuideTopic(hash){
let id;try{id=decodeURIComponent(hash.replace(/^#/,''))}catch{return}
const target=document.getElementById(id),topic=target?.closest('details.guide-topic');
if(!topic)return;topic.open=true;requestAnimationFrame(()=>target.scrollIntoView({block:'start',behavior:'instant'}));
}
if(document.querySelector('.guide-page')){
openGuideTopic(location.hash);
window.addEventListener('hashchange',()=>openGuideTopic(location.hash));
document.querySelectorAll('.guide-page a[href^="#"]').forEach(link=>link.addEventListener('click',()=>openGuideTopic(link.hash)));
}
function toast(message){if(!toastEl)return;clearTimeout(toastTimer);toastEl.textContent=message;toastEl.hidden=false;toastTimer=setTimeout(()=>{toastEl.hidden=true},4500)}
async function copy(text){try{if(navigator.clipboard&&window.isSecureContext)await navigator.clipboard.writeText(text);else{const t=document.createElement('textarea');t.value=text;t.setAttribute('readonly','');t.style.position='fixed';t.style.top='-1000px';document.body.append(t);t.select();if(!document.execCommand('copy'))throw new Error('clipboard');t.remove()}toast('기준시각과 주소를 복사했습니다.')}catch{toast('복사할 수 없습니다. 주소창의 주소를 복사해 주세요.')}}
function clockLabel(value,label='수집 확인'){if(!value)return label+' 시각 미확인';const date=new Date(value);if(Number.isNaN(date.getTime()))return label+' 시각 미확인';return label+' '+new Intl.DateTimeFormat('ko-KR',{timeZone:'Asia/Seoul',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false}).format(date)+' KST'}
function currentClock(){return Object.hasOwn(body.dataset,'collection')?clockLabel(body.dataset.collection):clockLabel(body.dataset.coverage,'전체 수집 범위 확인')}
document.querySelectorAll('[data-copy-link]').forEach(btn=>btn.addEventListener('click',()=>{const target=btn.dataset.snapshotUrl?new URL(btn.dataset.snapshotUrl,location.href).href:location.href;const headline=document.getElementById('assessment-title')?.textContent||'한반도 상황판';const stamp=Object.hasOwn(btn.dataset,'snapshotCollected')?clockLabel(btn.dataset.snapshotCollected):clockLabel(btn.dataset.snapshotCoverage,'전체 수집 범위 확인');copy((demo?'[가상 데이터 예시]\n':'')+'전쟁 날까 · '+headline+'\n'+stamp+'\n'+target+'\n공개자료 정리이며 전쟁 확률·공식 경보가 아닙니다.')}));
document.querySelectorAll('[data-copy-current]').forEach(btn=>btn.addEventListener('click',()=>copy((demo?'[가상 데이터 예시]\n':'')+document.title+'\n'+currentClock()+'\n'+location.href)));
const filters=[...document.querySelectorAll('[data-filter]')],changeRows=[...document.querySelectorAll('[data-change-type]')],changeEmpty=document.getElementById('filter-empty');
filters.forEach(btn=>btn.addEventListener('click',()=>{const selected=btn.dataset.filter;filters.forEach(b=>{b.classList.toggle('is-active',b===btn);b.setAttribute('aria-pressed',String(b===btn))});let visible=0;changeRows.forEach(row=>{const type=row.dataset.changeType,show=selected==='all'||type===selected||(selected==='correction'&&type==='retraction');row.hidden=!show;if(show)visible++});if(selected!=='all'){const fold=document.querySelector('.changes-section details.fold');if(fold&&changeRows.some(row=>!row.hidden&&fold.contains(row)))fold.open=true}if(changeEmpty){changeEmpty.hidden=visible>0;if(changeRows.length)changeEmpty.textContent='최근 24시간 안에 이 유형의 변경 기록이 없습니다.'}}));
const periodButtons=[...document.querySelectorAll('[data-period]')],panels=[...document.querySelectorAll('[data-period-panel]')];
periodButtons.forEach(btn=>btn.addEventListener('click',()=>{periodButtons.forEach(b=>{b.classList.toggle('is-active',b===btn);b.setAttribute('aria-pressed',String(b===btn))});panels.forEach(p=>{p.hidden=p.dataset.periodPanel!==btn.dataset.period})}));
const eventRows=[...document.querySelectorAll('[data-event-id]')],eventFilters=[...document.querySelectorAll('[data-event-filter]')],search=document.getElementById('event-search'),eventEmpty=document.getElementById('event-empty'),eventCount=document.getElementById('event-count');let eventFilter='all';
function applyEventFilters(){const query=(search?.value||'').toLocaleLowerCase('ko').trim();let visible=0;eventRows.forEach(row=>{const show=(eventFilter==='all'||row.dataset.eventCategory===eventFilter)&&(!query||row.dataset.eventSearch.toLocaleLowerCase('ko').includes(query));row.hidden=!show;if(show)visible++});if(eventEmpty)eventEmpty.hidden=visible>0;if(eventCount)eventCount.textContent=String(visible)}
eventFilters.forEach(btn=>btn.addEventListener('click',()=>{eventFilter=btn.dataset.eventFilter;eventFilters.forEach(b=>{b.classList.toggle('is-active',b===btn);b.setAttribute('aria-pressed',String(b===btn))});applyEventFilters()}));search?.addEventListener('input',applyEventFilters);
const marks=[...document.querySelectorAll('[data-map-event]')];
const reducedMotion=window.matchMedia('(prefers-reduced-motion: reduce)');
function revealMapSelection(){const selection=document.getElementById('map-selection');if(selection&&!reducedMotion.matches&&selection.animate)selection.animate([{opacity:.45,transform:'translateY(5px)'},{opacity:1,transform:'translateY(0)'}],{duration:220,easing:'cubic-bezier(.2,.7,.3,1)'});}
marks.forEach(mark=>mark.setAttribute('aria-pressed','false'));
marks.forEach(btn=>btn.addEventListener('click',()=>{marks.forEach(mark=>{mark.classList.toggle('is-active',mark===btn);mark.setAttribute('aria-pressed',String(mark===btn))});document.getElementById('map-selection-title').textContent=btn.dataset.mapTitle;const date=document.getElementById('map-selection-date');if(date){date.textContent=btn.dataset.mapDate||'발생·게시 날짜 미확인';date.hidden=false;}document.getElementById('map-selection-note').textContent=btn.dataset.mapPlace+' · 공개된 도시·지역 단위 위치입니다.';const link=document.getElementById('map-selection-link');link.href=btn.dataset.mapUrl;link.textContent='이 사건의 근거와 변화 보기 →';eventRows.forEach(row=>row.classList.toggle('is-selected',row.dataset.eventId===btn.dataset.mapEvent));revealMapSelection()}));
function officialFreshness(){
if(demo||body.dataset.snapshot==='true')return;
document.querySelectorAll('[data-official-state]').forEach(node=>{
const at=Date.parse(node.dataset.observedAt||''),failed=node.dataset.sourceStatus!=='ok';
let message=failed?(Number.isFinite(at)?'최근 확인 실패 · 마지막 확인 자료':'확인 실패 · 원문 확인 필요'):!Number.isFinite(at)?'확인 시각 미상 · 원문 확인 필요':Date.now()-at>=7200000?'갱신 지연 · 마지막 확인 자료':'';
node.textContent=message;node.hidden=!message;
});
}
function freshness(){
officialFreshness();
const badge=document.querySelector('[data-freshness]');if(!badge)return;
if(demo){badge.textContent='가상 데이터 · 실제 상황 아님';badge.classList.remove('is-old');return}
if(body.dataset.snapshot==='true'){badge.textContent='보존본 · 실시간 갱신 아님';badge.classList.remove('is-old');return}
const value=Object.hasOwn(body.dataset,'collection')?body.dataset.collection:body.dataset.coverage;
const timestamp=Date.parse(value||''),issues=Number(body.dataset.collectionIssues||0);
if(!Number.isFinite(timestamp)){badge.textContent='모든 자료원의 수집 성공 시각 미확인';badge.classList.add('is-old');return}
const hours=(Date.now()-timestamp)/3600000;
if(hours<-.1){badge.textContent='기기의 날짜와 시간을 확인해 주세요';badge.classList.add('is-old')}
else if(hours>=6){badge.textContent='오래된 수집 자료 · 최신 상황 확인 필요';badge.classList.add('is-old')}
else if(hours>=2){badge.textContent='수집 지연'+(issues?' · 일부 자료원 확인 필요':'');badge.classList.add('is-old')}
else if(issues){badge.textContent='일부 자료원 재확인 필요';badge.classList.add('is-old')}
else{badge.textContent='최근 수집 확인';badge.classList.remove('is-old')}
}freshness();setInterval(freshness,60000);
// Check only the live home. Preserve the current reading position and frozen snapshots.
const latestNotice=document.querySelector('[data-latest-notice]');
if(latestNotice&&!demo&&body.dataset.snapshot!=='true'){
let checking=false;
async function checkLatest(){
if(checking||!latestNotice.hidden||document.visibilityState==='hidden'||navigator.onLine===false)return;
checking=true;const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),8000);
try{
const response=await fetch(new URL(latestNotice.dataset.latestUrl,location.href),{cache:'no-store',signal:controller.signal});
if(!response.ok)return;
const next=await response.json(),nextTime=Date.parse(next.generated_at||''),currentTime=Date.parse(body.dataset.generatedAt||'');
if(next.mode!=='live'||typeof next.release_id!=='string'||!next.release_id||!Number.isFinite(nextTime))return;
const different=next.release_id!==body.dataset.release,newer=Number.isFinite(currentTime)?nextTime>currentTime||(nextTime===currentTime&&different):different;
if(newer)latestNotice.hidden=false;
}catch{/* A background check failure never replaces the current briefing. */}
finally{clearTimeout(timer);checking=false}
}
document.querySelector('[data-reload-latest]')?.addEventListener('click',()=>location.reload());
document.addEventListener('visibilitychange',()=>{if(document.visibilityState==='visible')checkLatest()});
window.addEventListener('online',checkLatest);
setInterval(checkLatest,90000);checkLatest();
}
const visitKey='warwatch:last-visit:'+(demo?'demo':'live');
try{const prior=JSON.parse(localStorage.getItem(visitKey)||'null'),note=document.getElementById('visit-note');if(prior?.at&&body.dataset.snapshot!=='true'){const at=Date.parse(prior.at);let count=0;changeRows.forEach(row=>{if(Date.parse(row.dataset.changeAt)>at){row.classList.add('is-new');count++}});if(note)note.textContent=count?'지난 방문 이후 '+count+'개 변화':'지난 방문 이후 새 변경 없음'}if(document.querySelector('.events-section')&&body.dataset.snapshot!=='true')localStorage.setItem(visitKey,JSON.stringify({release:body.dataset.release,at:new Date().toISOString()}))}catch{}
document.querySelectorAll('[data-clear-visit]').forEach(btn=>btn.addEventListener('click',()=>{try{localStorage.removeItem('warwatch:last-visit:live');localStorage.removeItem('warwatch:last-visit:demo');toast('이 브라우저의 방문 비교 기록을 지웠습니다.')}catch{toast('이 브라우저에서는 방문 기록을 저장하지 않습니다.')}}));
})();
