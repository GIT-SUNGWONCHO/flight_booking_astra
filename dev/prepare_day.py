"""08:20 준비 담당. 일반석 실제 결제창 시험 후 복원. 예매 정시 발사 권한 없음.

예매와 계측 준비를 개별 결과로 기록한다. 계측 실패는 예매에 영향을 주지 않는다.
"""
import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from runtime import new_run,output_dir,atomic_json,heartbeat
from test_calendar import rehearsal_plan,plan,KST
from rehearse import find_shell
from prepare_currency import prepare_krw
from worker_health import wait_worker
from stage_process import StageProcess

ROOT=Path(__file__).resolve().parent.parent
PY=str(ROOT/('.venv/Scripts/python.exe' if sys.platform=='win32' else '.venv/bin/python'))


def run_stage(report, out, deadline, name, args, timeout=240):
    """시작부터 남겨 강제 종료·시간 초과도 실패 단계로 추적한다."""
    started=time.monotonic()
    path=out/(name+'.log')
    item={'name':name,'status':'running','startedAt':datetime.now(KST).isoformat(),'log':str(path)}
    report['activeStage']=name
    report['stages'].append(item)
    atomic_json(out/'prepare_day.json',report)
    try:
        left=deadline-time.monotonic()
        if left<=0:raise TimeoutError('준비 마감')
        item['timeoutSeconds']=min(timeout,left)
        print(name,flush=True)
        heartbeat('prepare-day',name,day=report['plan']['runDate'])
        with path.open('w',encoding='utf-8') as f, StageProcess(args,cwd=ROOT,stdout=f,stderr=subprocess.STDOUT,
                env={**os.environ,'PYTHONIOENCODING':'utf-8','PYTHONUNBUFFERED':'1'},
                creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0)) as owned:
            result=owned.process
            item['pid']=result.pid
            atomic_json(out/'prepare_day.json',report)
            try:
                result.wait(timeout=max(.001, min(deadline, started+timeout)-time.monotonic()))
            except subprocess.TimeoutExpired:
                cleanup_started=time.monotonic()
                owned.stop()
                item['timeoutCleanup']={'scope':'이 단계 소유 프로세스만',
                    'method':'Windows Job Object' if os.name=='nt' else 'process.kill',
                    'elapsedSeconds':round(time.monotonic()-cleanup_started,3),'exitCode':result.returncode}
                raise
        item['exitCode']=result.returncode
        if result.returncode:raise RuntimeError(name+' 실패')
        item['status']='passed'
        return path.read_text(encoding='utf-8')
    except Exception as e:
        item.update(status='failed',error=type(e).__name__)
        raise
    finally:
        item.update(endedAt=datetime.now(KST).isoformat(),elapsedSeconds=round(time.monotonic()-started,3))
        atomic_json(out/'prepare_day.json',report)


