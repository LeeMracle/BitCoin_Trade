@echo off
cd /d D:\20.Personal\Study\BitCoin_Trade
set PYTHONUTF8=1
python scripts\harness_step6_gate.py --auto >> workspace\gate_reports\_cron.log 2>&1
