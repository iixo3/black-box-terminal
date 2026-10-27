(() => {
  'use strict';
  const { categories, records } = window.BBT_DATA;
  const $ = (selector) => document.querySelector(selector);
  const escape = (value) => String(value).replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
  const captureDay = record => new Intl.DateTimeFormat('en-CA', {timeZone:'UTC', year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date(record.captured));
  const sorted = [...records].sort((a,b) => b.captured.localeCompare(a.captured));
  const dateLabel = record => new Intl.DateTimeFormat('en-GB',{timeZone:'UTC',day:'numeric',month:'short'}).format(new Date(record.captured));
  const categoryName = id => categories.find(category => category.id === id)?.name || '';
  const main = $('#archive');
  const reader = $('#reader');
  const mobileNav = $('.mobile-categories');
  const state = {view:'categories', category:'assets', query:'', period:'month'};
  const scrollRouter = window.BBT_SCROLL.install(main, reader);
  const typeface = $('#typeface');
  const query = new URLSearchParams(location.search);
  if(query.has('embedded')) {
    document.documentElement.dataset.embedded='true';
    $('.brand').href='index.html?embedded=1';
  }
  function applyTypeface(value) {
    if(!['manrope','space','outfit'].includes(value))value='outfit';
    scrollRouter.reset();
    document.documentElement.dataset.typeface=value;
    typeface.value=value;
  }
  let savedTypeface='outfit';
  try { savedTypeface=localStorage.getItem('bbt-type-study-outfit-default')||savedTypeface; } catch (_) {}
  applyTypeface(query.get('type')||savedTypeface);
  typeface.addEventListener('change',()=>{
    applyTypeface(typeface.value);
    try { localStorage.setItem('bbt-type-study-outfit-default',typeface.value); } catch (_) {}
    const url=new URL(location.href);url.searchParams.set('type',typeface.value);
    history.replaceState(history.state,'',url);
  });
  const positions = new Map();
  let readerIds = [], currentId = null;

  function filteredRecords() {
    const words = state.query.toLocaleLowerCase().trim().split(/\s+/).filter(Boolean);
    return sorted.filter(record => {
      const date = captureDay(record);
      if (state.period === 'week' && date < '2026-09-24') return false;
      if (state.period === 'early' && date > '2026-09-15') return false;
      const haystack = [record.title,record.summary,record.kind,record.publisher,categoryName(record.category),...record.entities].join(' ').toLocaleLowerCase();
      return words.every(word => haystack.includes(word));
    });
  }
  function rememberScroll() {
    main.querySelectorAll('[data-scroll]').forEach(element => positions.set(element.dataset.scroll, element.scrollTop));
  }
  function restoreScroll() {
    main.querySelectorAll('[data-scroll]').forEach(element => { element.scrollTop = positions.get(element.dataset.scroll) || 0; });
  }
  function card(record, showCategory = false) {
    return `<button class="news-card" data-record="${record.id}" aria-haspopup="dialog" aria-label="Read: ${escape(record.title)}">
      <span class="news-meta"><span>${escape(showCategory ? categoryName(record.category) : dateLabel(record))}</span><span class="meta-divider">/</span><span class="article-type">${escape(record.kind)}</span></span>
      <h3>${escape(record.title)}</h3><p class="news-excerpt">${escape(record.summary)}</p>
      <span class="card-bottom"><span>${escape(record.publisher)}${record.roundup?' · Roundup':''}</span><span class="card-arrow" aria-hidden="true">↗</span></span></button>`;
  }
  function updateCount(items) {
    const mobile = matchMedia('(max-width:800px)').matches;
    const count = mobile && state.view==='categories' ? items.filter(record => record.category===state.category).length : items.length;
    $('#record-count').textContent = `${String(count).padStart(2,'0')} ${mobile&&state.view==='categories'?'in category':'records'} / ${records.length} in sample`;
  }
  function render() {
    scrollRouter.reset();
    const items = filteredRecords();
    mobileNav.hidden = state.view !== 'categories';
    mobileNav.innerHTML = categories.map(category => `<button class="mobile-category" data-category="${category.id}" aria-pressed="${state.category===category.id}">${escape(category.name)}</button>`).join('');
    main.setAttribute('aria-labelledby',`${state.view}-tab`);
    if (state.view === 'categories') {
      main.innerHTML = `<div class="columns">${categories.map((category,index) => {
        const categoryItems = items.filter(record => record.category===category.id);
        return `<section class="news-column ${state.category===category.id?'selected':''}" data-column="${category.id}" aria-labelledby="heading-${category.id}"><header class="column-header"><div class="column-index"><span class="number">0${index+1}</span><span>${String(categoryItems.length).padStart(2,'0')} RECORDS</span></div><h2 id="heading-${category.id}">${escape(category.name)}</h2><p>${escape(category.description)}</p></header><div class="column-feed" data-scroll="${category.id}" tabindex="0" aria-label="${escape(category.name)} news">${categoryItems.length?categoryItems.map(record=>card(record)).join(''):'<p class="empty-column">No records in this category for your current search or date range.</p>'}</div></section>`;
      }).join('')}</div>`;
    } else if (items.length) {
      const days = [...new Set(items.map(captureDay))];
      main.innerHTML = `<div class="timeline-scroll" data-scroll="chronology" tabindex="0" aria-label="News by date"><div class="timeline-heading"><h2>The September record</h2><p>BY CAPTURE DATE ↓</p></div>${days.map(day => {
        const date = new Date(`${day}T12:00:00+02:00`);
        const weekday = new Intl.DateTimeFormat('en-GB',{weekday:'short',timeZone:'UTC'}).format(date);
        return `<section class="day-group" aria-label="${day}"><div class="day-label"><strong>${day.slice(8)}</strong><span>SEP / ${weekday.toUpperCase()}</span></div><div class="day-items">${items.filter(record=>captureDay(record)===day).map(record=>card(record,true)).join('')}</div></section>`;
      }).join('')}</div>`;
    } else {
      main.innerHTML = '<div class="empty-state"><h2>No matching records.</h2><p>Try another name or topic, or broaden the date range.</p><button class="reset-search">Reset search & date range →</button></div>';
    }
    restoreScroll();
    updateCount(items);
  }
  function setView(view, focus=false) {
    rememberScroll();
    state.view=view;
    document.querySelectorAll('[data-view]').forEach(button=>{
      const active=button.dataset.view===view;
      button.classList.toggle('active',active);button.setAttribute('aria-selected',String(active));button.tabIndex=active?0:-1;
      if(active&&focus)button.focus();
    });
    render();
  }
  function selectCategory(category) {
    state.category=category;
    mobileNav.querySelectorAll('[data-category]').forEach(button=>button.setAttribute('aria-pressed',String(button.dataset.category===category)));
    main.querySelectorAll('[data-column]').forEach(column=>column.classList.toggle('selected',column.dataset.column===category));
    mobileNav.querySelector(`[data-category="${category}"]`)?.scrollIntoView({block:'nearest',inline:'nearest',behavior:'auto'});
    restoreScroll();updateCount(filteredRecords());
  }
  function drawArticle(record) {
    const index=readerIds.indexOf(record.id);
    $('#reader-content').innerHTML=`<div class="reader-kicker"><span>${escape(categoryName(record.category))}</span><span>/</span><span>${escape(record.kind)}</span></div><h2 id="reader-title">${escape(record.title)}</h2><div class="reader-dateline">CAPTURED ${escape(dateLabel(record))} 2026 · ${escape(record.publisher)}${record.roundup?' WEEKLY ROUNDUP':''}</div><p class="reader-summary">${escape(record.summary)}</p>${record.roundup?'<p class="reader-context">The date above is the roundup capture date. The underlying development may have happened earlier.</p>':''}<div class="reader-entities" aria-label="Mentioned in this record">${record.entities.map(entity=>`<span>${escape(entity)}</span>`).join('')}</div><section class="source-block"><h3>Follow the source</h3><a class="source-link" href="${escape(record.sourceUrl)}" target="_blank" rel="noopener noreferrer"><span>${escape(record.sourceLabel)}</span><span aria-hidden="true">↗</span><span class="sr-only"> (opens in a new tab)</span></a><p class="source-detail">${escape(record.sourceDetail)}</p>${record.sourceUrl!==record.captureUrl?`<a class="capture-link" href="${escape(record.captureUrl)}" target="_blank" rel="noopener noreferrer">View original Telegram capture ↗<span class="sr-only"> (opens in a new tab)</span></a>`:''}</section><p class="sample-note">DESIGN SAMPLE / September test material.<br>This record has not been independently verified for publication.</p>`;
    $('#reader-content').scrollTop=0;
    $('#reader-position').textContent=`${String(index+1).padStart(2,'0')} / ${String(readerIds.length).padStart(2,'0')}`;
    $('#previous-record').disabled=index<=0;$('#next-record').disabled=index>=readerIds.length-1;
  }
  function openArticle(id, push=true) {
    const record=records.find(item=>item.id===id);if(!record)return;
    if(!reader.open){
      readerIds=filteredRecords().filter(item=>state.view==='chronology'||item.category===record.category).map(item=>item.id);
      if(!readerIds.includes(id))readerIds=[id];
    }
    scrollRouter.reset();
    currentId=id;drawArticle(record);
    if(push){
      const url=`${location.pathname}${location.search}#record=${id}`;
      if(reader.open)history.replaceState({bbtReader:true},'',url);
      else history.pushState({bbtReader:true},'',url);
    }
    if(!reader.open){reader.showModal();$('#close-reader').focus();}
  }
  function requestClose(){
    if(history.state?.bbtReader){history.back();}
    else {history.replaceState(null,'',`${location.pathname}${location.search}`);reader.close();currentId=null;}
  }
  function syncHash(){
    const id=new URLSearchParams(location.hash.slice(1)).get('record');
    if(id&&records.some(record=>record.id===id)){openArticle(id,false);}
    else if(reader.open){reader.close();currentId=null;}
  }
  $('.view-tabs').addEventListener('click',event=>{const button=event.target.closest('[data-view]');if(button)setView(button.dataset.view);});
  $('.view-tabs').addEventListener('keydown',event=>{if(['ArrowLeft','ArrowRight','Home','End'].includes(event.key)){event.preventDefault();setView(event.key==='Home'?'categories':event.key==='End'?'chronology':state.view==='categories'?'chronology':'categories',true);}});
  mobileNav.addEventListener('click',event=>{const button=event.target.closest('[data-category]');if(button){rememberScroll();selectCategory(button.dataset.category);}});
  main.addEventListener('click',event=>{
    const button=event.target.closest('[data-record]');if(button)openArticle(button.dataset.record);
    if(event.target.closest('.reset-search')){state.query='';state.period='month';$('#search').value='';$('#period').value='month';positions.clear();render();$('#search').focus();}
  });
  main.addEventListener('pointermove',event=>{
    if(event.pointerType!=='mouse'||matchMedia('(prefers-reduced-motion:reduce)').matches)return;
    const card=event.target.closest('.news-card');if(!card)return;
    const bounds=card.getBoundingClientRect();
    card.style.setProperty('--light-x',`${(event.clientX-bounds.left)/bounds.width*100}%`);
    card.style.setProperty('--light-y',`${(event.clientY-bounds.top)/bounds.height*100}%`);
  },{passive:true});
  $('#search').addEventListener('input',event=>{state.query=event.target.value;positions.clear();render();});
  $('#period').addEventListener('change',event=>{state.period=event.target.value;positions.clear();render();});
  $('#close-reader').addEventListener('click',requestClose);
  reader.addEventListener('cancel',event=>{event.preventDefault();requestClose();});
  reader.addEventListener('click',event=>{const bounds=reader.getBoundingClientRect();if(event.target===reader&&(event.clientX<bounds.left||event.clientX>bounds.right||event.clientY<bounds.top||event.clientY>bounds.bottom))requestClose();});
  $('#previous-record').addEventListener('click',()=>openArticle(readerIds[readerIds.indexOf(currentId)-1]));
  $('#next-record').addEventListener('click',()=>openArticle(readerIds[readerIds.indexOf(currentId)+1]));
  window.addEventListener('popstate',syncHash);
  window.addEventListener('resize',()=>updateCount(filteredRecords()));
  document.addEventListener('keydown',event=>{if(event.key==='/'&&!reader.open&&!['INPUT','TEXTAREA','SELECT'].includes(document.activeElement.tagName)){event.preventDefault();$('#search').focus();}});
  render();syncHash();
})();
