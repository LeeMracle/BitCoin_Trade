@echo off
REM 업비트 일봉 마감: 매일 09:00 KST
REM 여유를 두고 09:05에 실행

set PYTHON_PATH=D:\20.Personal\Study\BitCoin_Trade\.venv\Scripts\python.exe
set SCRIPT_PATH=D:\20.Personal\Study\BitCoin_Trade\scripts\daily_check.py
set WORK_DIR=D:\20.Personal\Study\BitCoin_Trade

schtasks /create /tn "BTC_PaperTrading" /tr "\"%PYTHON_PATH%\" \"%SCRIPT_PATH%\"" /sc daily /st 09:05 /rl HIGHEST /f

echo.
echo 작업 스케줄러 등록 완료!
echo 작업명: BTC_PaperTrading
echo 실행시간: 매일 09:05
echo.
echo 확인: schtasks /query /tn "BTC_PaperTrading"
echo 삭제: schtasks /delete /tn "BTC_PaperTrading" /f
pause
