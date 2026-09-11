"""기존 실행의 네트워크 시간만 분석한다. 브라우저 접속·API 전송 없음."""
import argparse
import json
from pathlib import Path

CORE = ('awardAvailability', 'fareInformation', 'inputTravellers')
CONTEXT = ('calendarFareMatrix', 'validateMember', 'GetAvailablePaymentType', 'NaverPay')


def analyze(folder):
    folder=Path(folder)
    run=json.loads((folder/'manual_booking.json').read_text(encoding='utf-8'))
    network=json.loads((folder/'network.json').read_text(encoding='utf-8'))
    if run['runId'] != network.get('runId'):
        raise ValueError('예매 보고서와 네트워크 실행 ID가 다릅니다')
    fire=run['fire']['localAt']
    opening=run['fire']['targetAt']-run['clock']['offset']*1000
    rows=[];missing=[]
    for e in network['rows']:
        path=e.get('path','');timing=e.get('timing',{});sent=timing.get('startTime',0)
        if path.rsplit('/',1)[-1] not in CORE+CONTEXT:continue
        if sent<=0 or timing.get('responseEnd',-1)<0:
            missing.append({'path':path,'reason':'요청 또는 완료 시각 없음'});continue
        if sent<fire:continue
        rows.append({'path':path,'sentSinceOpen':round((sent-opening)/1000,3),
            'receivedSinceOpen':round((sent+timing['responseEnd']-opening)/1000,3),
            'networkMs':round(timing['responseEnd'],1),'httpStatus':e.get('status'),
            'legacyOrderIdObserved':e.get('orderCreated') if path.endswith('/inputTravellers') else None})
    rows.sort(key=lambda x:x['sentSinceOpen'])
    attempts={name:[r for r in rows if r['path'].endswith('/'+name)] for name in CORE}
    model=None;gaps=[]
    if all(len(attempts[name])==1 and attempts[name][0]['httpStatus']==200 for name in CORE):
        a,f,o=(attempts[name][0] for name in CORE)
        if a['receivedSinceOpen']<=f['sentSinceOpen'] and f['receivedSinceOpen']<=o['sentSinceOpen']:
            for before,after in [(a,f),(f,o)]:
                gaps.append({'from':before['path'],'to':after['path'],
                    'seconds':round(after['sentSinceOpen']-before['receivedSinceOpen'],3)})
            # 조건부 재배치 계산. 생략 가능한 요청/서버 의존관계는 아직 입증하지 못했다.
            durations=[attempts[name][0]['networkMs']/1000 for name in CORE]
            model={'assumptions':['T0에 조회 요청 시작', '세 호출이 직렬로 필요하다고 가정',
                    '관측한 네트워크 소요시간이 유지된다고 가정', '다른 작업과 요청의 대기시간을 모두 0으로 가정'],
                'orderRequestSentSeconds':round(sum(durations[:2]),3),
                'inputTravellersResponseSeconds':round(sum(durations),3),
                'measuredApiBooking':False,'seatHoldTimeVerified':False,
                'warning':'성능 예측이나 이론적 하한이 아니다. 의존관계와 응답시간이 달라지면 계산도 달라진다.'}
    for gap in gaps:
        gap['interpretation']='다른 API·화면 이동·사용자 절차가 섞인 구간. 전부 제거 가능한 UI 대기로 보지 않는다.'
    from_fire=run.get('secondsFromFire')
    return {'sourceRun':run['runId'],'rows':rows,'missingTiming':missing,'betweenCoreCalls':gaps,
            'conditionalZeroGapModel':model,'paymentWindowSecondsFromFire':from_fire,
            'paymentWindowSecondsFromOpen':round(from_fire+(fire-opening)/1000,3) if isinstance(from_fire,(int,float)) else None,
            'orderBusinessAcceptanceVerified':False,
            'orderInterpretation':'기존 orderCreated=false는 orderId 필드를 못 찾았다는 뜻이며 주문 실패 증거가 아니다.',
            'next':'업무 응답 구조·필수 토큰 의존관계·정상 결제창 연결 검증. UI 대기와 네트워크 시간을 분리할 것.'}


if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('run_folder');ap.add_argument('--output');a=ap.parse_args()
    result=json.dumps(analyze(a.run_folder),ensure_ascii=False,indent=2)
    if a.output:
        path=Path(a.output);path.parent.mkdir(parents=True,exist_ok=True);path.write_text(result,encoding='utf-8')
    print(result)
