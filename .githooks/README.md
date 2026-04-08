# .githooks — BATA git hooks

로컬 개발 환경에 git hook을 활성화하려면 다음 명령을 **한 번** 실행:

```bash
git config core.hooksPath .githooks
```

## 포함된 hook

### `pre-commit`
커밋 직전에 실행. 현재 검사 항목:
- `scripts/lint_none_format.py --quiet` — NoneType 포매팅 안전성

ERROR 발생 시 커밋 중단. 수정 후 다시 `git commit`.

## 우회 (권장하지 않음)

```bash
git commit --no-verify
```

급한 hotfix 상황이 아니면 사용 금지. 우회 후에도 `pre_deploy_check.py` 가
배포 전에 다시 검증하므로 결국 막힌다.

## 배경

- [docs/lint_layer.md](../docs/lint_layer.md) — 린트 층 설계 문서
- [docs/lessons/20260408_4_nonetype_format_lint.md](../docs/lessons/20260408_4_nonetype_format_lint.md) — 도입 배경
