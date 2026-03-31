# t3.micro 메모리 부족 위험 (스왑 미설정)

- **발생일**: 2026-03-31
- **심각도**: MEDIUM
- **카테고리**: 인프라

## 증상

AWS t3.micro (RAM 911MB)에서 봇 2개(BTC 155MB + Stock 88MB) 실행 시
가용 메모리 269MB, 스왑 0B. OOM Killer 발생 위험.

## 원인

- t3.micro 기본 설정에 스왑 없음
- BTC 봇(daily_live.py --realtime)이 웹소켓 + 123종목 감시로 메모리 사용량 변동
- 피크 시 243MB까지 사용 이력 확인

## 수정

512MB 스왑 파일 생성 완료 (2026-03-31):
```bash
sudo fallocate -l 512M /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' >> /etc/fstab
echo 'vm.swappiness=10' >> /etc/sysctl.conf
```

## 검증규칙

서버 헬스체크 시:
- `free -h`로 스왑 존재 확인 (512MB)
- 가용 메모리 100MB 이하 시 알림
- `deploy_to_aws.sh` 실행 후 스왑 설정 유지 확인

## 교훈

t3.micro에서 상시 봇 운영 시 스왑은 필수.
swappiness=10으로 설정하여 물리 메모리 우선 사용, 부족 시에만 스왑 사용.
새 서비스 추가 전 반드시 메모리 예산 확인.