def main(a):
    if not plan(a.day)['enabled']:
        print(plan(a.day)['skipReason']+' — 예매·계측 모두 준비하지 않습니다.',flush=True);return 0
    cfg=rehearsal_plan(a.day)
    if not a.preview and datetime.now(KST).date().isoformat()!=a.day:
        raise ValueError('당일 준비만 허용합니다. 사전 시험은 --preview 사용')
    if not a.preview and datetime.now(KST)>=datetime.fromisoformat(cfg['openAt'])-timedelta(minutes=10):
        raise ValueError('08:50 준비 마감 이후에는 리허설을 시작하지 않습니다')
    identity=new_run();out=output_dir();report={'runId':identity,'plan':cfg,'preview':a.preview,'booking':{'ready':False},'observer':{'ready':False},'stages':[]}
    def save():atomic_json(out/'prepare_day.json',report)
    deadline=time.monotonic()+1800 if a.preview else time.monotonic()+max(0,(datetime.fromisoformat(cfg['openAt'])-timedelta(minutes=10)-datetime.now(KST)).total_seconds())
    def stage(name,args,timeout=240):
        return run_stage(report,out,deadline,name,args,timeout)
    def setup_args(port,departure=False):
        return [PY,str(ROOT/'dev/setup.py'),cfg['destination'],'--from',cfg['origin'],
                '--port',str(port),'--date',cfg['rehearsalDepartureDate']]+(['--departure'] if departure else [])
    def boot(port):
        if sys.platform!='win32':
            # macOS/Linux 에는 파워셸 런처가 없다. 같은 포트·프로필 규칙의 셸 판을 쓴다.
            return [str(ROOT/'dev/astra_browsers.sh'),'-Port',str(port)]+(['-Restart'] if a.cold else [])
        return [find_shell(),'-NoProfile','-ExecutionPolicy','Bypass','-File',str(ROOT/'dev/astra_browsers.ps1'),
                '-Port',str(port)]+(['-Restart'] if a.cold else [])
    def child(name,args):
        log=(out/(name+'.log')).open('w',encoding='utf-8')
        proc=subprocess.Popen(args,cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,
            env={**os.environ,'PYTHONIOENCODING':'utf-8','PYTHONUNBUFFERED':'1'},
            creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        log.close();return {'pid':proc.pid,'log':str(out/(name+'.log'))},proc
    def ready_worker(meta,proc,role,marker,seconds):
        return wait_worker(meta,proc,role,marker,cfg,ROOT,seconds,deadline=deadline)
    save()
    from playwright.sync_api import sync_playwright
    try:
        stage('예약_크롬',boot(9232),100)
        stage('예약_통화조회',setup_args(9232,True),240)
        report['activeStage']='예약_KRW검사';save()
        with sync_playwright() as pw:
            b=pw.chromium.connect_over_cdp('http://127.0.0.1:9232')
            page=next(p for p in b.contexts[0].pages if '/select-award-flight/departure' in p.url)
            before=page.locator('#currencyBtn').inner_text(timeout=20000)
            report['booking']['currency']={'before':before,**prepare_krw(page,cfg['rehearsalDepartureDate'][5:])};save()
        stage('예약_리허설달력',setup_args(9232),240)
        stage('예약_결제창리허설',[PY,str(ROOT/'dev/manual_booking.py'),'--day',a.day,'--rehearsal',
            '--target',cfg['rehearsalDepartureDate'],'--at','+40s'],300)
        # 결제 승인 없이 시험 창만 닫고 새 흐름으로 복원한다. 서버 좌석 해제는 별도 사실이다.
        report['activeStage']='예약_시험결제창정리';save()
        with sync_playwright() as pw:
            b=pw.chromium.connect_over_cdp('http://127.0.0.1:9232')
            from payment_window import inspect_payment_window, payment_provider
            provider=payment_provider(cfg['origin'],cfg['destination'])
            closed=0
            for page in list(b.contexts[0].pages):
                if inspect_payment_window(page,provider).get('ready'):page.close();closed+=1
            report['booking']['rehearsalCleanup']={'closedPaymentWindows':closed,'paymentProvider':provider,'serverSeatReleaseVerified':False,
                'reason':'결제창 닫기만 수행. 서버 주문 취소·좌석 해제 응답은 관측하지 않음'};save()
        stage('예약_목표달력복원',setup_args(9232),240)
        if a.preview:
            stage('예약_미리보기',[PY,str(ROOT/'dev/manual_booking.py'),'--day',a.day,'--configure-only'],90)
            report['booking'].update(ready=True,scope='사전 리허설 완료·목표 미리보기. 당일 준비 필요')
        else:
            report['activeStage']='예약_대기프로그램준비';save()
            report['booking']['service'],booking_proc=child('예약_대기기록',[PY,str(ROOT/'dev/manual_booking.py'),'--day',a.day])
            report['booking'].update(ready=False,scope='주입 유지 프로그램 준비 확인 중')
            # PID 존재만으로 준비 완료라고 하지 않는다. 해당 자식 로그의 준비 메시지를 기다린다.
            if ready_worker(report['booking']['service'],booking_proc,'manual-booking','준비:',60):
                report['booking'].update(ready=True,scope='사용자가 대기 시작할 수 있음')
            if not report['booking']['ready']:raise RuntimeError('예매 대기 준비 확인 실패')
    except Exception as e:
        report['booking'].update(ready=False,error=type(e).__name__,failedStage=report.get('activeStage'));save()
    # 별도 실행: 예약 준비 실패/성공 여부에 종속시키지 않는다.
    try:
        stage('계측_크롬',boot(9233),100)
        stage('계측_달력',setup_args(9233),240)
        command=[PY,str(ROOT/'dev/calendar_observer.py'),'--day',a.day]
        if a.preview:
            stage('계측_리허설',command+['--rehearsal','--target',cfg['rehearsalDepartureDate'],'--at','+25s','--seconds','15'],90)
            report['observer'].update(ready=True,scope='사전 리허설; 신규 개방·소멸 미검증')
        else:
            report['activeStage']='계측_대기프로그램준비';save()
            report['observer']['service'],observer_proc=child('계측_대기',command)
            report['observer'].update(ready=False,scope='계측 준비 확인 중')
            if ready_worker(report['observer']['service'],observer_proc,'calendar-observer','계측 준비:',45):
                report['observer'].update(ready=True,scope='독립 계측 대기')
            if not report['observer']['ready']:raise RuntimeError('계측 대기 준비 확인 실패')
    except Exception as e:report['observer'].update(ready=False,error=type(e).__name__,failedStage=report.get('activeStage'))
    report['endedAt']=datetime.now(KST).isoformat();save()
    print(json.dumps({'booking':report['booking'],'observer':report['observer'],'report':str(out/'prepare_day.json')},ensure_ascii=False),flush=True)
    try:
        import winsound
        winsound.MessageBeep()
    except Exception:pass
    return 0 if report['booking']['ready'] else 2


if __name__=='__main__':
    ap=argparse.ArgumentParser()
    ap.add_argument('--day',required=True)
    ap.add_argument('--preview',action='store_true',help='전날 사전 리허설. 실전 대기는 걸지 않음')
    ap.add_argument('--cold',action='store_true',help='이 작업 폴더의 전용 크롬만 재시작')
    raise SystemExit(main(ap.parse_args()))
