#!/bin/bash
# ML 모델 파일만 AWS에 배포 (코드 미포함, ~수MB).
# - 로컬에서 학습 끝낸 .pkl + .meta.json만 rsync
# - 원자적 전환: 임시 파일명 → ln -sfn current.pkl
# - 실패 시 rollback 가이드 출력
#
# 사용:
#   bash scripts/deploy_model_to_aws.sh                    # 가장 최신 signal_filter_*.pkl 배포
#   bash scripts/deploy_model_to_aws.sh signal_filter_X    # 특정 버전 배포
#
# 전제:
#   - scripts/deploy_to_aws.sh 와 동일한 PEM 키 / HOST / USER 사용
#   - AWS 측 디렉터리: $PROJECT_DIR/data/models/

set -euo pipefail

# ── 설정 (deploy_to_aws.sh와 동일) ─────────────────────
AWS_HOST="13.124.82.122"
AWS_USER="ubuntu"
PEM_KEY="${PEM_KEY:-$HOME/Downloads/upbit-trading-key-seoul.pem}"
PROJECT_DIR="/home/ubuntu/BitCoin_Trade"
LOCAL_DIR="$(cd "$(dirname "$0")/.." && pwd)"
LOCAL_MODEL_DIR="$LOCAL_DIR/data/models"
REMOTE_MODEL_DIR="$PROJECT_DIR/data/models"

if [ ! -f "$PEM_KEY" ]; then
    echo "ERROR: PEM 파일 없음: $PEM_KEY" >&2
    exit 1
fi

SSH_CMD="ssh -i $PEM_KEY -o StrictHostKeyChecking=no $AWS_USER@$AWS_HOST"

# ── 배포 대상 결정 ───────────────────────────────────
if [ $# -ge 1 ]; then
    VERSION="$1"
    PKL="$LOCAL_MODEL_DIR/${VERSION}.pkl"
    META="$LOCAL_MODEL_DIR/${VERSION}.meta.json"
else
    # 가장 최신 signal_filter_*.pkl
    PKL=$(ls -t "$LOCAL_MODEL_DIR"/signal_filter_*.pkl 2>/dev/null | head -1)
    if [ -z "$PKL" ]; then
        echo "ERROR: 배포할 모델 없음: $LOCAL_MODEL_DIR/signal_filter_*.pkl" >&2
        exit 2
    fi
    META="${PKL%.pkl}.meta.json"
    VERSION=$(basename "$PKL" .pkl)
fi

if [ ! -f "$PKL" ] || [ ! -f "$META" ]; then
    echo "ERROR: 모델/메타 파일 누락" >&2
    echo "  PKL:  $PKL"
    echo "  META: $META"
    exit 3
fi

# ── 로컬 체크: 메타 검증 (Windows/Git Bash 경로 호환) ─
META_FOR_PY="$META"
if command -v cygpath >/dev/null 2>&1; then
    META_FOR_PY=$(cygpath -w "$META")
fi
python - <<EOF
import json, sys
meta = json.loads(open(r"$META_FOR_PY", encoding="utf-8").read())
required = ["version", "feature_columns", "n_samples", "threshold", "cv_metrics"]
missing = [k for k in required if k not in meta]
if missing:
    print(f"ERROR: 메타 누락 키: {missing}", file=sys.stderr); sys.exit(4)
auc = meta.get("cv_metrics", {}).get("mean_auc", 0)
print(f"  메타 OK: n_samples={meta['n_samples']} auc={auc:.3f} threshold={meta['threshold']}")
EOF

echo "=== 모델 배포 시작 ==="
echo "  버전: $VERSION"
echo "  PKL:  $(basename "$PKL")"
echo "  META: $(basename "$META")"

# ── 원격 디렉터리 보장 ────────────────────────────────
$SSH_CMD "mkdir -p $REMOTE_MODEL_DIR"

# ── rsync (모델 파일만) ───────────────────────────────
RSYNC_CMD="rsync -avz -e 'ssh -i $PEM_KEY -o StrictHostKeyChecking=no'"
if ! command -v rsync &> /dev/null; then
    # rsync 없으면 scp 폴백 (lessons #16)
    echo "  rsync 미설치 → scp 폴백"
    scp -i "$PEM_KEY" -o StrictHostKeyChecking=no "$PKL"  "$AWS_USER@$AWS_HOST:$REMOTE_MODEL_DIR/"
    scp -i "$PEM_KEY" -o StrictHostKeyChecking=no "$META" "$AWS_USER@$AWS_HOST:$REMOTE_MODEL_DIR/"
else
    eval $RSYNC_CMD "$PKL"  "$AWS_USER@$AWS_HOST:$REMOTE_MODEL_DIR/"
    eval $RSYNC_CMD "$META" "$AWS_USER@$AWS_HOST:$REMOTE_MODEL_DIR/"
fi

# ── 원자적 current 전환 (심볼릭 링크) ─────────────────
$SSH_CMD "cd $REMOTE_MODEL_DIR && ln -sfn $(basename "$PKL")  current.pkl && \
                                  ln -sfn $(basename "$META") current.meta.json"

# ── 검증 ──────────────────────────────────────────────
echo ""
echo "=== 원격 검증 ==="
$SSH_CMD "ls -la $REMOTE_MODEL_DIR/current.* && \
          python3 -c 'import json; m=json.load(open(\"$REMOTE_MODEL_DIR/current.meta.json\")); \
                       print(\"  version:\", m[\"version\"], \"auc:\", round(m[\"cv_metrics\"][\"mean_auc\"],3))'"

echo ""
echo "=== 완료 ==="
echo "ML_FILTER_ENABLED=1 환경변수가 설정되어야 실제 게이트가 활성화됩니다."
echo "롤백: ssh ... 'cd $REMOTE_MODEL_DIR && ln -sfn signal_filter_<이전버전>.pkl current.pkl'"
