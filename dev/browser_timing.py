"""조회 지연 진단. 본문·헤더·URL 쿼리·화면 텍스트를 수집하지 않는다."""
import time
from urllib.parse import urlsplit
from runtime import atomic_json

SCRIPT = r"""(() => {
 if (window.__astraTiming) return;
 const events=[];
 const emit=(kind,extra={})=>{events.push({kind,at:Date.now(),mono:performance.now(),...extra});if(events.length>1500)events.shift();};
 window.__astraTiming={drain:()=>events.splice(0)};
 emit('document-start',{visibility:document.visibilityState});
 for(const type of ['DOMContentLoaded','load','pageshow'])window.addEventListener(type,()=>emit(type),{once:true});
 document.addEventListener('visibilitychange',()=>emit('visibility',{visibility:document.visibilityState}));
 document.addEventListener('freeze',()=>emit('freeze'));
 document.addEventListener('resume',()=>emit('resume'));
 document.addEventListener('click',e=>{
   const cell=e.target.closest?.('[id^="dep-fare-"]');
   if(cell)emit('date-click');
   else if(e.target.closest?.('button'))emit('button-click');
 },true);
 try{new PerformanceObserver(list=>{for(const e of list.getEntries())emit('long-task',{startMono:e.startTime,duration:e.duration});}).observe({type:'longtask',buffered:true});}catch(_){}
 let last=performance.now();setInterval(()=>{const now=performance.now(),lag=now-last-250;last=now;if(lag>100)emit('timer-lag',{lagMs:lag,visibility:document.visibilityState});},250);
})();"""


def safe_path(url):
    parsed=urlsplit(url)
    if parsed.hostname!='www.koreanair.com':return None
    path=parsed.path
    if path.startswith(('/api/','/booking/')) or path.endswith('.js'):return path
    return None


class BrowserTiming:
    def __init__(self,page,folder):
        self.page,self.folder=page,folder
        self.events=[]
        self.cdp=page.context.new_cdp_session(page)
        self.cdp.send('Network.enable')
        self.cdp.on('Network.requestWillBeSent',self.request)
        self.cdp.on('Network.loadingFinished',lambda e:self.network_end(e,'network-end'))
        self.cdp.on('Network.loadingFailed',lambda e:self.network_end(e,'network-failed'))
        self.pending={}
        page.add_init_script(SCRIPT)
        page.evaluate(SCRIPT)

    def request(self,e):
        path=safe_path(e['request']['url'])
        if not path:return
        self.pending[e['requestId']]=path
        stack=e.get('initiator',{}).get('stack',{}).get('callFrames',[])
        callers=[{'path':safe_path(f.get('url','')),'line':f.get('lineNumber')} for f in stack[:5]]
        self.events.append({'kind':'request','path':path,'id':e['requestId'],
            'at':e['wallTime']*1000,'cdpMono':e['timestamp'],'type':e.get('type'),
            'initiator':e.get('initiator',{}).get('type'),'callers':callers})

    def network_end(self,e,kind):
        path=self.pending.pop(e['requestId'],None)
        if path:self.events.append({'kind':kind,'path':path,'id':e['requestId'],
            'cdpMono':e['timestamp'],'observedAt':time.time()*1000})

    def collect(self):
        try:self.events.extend(self.page.evaluate('window.__astraTiming?.drain() || []'))
        except Exception:self.events.append({'kind':'collection-unavailable','at':time.time()*1000})

    def save(self):
        self.collect()
        atomic_json(self.folder/'browser_timing.json',{'events':self.events,'pending':list(self.pending.values()),
            'interpretation':'CDP 요청 시각·브라우저 긴 작업·타이머 지연. 타이머 지연만으로 CPU 원인을 확정하지 않음.'})

    def close(self):
        self.save()
        self.cdp.detach()
