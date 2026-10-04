@echo off
rem 매일 09:00 측정. 스케줄러가 08:50 에 실행한다.
rem
rem 이 파일은 '그날 무엇을 잴지' 에 따라 손으로 조정한다. 날마다 목적이 다르기 때문이다.
rem   --route/--from : 노선과 방향   --no-macro : 계측만   (인자 없으면 요일로 자동 판단)
rem
rem 2026-09-04 (금, 열리는 날 2027-08-30 월요일 = 로마 운항일):
rem   인천->로마(SEL->FCO)로 계측기 + dry 매크로를 함께 돌린다.
rem   계측기 = 프레스티지가 몇 석이고 몇 초에 0 이 되나
rem   dry 매크로 = 우리가 몇 초에 잠글 수 있었나 (7단계 앞 정지라 주문 안 생김)
rem   두 숫자를 겹쳐야 '몇 초 부족한가' 가 나온다.
cd /d "%~dp0.."
".venv\Scripts\python.exe" "macro\daily.py" --route FCO > "dev-shots\daily_console.log" 2>&1
