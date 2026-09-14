"""저장된 사이트 복원 라이브러리 실행 시험. 전체 Angular 앱/결제 화면 시험 아님."""
import hashlib
from pathlib import Path
import unittest

import test_state_bridge as fixtures
import state_bridge

SOURCE = Path(__file__).resolve().parents[2] / 'flight_booking_astra' / 'dev-shots' / 'api-source-2026-09-10' / '5064.f719afa8cfe71e62.js'
SHA = '96643181f464c3a67439ade352d0cdd38d10ea3c4719176fa5bd358457beefd7'
BOOT = '''() => {
 const modules={},cache={};self.webpackChunknonCmsShell=[];
 self.webpackChunknonCmsShell.push=c=>Object.assign(modules,c[1]);
 const req=id=>{if(cache[id])return cache[id].exports;
   if(!modules[id])throw Error('missing-archive-module');
   const m={exports:{}};cache[id]=m;
   try{modules[id](m,m.exports,req);}catch(e){delete cache[id];throw e;}
   return m.exports;};
 req.d=(o,d)=>{for(const k in d)if(!Object.prototype.hasOwnProperty.call(o,k))
   Object.defineProperty(o,k,{enumerable:true,get:d[k]});};
 self.archiveRequire=req;
}'''


class SiteLibraryTests(fixtures.Fixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not SOURCE.exists():
            raise RuntimeError('saved-site-source-missing')
        raw=SOURCE.read_bytes()
        if hashlib.sha256(raw).hexdigest()!=SHA:
            raise RuntimeError('saved-site-source-changed')
        cls.source=raw.decode('utf-8')
        cls.pw=fixtures.sync_playwright().start()
        cls.browser=cls.pw.chromium.launch(headless=True,args=['--host-resolver-rules=MAP * ~NOTFOUND'])

    @classmethod
    def tearDownClass(cls):
        cls.browser.close();cls.pw.stop()

    def setUp(self):
        self.setup_fixture()
        self.ctx=self.browser.new_context(service_workers='block');self.addCleanup(self.ctx.close)
        self.hits=[]
        def local(route):
            self.hits.append(route.request.url)
            route.fulfill(status=200,content_type='text/html',body='<title>isolated library</title>')
        self.ctx.route('**/*',local)
        self.pg=self.ctx.new_page();self.pg.goto('https://bridge.invalid/')
        self.pg.evaluate('(s)=>Object.entries(s).forEach(([k,v])=>sessionStorage.setItem(k,v))',self.storage)
        self.patch=self.build()
        state_bridge.fixture_apply(self.pg,self.patch)
        self.pg.evaluate(BOOT);self.pg.add_script_tag(content=self.source)

    def test_actual_library_rehydrates_validated_models(self):
        result=self.pg.evaluate('''() => {
          const sync=archiveRequire(42584).tM;
          const reducer=sync({keys:['fareInformation','inputTravellers'],
            rehydrate:true,storage:sessionStorage,restoreDates:false,
            mergeReducer:(s,p,a)=>((a.type==='@ngrx/store/init'||a.type==='@ngrx/store/update-reducers')&&p?Object.assign({},s,p):s)
          })((s={})=>s);
          const state=reducer(undefined,{type:'@ngrx/store/init'});
          return {referenceMatches:state.inputTravellers.model.pnr==='FAKEPNR',
            amount:state.inputTravellers.model.pnrFareInfo.totalAmount,
            pending:state.inputTravellers.isPending,
            fareTicketMatches:state.fareInformation.model.pageTicket==='FAKE-TICKET'};
        }''')
        self.assertEqual(result,{'referenceMatches':True,'amount':'325500',
                                 'pending':False,'fareTicketMatches':True})
        self.assertEqual(len(self.hits),1)

    def test_library_snapshots_storage_when_constructed(self):
        result=self.pg.evaluate('''() => {
          const sync=archiveRequire(42584).tM;
          const reducer=sync({keys:['inputTravellers'],rehydrate:true,storage:sessionStorage,
            restoreDates:false})((s={})=>s);
          sessionStorage.setItem('inputTravellers',JSON.stringify({model:{pnr:'CHANGED'}}));
          return reducer(undefined,{type:'@ngrx/store/init'}).inputTravellers.model.pnr==='FAKEPNR';
        }''')
        self.assertTrue(result)


if __name__=='__main__':unittest.main()
