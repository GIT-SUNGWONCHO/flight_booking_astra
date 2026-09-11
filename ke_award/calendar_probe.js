/* 달력 전용 읽기 도구. 예약 매크로를 포함하지 않는다. */
(function (root) {
  'use strict';
  function airport(value) { return String(value || '').toUpperCase().replace(/^SEL$/, 'ICN'); }
  function project(data, target, origin, destination, requestSegments) {
    if (!data || typeof data !== 'object') return {state:'schema-error', p:null};
    if (data.code !== undefined && data.code !== null && data.code !== '')
      return {state:'application-error', code:String(data.code), p:null};
    if (!Array.isArray(data.boundFareCalendarList)) return {state:'schema-error', p:null};
    let bounds = data.boundFareCalendarList.filter(b => airport(b.departureAirport) === airport(origin)
      && airport(b.arrivalAirport) === airport(destination));
    // 실제 응답은 공항 필드가 없다. 동일 응답의 요청이 검증된 편도일 때만 대응한다.
    const requestMatches = Array.isArray(requestSegments) && requestSegments.length === 1
      && airport(requestSegments[0].departureAirport) === airport(origin)
      && airport(requestSegments[0].arrivalAirport) === airport(destination);
    if (requestSegments !== undefined && !requestMatches) return {state:'route-mismatch', p:null};
    if (!bounds.length && requestMatches && data.boundFareCalendarList.length === 1
      && !data.boundFareCalendarList[0].departureAirport && !data.boundFareCalendarList[0].arrivalAirport)
      bounds = data.boundFareCalendarList;
    if (bounds.length !== 1) return {state:'route-mismatch', p:null,
      observedBounds:data.boundFareCalendarList.map(b=>({origin:b.departureAirport,destination:b.arrivalAirport,count:b.fareCalendarList?.length}))};
    if (!Array.isArray(bounds[0].fareCalendarList)) return {state:'schema-error', p:null};
    const date = target.replace(/-/g, '');
    const rows = bounds[0].fareCalendarList.filter(d => d.departureDate === date);
    if (!rows.length) return {state:'date-not-listed', p:null};
    if (rows.length !== 1) return {state:'ambiguous-date', p:null};
    const d = rows[0], fs = d.fareFamilyList, ss = d.fareFamilyStatus;
    if (d.emptyFare === true && (!fs || !fs.length))
      return {state:'valid', p:false, date, emptyFare:true, emptyCode:d.emptyCode || null, currency:d.currency};
    if (!Array.isArray(fs) || !Array.isArray(ss) || ss.length !== fs.length)
      return {state:'schema-error', p:null, date};
    // 실제 사이트 checkCff: family 일치 + 같은 인덱스 status != SOLDOUT.
    const p = fs.some((f,i) => f === 'KEBONUSPR' && ss[i] !== 'SOLDOUT');
    return {state:'valid', p, date, families:fs, statuses:ss, emptyFare:d.emptyFare,
      emptyCode:d.emptyCode || null, currency:d.currency};
  }
  function dom(target) {
    const parts = target.split('-'), month = +parts[parts.length-2], day = +parts[parts.length-1];
    const cells = [...document.querySelectorAll('[id^="dep-fare-"]')].filter(e => {
      const t = e.textContent || '', m = t.match(/(\d{1,2})월\s*(\d{1,2})일/);
      return m && +m[1] === month && +m[2] === day && e.getClientRects().length;
    });
    if (cells.length !== 1) return {state:cells.length ? 'ambiguous-cell':'date-not-rendered',p:null};
    const e = cells[0], disabled = e.getAttribute('aria-disabled') === 'true' || e.classList.contains('-disabled');
    const p = !!e.querySelector('.-legend-prestige') || /프레스티지/.test(e.textContent || '');
    return {state:'observed',p,disabled,cell:e.id,documentReady:document.readyState};
  }
  function start(config, source) {
    const endpoint = '/api/ap/booking/avail/calendarFareMatrix';
    const u = new URL(source.url, location.href);
    if (u.origin !== location.origin || u.pathname !== endpoint || source.method !== 'POST')
      throw new Error('calendar endpoint only');
    const body = JSON.parse(source.body);
    const gap=config.gapMs || 1000, maxInflight=config.maxInflight || 2;
    const maxStartDelay=config.maxStartDelayMs === undefined ? 150 : config.maxStartDelayMs;
    if(gap<250 || gap>1000 || !Number.isInteger(maxInflight) || maxInflight<1 || maxInflight>4
       || !Number.isFinite(maxStartDelay) || maxStartDelay<0 || maxStartDelay>200)
      throw new Error('measurement rate outside tested bounds');
    if (!Array.isArray(body.segmentList) || body.segmentList.length !== 1
      || airport(body.segmentList[0].departureAirport) !== airport(config.origin)
      || airport(body.segmentList[0].arrivalAirport) !== airport(config.destination))
      throw new Error('captured route mismatch');
    body.segmentList[0].departureDate = config.target.replace(/-/g,'');
    const state = {events:[],active:0,seq:0,stopped:false,backoffUntil:0};
    const emit = e => state.events.push(e);
    const controllers = new Set();
    let timer;
    async function ask(scheduledAt) {
      const id = ++state.seq, queuedAt = Date.now();
      // 직전 응답이 2초를 몇 ms 넘겼다는 이유로 1초 슬롯 전체를 버리지 않는다.
      // 최대150ms만 기다리고, 끝내 자리가 없으면 누락으로 기록한다. 동시 수는 늘리지 않는다.
      if(state.active>=maxInflight && !state.stopped) {
        while(state.active>=maxInflight && Date.now()-queuedAt<maxStartDelay && !state.stopped)
          await new Promise(resolve=>setTimeout(resolve,5));
        emit({kind:'queued',id,scheduledAt,at:Date.now(),waitMs:Date.now()-queuedAt});
      }
      if(state.stopped)return;
      const sentAt = Date.now();
      // 슬롯이 한 주기 이상 늦었거나 측정이 끝났으면 뒤늦게 요청하지 않는다.
      // 150ms는 슬롯 대기 한도이지 브라우저 타이머의 실행 보장이 아니다.
      if(sentAt>=config.endAt || sentAt-scheduledAt>=gap) {
        emit({kind:'skipped',id,scheduledAt,at:sentAt,reason:sentAt>=config.endAt?'measurement-ended':'stale-slot'});return;
      }
      if (state.active >= maxInflight || sentAt < state.backoffUntil) {
        emit({kind:'skipped',id,scheduledAt,at:sentAt,reason:state.active>=maxInflight?'inflight-limit':'server-backoff'}); return;
      }
      const headers = {...source.headers};
      for (const k of Object.keys(headers)) if(k.toLowerCase() === 'timestamp') headers[k]=String(sentAt);
      const controller = new AbortController(); controllers.add(controller); state.active++;
      const startedMono=performance.now(),timeoutAt=sentAt+6000;
      const abort = setTimeout(()=>{
        emit({kind:'request-timeout',id,at:Date.now(),dueAt:timeoutAt,lagMs:Date.now()-timeoutAt});
        controller.abort();
      },6000);
      emit({kind:'sent',id,scheduledAt,sentAt,lagMs:sentAt-scheduledAt});
      try {
        const response = await fetch(u.href,{method:'POST',credentials:'include',headers,
          body:JSON.stringify(body),cache:'no-store',signal:controller.signal});
        const headersAt = Date.now(), text = await response.text(), receivedAt = Date.now();
        let result;
        try { result=project(JSON.parse(text),config.target,config.origin,config.destination,body.segmentList); }
        catch (_) { result={state:'invalid-json',p:null}; }
        if (!response.ok) result={state:'http-error',p:null};
        const resource=performance.getEntriesByName(u.href).filter(e=>e.initiatorType==='fetch'
          && e.startTime>=startedMono-2 && e.startTime<=startedMono+100).at(-1);
        const network=resource ? {startedAt:performance.timeOrigin+resource.startTime,
          receivedAt:performance.timeOrigin+resource.responseEnd,durationMs:resource.responseEnd-resource.startTime,
          callbackLagMs:receivedAt-(performance.timeOrigin+resource.responseEnd)} : null;
        emit({kind:'response',id,scheduledAt,sentAt,headersAt,receivedAt,readAt:Date.now(),network,
          durationMs:performance.now()-startedMono,status:response.status,
          cache:{age:response.headers.get('age'),date:response.headers.get('date'),control:response.headers.get('cache-control')},...result});
        if (response.status===429 || result.code==='503') state.backoffUntil=Date.now()+2000;
        if (response.status===401 || response.status===403) {state.stopped=true;clearTimeout(timer);}
      } catch(e) {
        emit({kind:'response',id,scheduledAt,sentAt,receivedAt:Date.now(),durationMs:performance.now()-startedMono,
          state:'network-error',p:null,error:e.name});
      } finally {clearTimeout(abort);controllers.delete(controller);state.active--;}
    }
    let slot=0;
    function tick() {
      const scheduledAt=config.startAt+slot*gap;
      if(state.stopped || scheduledAt>=config.endAt) return;
      timer=setTimeout(()=>{
        if(state.stopped)return;
        const now=Date.now(), missed=Math.floor((now-scheduledAt)/gap);
        if(missed>0)emit({kind:'timer-gap',at:now,scheduledAt,missedSlots:missed});
        if(now>config.endAt+50){emit({kind:'expired-slot',at:now,scheduledAt});return;}
        ask(scheduledAt);
        // 지연된 슬롯을 한꺼번에 쏘지 않는다. 지연 자체는 위 이벤트와 lagMs에 남긴다.
        slot=Math.max(slot+1,Math.floor((now-config.startAt)/gap)+1);tick();
      },Math.max(0,scheduledAt-Date.now()));
    }
    tick();
    return {drain:()=>state.events.splice(0),status:()=>({active:state.active,stopped:state.stopped,seq:state.seq}),
      stop:()=>{state.stopped=true;clearTimeout(timer);for(const c of controllers)c.abort();}};
  }
  async function startWorker(config,source,script) {
    const workerSource=script+'\nlet sampler; onmessage=e=>{if(e.data.op==="start"){try{sampler=ASTRA_CALENDAR.start(e.data.config,e.data.source);postMessage({ready:true});}catch(x){postMessage({error:x.name});}}else if(e.data.op==="stop"){sampler?.stop();}};setInterval(()=>{if(sampler){const events=sampler.drain();if(events.length)postMessage({events});}},50);';
    const blob=new Blob([workerSource],{type:'application/javascript'}),url=URL.createObjectURL(blob);
    const worker=new Worker(url), events=[];
    await new Promise((resolve,reject)=>{
      const deadline=setTimeout(()=>reject(new Error('worker-start-timeout')),3000);
      worker.onmessage=e=>{if(e.data.ready){clearTimeout(deadline);resolve();}if(e.data.error){clearTimeout(deadline);reject(new Error(e.data.error));}if(e.data.events)events.push(...e.data.events);};
      worker.onerror=()=>{clearTimeout(deadline);reject(new Error('worker-error'));};
      worker.postMessage({op:'start',config,source});
    }).catch(e=>{worker.terminate();URL.revokeObjectURL(url);throw e;});
    return {drain:()=>events.splice(0),stop:()=>{worker.postMessage({op:'stop'});setTimeout(()=>{worker.terminate();URL.revokeObjectURL(url);},100);}};
  }
  const api={project,dom,start,startWorker};
  if(typeof module!=='undefined' && module.exports) module.exports=api;
  else root.ASTRA_CALENDAR=api;
})(typeof window!=='undefined'?window:globalThis);
