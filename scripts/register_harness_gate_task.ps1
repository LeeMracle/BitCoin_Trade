# Harness Step 6 Gate — Windows Task Scheduler 주간 등록
# 실행 방법: 관리자 PowerShell에서 1회 실행
#   powershell -ExecutionPolicy Bypass -File scripts\register_harness_gate_task.ps1
#
# 등록 후 매주 목요일 09:05 KST에 harness_step6_gate.py --auto 가 실행되고
# 결과가 텔레그램으로 자동 발송됩니다.

$TaskName = 'HarnessStep6Gate'
$BatPath  = 'D:\20.Personal\Study\BitCoin_Trade\scripts\harness_gate_weekly.bat'

if (-not (Test-Path $BatPath)) {
    Write-Error "배치 파일이 없습니다: $BatPath"
    exit 1
}

$Action    = New-ScheduledTaskAction -Execute $BatPath
$Trigger   = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Thursday -At 9:05am
$Principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited
$Settings  = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $Action `
    -Trigger $Trigger `
    -Principal $Principal `
    -Settings $Settings `
    -Description 'Weekly harness Step6 gate auto check (auto-telegram report)' `
    -Force

Write-Host ""
Write-Host "[OK] 스케줄 등록 완료: $TaskName" -ForegroundColor Green
Write-Host "     매주 목요일 09:05 KST 실행"
Write-Host ""
Write-Host "확인: schtasks /Query /TN $TaskName /FO LIST"
Write-Host "즉시실행: schtasks /Run /TN $TaskName"
Write-Host "삭제: schtasks /Delete /TN $TaskName /F"
