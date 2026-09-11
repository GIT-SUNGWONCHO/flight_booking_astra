"""9242 정상 UI의 수동 흐름을 수동적으로 기록한다. 클릭/재조회/주문 전송 없음."""
import argparse
import hashlib
import json
import os
import time
import math
import uuid
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit, parse_qs

from contracts import ContractRecorder, PATHS
from evidence import FlowEvidence

ROOT = Path(__file__).resolve().parents[1]


def request_input(request):
    if request.method == 'GET':
        return {k: v[0] if len(v) == 1 else v for k, v in parse_qs(urlsplit(request.url).query).items()}
    text = request.post_data
    if not text:
        return None
    if len(text) > 2_000_000:
        raise ValueError('request-size-limit')
    return json.loads(text)


class Collector:
    def __init__(self, folder, reference_at=None, target=None):
        self.folder = folder
        self.recorder = ContractRecorder()
        self.requests = {}
        self.errors = []
        self.assets = []
        self.assets_seen = set()
        self.events = []
        self.ledger = {}
        self.started_wall = time.time()
        self.started_mono = time.monotonic()
        self.reference_at = reference_at
        self.run_id = folder.name
        self.target = target or {}
        self.flow = FlowEvidence(self.target)
        self.outside = {}
        self.hashes = {name: hashlib.sha256((Path(__file__).parent / name).read_bytes()).hexdigest()
                       for name in ('collect.py', 'contracts.py', 'evidence.py')}

    def stamp(self):
        return {'callbackAt': time.time(), 'elapsedMs': (time.monotonic()-self.started_mono)*1000}

    def update_timing(self, request):
        entry = self.ledger[self.requests[request]]
        try:
            timing = request.timing
            raw = {k: v for k, v in timing.items() if k in
                   ('startTime', 'requestStart', 'responseStart', 'responseEnd')
                   and type(v) in (int, float) and math.isfinite(v) and v >= 0}
            entry['browserTimingMs'] = raw
            start = raw.get('startTime')
            if start is not None:
                moments = {'requestInitiatedAt': start/1000}
                for key, label in [('requestStart','requestSendStartAt'),
                                   ('responseStart','responseFirstByteAt'),('responseEnd','responseCompleteAt')]:
                    if key in raw:
                        moments[label] = (start+raw[key])/1000
                entry['browserTimes'] = moments
                if self.reference_at is not None:
                    entry['secondsFromReference'] = {k: v-self.reference_at for k,v in moments.items()}
        except Exception as error:
            entry['timingError'] = type(error).__name__

    def summary(self):
        entries = list(self.ledger.values())
        pending = [e['requestId'] for e in entries if e['state'] == 'pending']
        return {'started':len(entries), 'pendingRequestIds':pending,
                'incompleteRequestIds':[e['requestId'] for e in entries if not e.get('responseRecorded',False)],
                'networkFailed':sum(e['state']=='network-failed' for e in entries),
                'responsesRecorded':sum(e.get('responseRecorded',False) for e in entries),
                'httpNon200':sum(e.get('httpStatus',200)!=200 for e in entries),
                'allTrackedRequestsRecorded': bool(entries) and not pending and not self.errors
                    and all(e.get('responseRecorded',False) for e in entries),
                'fullBookingFlowVerified':False, 'seatHoldVerified':False}


    def started(self, request):
        u = urlsplit(request.url)
        if u.hostname == 'www.koreanair.com' and u.path.startswith('/api/') and u.path not in PATHS:
            # 경로 자체에 회원·주문 ID가 들어갈 수 있으므로 원문 경로도 저장하지 않는다.
            if request not in self.outside:
                self.outside[request] = {'id':len(self.outside)+1,'state':'pending','method':request.method,
                                        'started':self.stamp(),'bodyCaptured':False}
        if u.hostname != 'www.koreanair.com' or u.path not in PATHS:
            return
        identity = len(self.requests) + 1
        if request in self.requests:
            return
        self.requests[request] = identity
        self.flow.begin(u.path)
        self.ledger[identity] = {'requestId':identity,'path':u.path,'method':request.method,
                                 'state':'pending','requestCallback':self.stamp()}
        self.update_timing(request)
        try:
            data = request_input(request)
            self.ledger[identity]['requestEvidence'] = self.flow.observe(u.path,'request',data,identity)
            self.recorder.record(u.path, 'request', self.ledger[identity]['requestCallback']['callbackAt'], data, identity,
                                 method=request.method)
            self.ledger[identity]['requestParseCompleted'] = self.stamp()
        except Exception as error:
            self.errors.append({'requestId':identity, 'path': u.path, 'phase': 'request', 'error': type(error).__name__})

    def responded(self, response):
        request = response.request
        entry = self.ledger.get(self.requests.get(request))
        if entry is not None:
            entry['responseHeadersCallback'] = self.stamp()
            entry['httpStatus'] = response.status
            self.update_timing(request)

    def finished(self, request):
        u = urlsplit(request.url)
        entry = self.ledger.get(self.requests.get(request))
        if entry is not None:
            entry['finishedCallback'] = self.stamp()
            self.update_timing(request)
            entry['state'] = 'finished'
        try:
            response = request.response()
        except Exception as error:
            response = None
        if not response:
            if entry is not None:
                self.errors.append({'requestId':entry['requestId'],'path':u.path,
                                    'phase':'response','error':'response-unavailable'})
            return
        if request in self.outside:
            self.outside[request].update(state='finished',httpStatus=response.status,finished=self.stamp())
        # 공개 JS만 저장. 인증·결제 제공자·탐지 스크립트 본문은 저장하지 않는다.
        if (u.hostname == 'www.koreanair.com' and u.path.startswith(('/booking/', '/payment/', '/shell/'))
                and u.path.endswith('.js') and u.path not in self.assets_seen and len(self.assets) < 60):
            self.assets_seen.add(u.path)
            if response.status != 200:
                self.events.append({'kind': 'public-source-status', 'status': response.status})
                return
            try:
                body = response.body()
                if len(body) > 12_000_000:
                    raise ValueError('source-size-limit')
                name = 'source-' + hashlib.sha256(u.path.encode()).hexdigest()[:12] + '.js'
                (self.folder / name).write_bytes(body)
                self.assets.append({'path': u.path, 'file': name, 'bytes': len(body),
                                    'sha256': hashlib.sha256(body).hexdigest()})
            except Exception as error:
                self.errors.append({'phase': 'public-source', 'error': type(error).__name__})
        if request not in self.requests:
            return
        entry['httpStatus'] = response.status
        timing = entry.get('browserTimingMs',{})
        received = entry.get('browserTimes',{}).get('responseCompleteAt')
        if received is None:
            received = entry['finishedCallback']['callbackAt']
            entry['responseTimeSource'] = 'python-callback-fallback'
        else:
            entry['responseTimeSource'] = 'browser-responseEnd'
        try:
            body = response.body()
            if len(body) > 5_000_000:
                raise ValueError('response-size-limit')
            data = json.loads(body)
            self.recorder.record(u.path, 'response', received, data, self.requests[request],
                                 httpStatus=response.status, networkMs=timing.get('responseEnd'))
            entry['responseEvidence'] = self.flow.observe(u.path,'response',data,entry['requestId'],response.status)
            entry['responseRecorded'] = True
            entry['parseCompleted'] = self.stamp()
        except Exception as error:
            self.errors.append({'requestId':entry['requestId'], 'path': u.path, 'phase': 'response', 'httpStatus': response.status,
                                'error': type(error).__name__})
        self.events.append({'kind': 'api-status', 'path': u.path, 'at': received, 'status': response.status,
                            'bodyMeaningVerified': False})

    def failed(self, request):
        if request in self.outside:
            self.outside[request].update(state='network-failed',failed=self.stamp())
        if request in self.requests:
            entry = self.ledger[self.requests[request]]
            entry['state'] = 'network-failed'
            entry['failedCallback'] = self.stamp()
            self.update_timing(request)
            self.errors.append({'requestId':entry['requestId'], 'path': urlsplit(request.url).path, 'phase': 'network', 'error': 'requestfailed'})

    def save(self, state='observing'):
        configuration = {'port':9242,'target':self.target,'referenceAt':self.reference_at}
        data = {**self.recorder.snapshot(), 'schemaVersion':3, 'runId':self.run_id,
                'state': state, 'updatedAt': time.time(), 'codeHashes':self.hashes,
                'configuration':configuration,
                'configurationHash':hashlib.sha256(json.dumps(configuration,sort_keys=True).encode()).hexdigest(),
                'clock':{'startedAt':self.started_wall, 'referenceAt':self.reference_at,
                         'referenceKind':'user-supplied' if self.reference_at is not None else 'not-set',
                         'ntpVerified':False,'uncertaintyMs':None,
                         'elapsedMs':(time.monotonic()-self.started_mono)*1000},
                'requestLedger':list(self.ledger.values()), 'captureSummary':self.summary(),
                'timingMeaning':'브라우저 전송·응답과 Python 콜백/단조 경과를 분리. 서버 수신·좌석 확보 시각은 미확인.',
                'errors': self.errors, 'publicSources': self.assets, 'events': self.events,
                'scope': '9242 정상 UI 수동 관측. 수집기 자체의 클릭/요청 재전송/주문 없음',
                'coverage': {'allowlistedPaths': sorted(PATHS), 'unknownPaymentEndpointsCaptured': False,
                             'outsideAllowlist':list(self.outside.values()),
                             'unobservableTiming':['request-construction','request-ready','server-receipt','server-seat-hold'],
                             'outsideMeaning':'대한항공 /api/ 허용 목록 밖 요청은 본문·경로 비저장. 결제 제공자 요청은 별도 미수집.'}}
        target = self.folder / 'contracts.json'
        tmp = self.folder / 'contracts.tmp'
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
        # Windows에서 읽기 점검·백신 등이 짧게 파일을 열어도 수집기를 종료하지 않는다.
        for attempt in range(10):
            try:
                os.replace(tmp, target)
                break
            except PermissionError:
                if attempt == 9:
                    raise
                time.sleep(.05)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--seconds', type=int, default=300)
    ap.add_argument('--reference-at', help='시간대가 포함된 ISO 기준 시각. 서버 개방 확인/NTP 보정을 뜻하지 않음')
    ap.add_argument('--target-date', help='일반석 관측 대상 YYYY-MM-DD')
    ap.add_argument('--origin', choices=['ICN','CDG'])
    ap.add_argument('--destination', choices=['ICN','CDG'])
    a = ap.parse_args()
    if not 1 <= a.seconds <= 900:
        raise ValueError('수집은 최대15분')
    reference = None
    if a.reference_at:
        parsed = datetime.fromisoformat(a.reference_at)
        if parsed.tzinfo is None:
            raise ValueError('기준 시각에 시간대 필요')
        reference = parsed.timestamp()
    target = None
    if any((a.target_date,a.origin,a.destination)):
        if not all((a.target_date,a.origin,a.destination)) or a.origin==a.destination:
            raise ValueError('대상 노선·출발일 전체 필요')
        datetime.strptime(a.target_date,'%Y-%m-%d')
        target = {'origin':a.origin,'destination':a.destination,'date':a.target_date,'cabin':'일반석','currency':'KRW'}
    folder = ROOT / 'dev-shots' / 'api-capture' / (time.strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:8])
    folder.mkdir(parents=True, exist_ok=False)
    collector = Collector(folder, reference_at=reference, target=target)
    from playwright.sync_api import sync_playwright
    state = 'failed'
    collector.save('connecting')
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.connect_over_cdp('http://127.0.0.1:9242', timeout=15000)
            context = browser.contexts[0]
            context.on('request', collector.started)
            context.on('response', collector.responded)
            context.on('requestfinished', collector.finished)
            context.on('requestfailed', collector.failed)
            print(str(folder), flush=True)
            deadline = time.monotonic() + a.seconds
            while time.monotonic() < deadline and browser.is_connected() and context.pages:
                context.pages[0].wait_for_timeout(1000)
                collector.save()
            state = 'ended' if time.monotonic() >= deadline else 'interrupted'
    except BaseException as error:
        collector.errors.append({'phase':'collector','error':type(error).__name__})
        raise
    finally:
        collector.save(state)
    print('수집 종료; 브라우저/주문 상태 변경 없음', flush=True)


if __name__ == '__main__':
    main()
