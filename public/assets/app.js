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
filters.forEach(btn=>btn.addEventListener('click',()=>{const selected=btn.dataset.filter;filters.forEach(b=>{b.classList.toggle('is-active',b===btn);b.setAttribute('aria-pressed',String(b===btn))});let visible=0;changeRows.forEach(row=>{const type=row.dataset.changeType,show=selected==='all'||type===selected||(selected==='backfill'&&row.dataset.registrationKind==='backfill')||(selected==='correction'&&type==='retraction');row.hidden=!show;if(show)visible++});if(selected!=='all'){const fold=document.querySelector('.changes-section details.fold');if(fold&&changeRows.some(row=>!row.hidden&&fold.contains(row)))fold.open=true}if(changeEmpty){changeEmpty.hidden=visible>0;if(changeRows.length)changeEmpty.textContent='최근 24시간 안에 이 유형의 변경 기록이 없습니다.'}}));
const periodButtons=[...document.querySelectorAll('[data-period]')],panels=[...document.querySelectorAll('[data-period-panel]')];
periodButtons.forEach(btn=>btn.addEventListener('click',()=>{periodButtons.forEach(b=>{b.classList.toggle('is-active',b===btn);b.setAttribute('aria-pressed',String(b===btn))});panels.forEach(p=>{p.hidden=p.dataset.periodPanel!==btn.dataset.period})}));
const eventRows=[...document.querySelectorAll('[data-event-id]')],eventFilters=[...document.querySelectorAll('[data-event-filter]')],search=document.getElementById('event-search'),eventEmpty=document.getElementById('event-empty'),eventCount=document.getElementById('event-count');let eventFilter='all',eventPage=1,mobileHomeEnabled=false;
const eventPager=document.querySelector('[data-event-pagination]'),eventPageSize=1;
function matchingEvents(){const query=(search?.value||'').toLocaleLowerCase('ko').trim();return eventRows.filter(row=>(eventFilter==='all'||row.dataset.eventCategory===eventFilter)&&(!query||row.dataset.eventSearch.toLocaleLowerCase('ko').includes(query)));}
function applyEventFilters(){
const matching=matchingEvents();
const pages=Math.max(1,Math.ceil(matching.length/eventPageSize));eventPage=Math.min(eventPage,pages);
const shown=new Set(mobileHomeEnabled?matching.slice((eventPage-1)*eventPageSize,eventPage*eventPageSize):matching);
eventRows.forEach(row=>{row.hidden=!shown.has(row)});if(eventEmpty)eventEmpty.hidden=matching.length>0;if(eventCount)eventCount.textContent=String(matching.length);
const filterState=document.querySelector('[data-mobile-filter-state]');if(filterState)filterState.textContent=eventFilter!=='all'||(search?.value||'').trim()?'조건 적용':'전체';
if(eventPager){eventPager.hidden=!mobileHomeEnabled||pages<=1;eventPager.querySelector('[data-event-page-status]').textContent=eventPage+' / '+pages;eventPager.querySelector('[data-event-page="previous"]').disabled=eventPage<=1;eventPager.querySelector('[data-event-page="next"]').disabled=eventPage>=pages;}
}
eventFilters.forEach(btn=>btn.addEventListener('click',()=>{eventFilter=btn.dataset.eventFilter;eventPage=1;eventFilters.forEach(b=>{b.classList.toggle('is-active',b===btn);b.setAttribute('aria-pressed',String(b===btn))});applyEventFilters();if(mobileHomeEnabled){const fold=document.querySelector('[data-mobile-event-tools]');if(fold)fold.open=false;}}));search?.addEventListener('input',()=>{eventPage=1;applyEventFilters()});
search?.addEventListener('keydown',event=>{if(event.key==='Enter'&&mobileHomeEnabled){const fold=document.querySelector('[data-mobile-event-tools]');if(fold){fold.open=false;fold.querySelector('summary')?.focus();}}});
document.querySelectorAll('[data-event-page]').forEach(button=>button.addEventListener('click',()=>{eventPage+=button.dataset.eventPage==='next'?1:-1;applyEventFilters();const heading=document.getElementById('events-title');heading?.setAttribute('tabindex','-1');heading?.focus({preventScroll:true});document.getElementById('events')?.scrollIntoView({block:'start',behavior:'instant'});}));
const marks=[...document.querySelectorAll('[data-map-event]')];
const reducedMotion=typeof window!=='undefined'&&window.matchMedia?window.matchMedia('(prefers-reduced-motion: reduce)'):{matches:true};
function revealMapSelection(){const selection=document.getElementById('map-selection');selection?.classList.add('is-open');if(mobileHomeEnabled)requestAnimationFrame(()=>selection?.scrollIntoView({block:'nearest',behavior:'instant'}));if(selection&&!reducedMotion.matches&&selection.animate)selection.animate([{opacity:.45,transform:'translateY(5px)'},{opacity:1,transform:'translateY(0)'}],{duration:220,easing:'cubic-bezier(.2,.7,.3,1)'});}
marks.forEach(mark=>mark.setAttribute('aria-pressed','false'));
marks.forEach(btn=>btn.addEventListener('click',()=>{marks.forEach(mark=>{mark.classList.toggle('is-active',mark===btn);mark.setAttribute('aria-pressed',String(mark===btn))});document.getElementById('map-selection-title').textContent=btn.dataset.mapTitle;const date=document.getElementById('map-selection-date');if(date){date.textContent=btn.dataset.mapDate||'발생·게시 날짜 미확인';date.hidden=false;}document.getElementById('map-selection-note').textContent=btn.dataset.mapPlace+' · 공개된 도시·지역 단위 위치입니다.';const link=document.getElementById('map-selection-link');link.href=btn.dataset.mapUrl;link.textContent='이 사건의 근거와 변화 보기 →';eventRows.forEach(row=>row.classList.toggle('is-selected',row.dataset.eventId===btn.dataset.mapEvent));revealMapSelection()}));
document.querySelector('[data-close-map]')?.addEventListener('click',()=>{document.getElementById('map-selection')?.classList.remove('is-open');marks.find(mark=>mark.classList.contains('is-active'))?.focus({preventScroll:true});});
function officialFreshness(){
if(demo||body.dataset.snapshot==='true')return;
document.querySelectorAll('[data-official-state]').forEach(node=>{
const at=Date.parse(node.dataset.observedAt||''),failed=node.dataset.sourceStatus!=='ok';
let message=failed?(Number.isFinite(at)?'최근 확인 실패 · 마지막 확인 자료':'확인 실패 · 원문 확인 필요'):!Number.isFinite(at)?'확인 시각 미상 · 원문 확인 필요':Date.now()-at>=7200000?'갱신 지연 · 마지막 확인 자료':'';
node.textContent=message;node.hidden=!message;
});
}
function tensionFreshness(){
const panel=document.querySelector('[data-tension]');
if(!panel||demo||body.dataset.snapshot==='true')return;
const expires=Date.parse(panel.dataset.validUntil||''),generated=Date.parse(body.dataset.generatedAt||'');
const future=Number.isFinite(generated)&&Date.now()<generated-60000;
const unavailable=(!Number.isFinite(expires)&&panel.dataset.originalKey!=='unknown')||(Number.isFinite(expires)&&Date.now()>=expires)||future;
const label=panel.querySelector('[data-tension-label]'),summary=panel.querySelector('[data-tension-summary]'),mascot=panel.querySelector('[data-tension-mascot]');
const staleNote=panel.querySelector('[data-tension-stale]');if(staleNote)staleNote.hidden=!unavailable;
if(mascot&&!mascot.dataset.originalSrc){mascot.dataset.originalSrc=mascot.getAttribute('src');mascot.dataset.originalAlt=mascot.getAttribute('alt');}
panel.dataset.tensionKey=unavailable?'unknown':panel.dataset.originalKey||panel.dataset.tensionKey;
if(label)label.textContent=unavailable?'판단 보류':panel.dataset.tensionLabel;
if(summary)summary.textContent=unavailable?(future?'기기의 날짜와 시간을 확인해 주세요.':'수집 확인이 지연되어 현재 상태를 판단하지 않습니다.'):panel.dataset.tensionSummary;
if(mascot){mascot.src=unavailable?panel.dataset.unknownImage:mascot.dataset.originalSrc;mascot.alt=unavailable?'군모를 쓴 진돗개 · 판단 보류':mascot.dataset.originalAlt;}
}
const tensionPanel=document.querySelector('[data-tension]');if(tensionPanel)tensionPanel.dataset.originalKey=tensionPanel.dataset.tensionKey;
function freshness(){
officialFreshness();
tensionFreshness();
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
try{const prior=JSON.parse(localStorage.getItem(visitKey)||'null'),note=document.getElementById('visit-note');if(prior?.at&&body.dataset.snapshot!=='true'){const at=Date.parse(prior.at);let count=0;changeRows.forEach(row=>{if(Date.parse(row.dataset.changeAt)>at){row.classList.add('is-new');count++}});if(note)note.textContent=count?'지난 방문 이후 '+count+'건의 기록 갱신':'지난 방문 이후 새 변경 없음'}if(document.querySelector('.events-section')&&body.dataset.snapshot!=='true')localStorage.setItem(visitKey,JSON.stringify({release:body.dataset.release,at:new Date().toISOString()}))}catch{}
document.querySelectorAll('[data-clear-visit]').forEach(btn=>btn.addEventListener('click',()=>{try{localStorage.removeItem('warwatch:last-visit:live');localStorage.removeItem('warwatch:last-visit:demo');toast('이 브라우저의 방문 비교 기록을 지웠습니다.')}catch{toast('이 브라우저에서는 방문 기록을 저장하지 않습니다.')}}));

// Move the existing content, never clone event IDs or rewrite a saved briefing.
// Without JS, or above the phone breakpoint, the complete original document remains.
function setupMobileHome(){
const home=document.querySelector('[data-mobile-home]');
if(!home||typeof window==='undefined'||!window.matchMedia)return;
const media=window.matchMedia('(max-width:680px)'),tablist=document.querySelector('[data-home-tabs]'),tabs=[...tablist.querySelectorAll('[data-home-tab]')],homePanels=[...home.querySelectorAll('[data-home-panel]')],placements=[];
const panel=name=>homePanels.find(item=>item.dataset.homePanel===name);
function move(selector,target,before=null){const node=typeof selector==='string'?document.querySelector(selector):selector;if(!node||!target)return;const marker=document.createComment('mobile home section');node.before(marker);placements.push({node,marker});target.insertBefore(node,before);}
function panelFor(hash){
let id;try{id=decodeURIComponent(hash.replace(/^#/,''))}catch{return 'overview'}
if(id==='events'||id==='home-panel-news'||id.startsWith('record-'))return 'news';
if(id==='official'||id==='home-panel-official')return 'official';
if(['records','changes','sources','trend','brief-title','home-panel-records'].includes(id))return 'records';
const target=document.getElementById(id),owner=target?.closest('[data-home-panel]');return owner?.dataset.homePanel||'overview';
}
function activate(hash,scroll=false){
if(!mobileHomeEnabled)return;
const active=panelFor(hash);body.dataset.homeTab=active;
let targetId;try{targetId=decodeURIComponent(hash.replace(/^#/,''))}catch{targetId=''}
const targetRecord=active==='news'&&targetId.startsWith('record-')?document.getElementById(targetId):null;
if(targetRecord&&eventRows.includes(targetRecord)){
if(eventFilter!=='all'&&targetRecord.dataset.eventCategory!==eventFilter){eventFilter='all';eventFilters.forEach(button=>{const selected=button.dataset.eventFilter==='all';button.classList.toggle('is-active',selected);button.setAttribute('aria-pressed',String(selected));});}
const query=(search?.value||'').toLocaleLowerCase('ko').trim();if(query&&!targetRecord.dataset.eventSearch.toLocaleLowerCase('ko').includes(query)&&search)search.value='';
eventPage=Math.floor(matchingEvents().indexOf(targetRecord)/eventPageSize)+1;applyEventFilters();const searchFold=document.querySelector('[data-mobile-event-tools]');if(searchFold)searchFold.open=false;
}
homePanels.forEach(item=>{item.hidden=item.dataset.homePanel!==active;item.setAttribute('role','tabpanel');item.setAttribute('aria-labelledby','home-tab-'+item.dataset.homePanel);});
tabs.forEach(tab=>{const selected=tab.dataset.homeTab===active;tab.setAttribute('aria-selected',String(selected));tab.tabIndex=selected?0:-1;});
if(scroll){requestAnimationFrame(()=>{let id;try{id=decodeURIComponent(hash.replace(/^#/,''))}catch{id=''}const target=document.getElementById(id);if(target&&target.closest('[data-home-panel]')===panel(active)){let detail=target.closest('details');while(detail){detail.open=true;detail=detail.parentElement?.closest('details');}target.scrollIntoView({block:'start',behavior:'instant'});}else window.scrollTo({top:0,behavior:'instant'});});}
}
function navigate(hash){if(location.hash!==hash)history.pushState(null,'',hash);activate(hash,true);}
function enable(){
if(mobileHomeEnabled)return;mobileHomeEnabled=true;body.classList.add('home-mobile');tablist.setAttribute('role','tablist');tabs.forEach(tab=>{tab.setAttribute('role','tab');tab.setAttribute('aria-controls','home-panel-'+tab.dataset.homeTab);});
move('.atlas-hero',panel('overview'),panel('overview').querySelector('.mobile-overview-note'));
move('.headline-rail',document.querySelector('.atlas-hero'),document.querySelector('.hero-meta'));
move('#events',panel('news'));
move('.event-tools',home.querySelector('[data-mobile-search]'));
move('.brief-official',home.querySelector('[data-mobile-official]'));
home.querySelectorAll('.official-row').forEach(row=>{const target=row.querySelector('[data-official-more-content]');row.querySelectorAll('.official-detail:not(.official-window),.official-meta:not(.official-observed),.official-messages').forEach(node=>move(node,target));});
const records=home.querySelector('[data-mobile-records]');
['.activity-summary','.brief.briefing-band','#changes','.fields-fold','#trend','#sources'].forEach(selector=>move(selector,records));
move('.hero-actions',home.querySelector('[data-mobile-tools]'));move('.collection-note',home.querySelector('[data-mobile-tools]'));
eventPage=1;applyEventFilters();activate(location.hash,Boolean(location.hash));
}
function disable(){
if(!mobileHomeEnabled)return;mobileHomeEnabled=false;
placements.reverse().forEach(({node,marker})=>{marker.replaceWith(node)});placements.length=0;
body.classList.remove('home-mobile');delete body.dataset.homeTab;homePanels.forEach(item=>{item.hidden=false;});eventPage=1;applyEventFilters();
}
function sync(){media.matches?enable():disable();}
tabs.forEach((tab,index)=>{tab.addEventListener('click',event=>{if(!mobileHomeEnabled)return;event.preventDefault();navigate(tab.hash);});tab.addEventListener('keydown',event=>{let next;if(event.key==='ArrowRight')next=(index+1)%tabs.length;else if(event.key==='ArrowLeft')next=(index+tabs.length-1)%tabs.length;else if(event.key==='Home')next=0;else if(event.key==='End')next=tabs.length-1;else return;event.preventDefault();tabs[next].focus();navigate(tabs[next].hash);});});
document.addEventListener('click',event=>{if(!mobileHomeEnabled)return;const link=event.target.closest('a[href]');if(!link||link.closest('[data-home-tabs]')||link.target==='_blank'||event.ctrlKey||event.metaKey||event.shiftKey||event.altKey)return;const url=new URL(link.href,location.href);if(url.origin!==location.origin||url.pathname!==location.pathname||!url.hash)return;const id=url.hash.slice(1);if(!['overview','official','records'].includes(id)&&!document.getElementById(id))return;event.preventDefault();navigate(url.hash);});
window.addEventListener('hashchange',()=>activate(location.hash,true));window.addEventListener('popstate',()=>activate(location.hash,true));
if(media.addEventListener)media.addEventListener('change',sync);else media.addListener(sync);sync();
}
setupMobileHome();

// The news rail advances only while nobody is reading or manipulating it.
// A pointer, keyboard or focus interaction pauses it until explicitly restarted.
function setupNewsCarousel(){
const rail=document.querySelector('[data-news-carousel]');if(!rail)return;
const track=rail.querySelector('[data-news-track]'),slides=[...rail.querySelectorAll('[data-news-slide]')],play=rail.querySelector('[data-news-play]'),position=rail.querySelector('[data-news-position]'),buttons=[...rail.querySelectorAll('[data-news-direction]')];
if(!track||!slides.length){rail.querySelector('.headline-controls')?.setAttribute('hidden','');return;}
let index=0,automatic=slides.length>1&&!reducedMotion.matches,scrollTimer;
function renderControls(){
if(position){position.textContent=(index+1)+' / '+slides.length;position.setAttribute('aria-live',automatic?'off':'polite');}
if(play){play.disabled=slides.length<2||reducedMotion.matches;play.textContent=automatic?'정지':'자동';play.setAttribute('aria-pressed',String(automatic));play.setAttribute('aria-label',automatic?'뉴스 자동 넘김 일시정지':reducedMotion.matches?'동작 줄이기 설정으로 자동 넘김 꺼짐':'뉴스 자동 넘김 시작');}
buttons.forEach(button=>button.disabled=slides.length<2);
}
function stop(){automatic=false;renderControls();}
function go(next){
index=(next+slides.length)%slides.length;
track.scrollTo({left:slides[index].offsetLeft-slides[0].offsetLeft,behavior:reducedMotion.matches?'instant':'smooth'});renderControls();
}
buttons.forEach(button=>button.addEventListener('click',()=>{stop();go(index+(button.dataset.newsDirection==='next'?1:-1));}));
play?.addEventListener('click',()=>{automatic=!automatic&&!reducedMotion.matches;renderControls();});
track.addEventListener('pointerdown',stop,{passive:true});track.addEventListener('wheel',stop,{passive:true});
track.addEventListener('focusin',stop);
track.addEventListener('keydown',event=>{let next;if(event.key==='ArrowRight')next=index+1;else if(event.key==='ArrowLeft')next=index-1;else if(event.key==='Home')next=0;else if(event.key==='End')next=slides.length-1;else return;event.preventDefault();stop();go(next);});
track.addEventListener('scroll',()=>{clearTimeout(scrollTimer);scrollTimer=setTimeout(()=>{const origin=slides[0].offsetLeft;index=slides.reduce((nearest,slide,i)=>Math.abs(slide.offsetLeft-origin-track.scrollLeft)<Math.abs(slides[nearest].offsetLeft-origin-track.scrollLeft)?i:nearest,0);renderControls();},100);},{passive:true});
const motionChanged=()=>{if(reducedMotion.matches)stop();else renderControls();};
if(reducedMotion.addEventListener)reducedMotion.addEventListener('change',motionChanged);
setInterval(()=>{if(!automatic||document.visibilityState==='hidden'||rail.closest('[hidden]')||track.contains(document.activeElement))return;go(index+1);},6000);
renderControls();
}
setupNewsCarousel();
})();
