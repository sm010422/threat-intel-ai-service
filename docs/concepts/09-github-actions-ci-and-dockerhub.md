# 개념 정리 — GitHub Actions CI/CD와 Docker Hub 연동

## 왜 `target-tracking-service`의 워크플로우를 그대로 복제했나

`.github/workflows/deploy.yml`은 `target-tracking-service`의 것을 거의 그대로 가져왔다 — 트리거 조건(`branches: [main]`, 관련 경로 변경 시), `runs-on: ubuntu-24.04-arm`(클러스터가 aarch64라 크로스 컴파일 없이 네이티브로 arm64 빌드), `docker/build-push-action@v5`로 `:{sha}`/`:latest` 두 태그 push하는 구조까지 동일하다. 차이는 트리거 경로뿐이다.

```yaml
paths:
  - 'app/**'
  - 'requirements.txt'
  - 'Dockerfile'
  - '.github/workflows/deploy.yml'
```

Java 쪽은 `src/**`, `build.gradle`이던 걸 Python 프로젝트 구조(`app/**`, `requirements.txt`)에 맞게만 바꿨다. 이미 검증된 파이프라인 설계를 재사용하는 게, 새로 설계하는 것보다 실패 지점을 줄인다는 판단.

## 겪은 문제 — Docker Hub 로그인이 두 번 실패했다

CI를 처음 push했을 때(`ce71091`) `DOCKERHUB_USERNAME`/`DOCKERHUB_TOKEN` 시크릿 자체가 없어서 아래처럼 실패했다:

```
##[error]Username and password required
```

이건 예상된 실패였다 — 시크릿은 리포 단위로 격리되어 있어서(target-tracking-service에 있어도 이 리포엔 없음), `gh secret set DOCKERHUB_USERNAME/DOCKERHUB_TOKEN --repo sm010422/threat-intel-ai-service`을 사용자가 직접 실행해야 했다.

시크릿을 등록한 뒤 재실행했는데 **다시 실패**했다:

```
##[error]Error response from daemon: Get "https://registry-1.docker.io/v2/": unauthorized: incorrect username or password
```

이번엔 시크릿은 있는데 값이 틀린 경우였다. 원인 후보를 두 가지로 좁혔다:

1. `DOCKERHUB_TOKEN`에 계정 로그인 비밀번호를 넣었을 가능성 — Docker Hub는 2FA가 켜져 있으면 비밀번호 로그인 자체가 막히고, 애초에 Access Token 사용을 권장한다.
2. `DOCKERHUB_USERNAME`에 이메일을 넣었을 가능성 — Docker Hub 로그인 ID(닉네임)여야 한다.

**해결**: Docker Hub → Account Settings → Personal access tokens에서 **Read & Write** 권한으로 새 토큰을 발급해서 다시 등록. `docker/build-push-action`은 이미지를 pull(Read)뿐 아니라 push(Write)까지 해야 하므로 Read-only 토큰으로는 애초에 안 된다 — Read & Write를 명시적으로 골라야 하는 이유.

재등록 후 `gh run rerun`/`gh workflow run deploy.yml`로 재실행하니 54초 만에 성공했고, Docker Hub API(`hub.docker.com/v2/repositories/.../tags`)로 실제 `latest`, `<sha>` 두 태그가 올라간 걸 직접 확인했다.

## 시크릿 값을 직접 다루지 않은 이유

이 과정에서 실제 Docker Hub 토큰 값은 한 번도 대화나 커맨드에 등장하지 않았다 — `gh secret set NAME --repo ...`는 인자 없이 실행하면 터미널에서 값을 프롬프트로 입력받고, 그 입력은 GitHub API로 바로 암호화되어 전송된다. 사용자에게 이 명령을 직접 실행하게 안내한 이유가 이거다: AI 에이전트가 크리덴셜 값을 보거나 다루지 않아도 되는 경로를 우선한 것.

## 이 CI가 실제로 어디에 영향을 주는지

이 리포(`threat-intel-ai-service`)에서 push → Docker Hub까지가 CI의 책임 범위다. Docker Hub 이후 `k3s-msa-infrastructure` 리포에 이미지 참조를 반영하고 실제 클러스터에 배포하는 부분은 이 리포의 관심사가 아니고, `k3s-msa-infrastructure`의 ArgoCD Image Updater가 담당한다 — 자세한 내용은 `k3s-msa-infrastructure/docs/Threat-Intel-AI-Service-K3s-Deployment.md` 참고.
