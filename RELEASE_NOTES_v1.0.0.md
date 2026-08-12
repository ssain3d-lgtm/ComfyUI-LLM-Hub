ComfyUI에서 LLM 백엔드를 드롭다운으로 골라 텍스트를 생성하는 커스텀 노드팩입니다.
로컬 모델(LM Studio)과 이미 쓰고 있는 구독 CLI(Claude Code / Codex / Gemini)를 같은 노드 하나로 바꿔가며 쓸 수 있습니다.

## 주요 기능

**4개 백엔드를 노드 하나로**
`lmstudio`(HTTP) / `claude` / `codex` / `gemini`(subprocess). 드롭다운으로 즉시 전환됩니다.

**지정 폴더의 파일 읽기**
`file_access`를 켜고 폴더를 지정하면 그 안의 파일을 LLM이 읽고 답합니다.
CLI 3종은 각자의 내장 읽기 도구를, LM Studio는 노드가 제공하는 `list_dir`/`read_file` 도구 루프를 씁니다.

**이미지 · 비디오 입력**
이미지는 4개 백엔드 전부 지원합니다. 비디오는 백엔드별 실제 지원 여부를 조사해 나눴습니다.

| 백엔드 | 네이티브 비디오 | 처리 |
|---|---|---|
| gemini | O (유일) | 파일을 그대로 전달 |
| claude / codex / lmstudio | X | 균등 간격 프레임을 뽑아 이미지로 전달 |

프레임 추출은 `ffmpeg`(외부 실행 파일) 우선, 없으면 기존 `cv2`를 씁니다.

**노드 안 실시간 모니터링 창**
생성 중인 텍스트가 노드 안에 실시간으로 표시됩니다. `stream_view`로 `plain`(기본) / `markdown` / `off`를 고르며, 생성 중에 바꿔도 즉시 다시 그려집니다.
기본이 `plain`인 이유는 이미지 프롬프트 생성 시 CLIP으로 넘어갈 **문자 그대로**를 봐야 하기 때문입니다. 마크다운 렌더러는 외부 CDN 없이 직접 구현해 오프라인에서도 동작합니다.

**LM Studio 모델 선택 + VRAM 자동 해제**
ComfyUI는 이미지 모델이 VRAM을 써야 하므로 LM Studio가 모델을 물고 있으면 문제가 됩니다.

- `lmstudio_model` 드롭다운으로 모델 선택
- `lmstudio_unload_after`(기본 켜짐) — 응답 직후 `lms unload`로 **즉시** 해제
- `lmstudio_ttl_sec`(기본 300초) — 유휴 시 자동 해제. `lms` CLI가 없을 때의 안전망

**워크플로우가 죽지 않습니다**
항상 `text` / `status` / `debug` 3개를 반환합니다. 로그인 안 됨, 서버 꺼짐, 폴더 없음 등 모든 실패가 한국어 `status`로 나오고 예외는 밖으로 던지지 않습니다.

## 보안

- 워크스페이스 경로를 `realpath` 기준으로 검증 — `../` 탈출과 폴더 밖 심볼릭 링크 차단
- 읽기 전용 지향: claude `Read,Glob,Grep`(Write/Edit/Bash 미포함), codex `-s read-only`, gemini `--approval-mode plan`
- `extra_args`의 샌드박스 해제 플래그(`--dangerously-*`, `--yolo`, `-s danger-full-access` 등) 차단
- `subprocess`에 `shell=True` 미사용 — AST 검사 테스트로 고정
- 모니터링 창 링크에 스킴 화이트리스트 적용 (`javascript:` 차단)
- `config.json`은 `.gitignore`, `api_token` 등 비밀값은 debug 출력에 미포함

> **주의**: 워크스페이스 파일 내용은 신뢰할 수 없는 입력입니다. 파일 안에 지시문이 섞여 있으면 LLM이 따라갈 수 있으므로(프롬프트 인젝션) 필요한 폴더만 좁게 지정하세요.

## 개발 중 실측으로 잡은 것들

CLI 플래그는 문서를 믿지 않고 전부 `--help`로 실측했고, 그 과정에서 여러 문제를 발견했습니다.

- **gemini `--approval-mode plan`이 조용히 강등됩니다.** 신뢰되지 않은 폴더에서는 읽기 전용이 해제되고 경고만 출력됩니다. `--skip-trust`를 함께 줘야 유지됩니다
- **claude는 정상 호출에도 `rate_limit_event`를 흘립니다.** 여기 포함된 `rate_limit` 문자열 때문에 모든 스트리밍 호출이 `rate_limited`로 오분류됐습니다
- **모델 답변이 통째로 버려지던 버그.** "429가 뭐야?" 같은 질문의 정답에 `429`가 있다는 이유로 오류 처리됐습니다. 이제 진단은 stderr에만 겁니다
- **LM Studio SSE에 charset이 없으면** requests가 ISO-8859-1로 디코딩해 **한글이 깨집니다**
- **claude에 `--max-turns`가 없습니다.** 툴 전체 차단은 `--tools ""`가 공식 수단입니다
- 사용자 원본 파일을 덮어쓰던 문제, Windows `.cmd` 셔틀의 프로세스 누수, ffmpeg 프레임 번호 어긋남 등

## 검증

- **자동 테스트 138종 통과.** LM Studio 없이 검증하려고 표준 라이브러리로 가짜 서버(SSE 포함)를 만들었고, CLI는 `run_cli`를 가로채 커맨드 조립·파싱을 확인합니다
- **claude 실계정 스모크 통과** — 텍스트 생성 / 시스템 프롬프트 / 파일 읽기 / 이미지 / 비디오
- 이미지 테스트 중 모델의 Bash 시도가 `permission_denials`로 **실제 차단**되는 것을 확인했습니다
- 스트리밍은 델타 7개가 3.2초에 걸쳐 도착하고 누적본과 최종 결과가 일치하는 것을 확인했습니다

## 설치

```
cd ComfyUI/custom_nodes
git clone https://github.com/ssain3d-lgtm/ComfyUI-LLM-Hub.git
```

이어서 `install.bat` 실행(ComfyUI 내장 파이썬을 찾아 `requests` 설치 + `config.json` 생성) 후
ComfyUI를 재시작하면 **LLM Hub Generate** 노드가 `LLM Hub` 카테고리에 나타납니다.

pip 의존성은 **`requests` 하나**입니다. 자세한 설정과 트러블슈팅은 [README](README.md)를 참고하세요.

## 알려진 제한 / 확인 필요

- **모니터링 창 JS는 실제 ComfyUI에서 렌더링을 검증하지 못했습니다.** 문법 검사와 API 사용 검토만 거쳤습니다. 안 보이면 브라우저 새로고침(JS 확장 로드)부터 확인하세요
- **lmstudio / codex / gemini 실계정 스모크는 미검증입니다** (로그인 필요). README §8의 명령으로 확인해 주세요
- codex `--json` 이벤트 스키마를 실측하지 못해 관대한 파서로 처리했습니다. 파싱이 안 돼도 최종 본문은 `-o` 파일에서 읽으므로 결과 자체는 정상입니다
- gemini `plan` 모드가 응답을 "계획서" 형식으로 바꾸는지는 실계정에서만 확인 가능합니다. 그럴 경우 `config.json`의 `gemini_approval_mode`를 `default`로 바꾸세요
- 워드(.docx) · 한글(.hwp) · 엑셀은 읽지 못합니다. PDF는 claude/gemini만 가능합니다
- 비디오 프레임 변환 경로는 정지 화면 몇 장만 보므로 빠른 움직임과 오디오는 반영되지 않습니다

## v1에서 하지 않은 것

멀티턴 세션 유지(`--resume`), 파일 쓰기/편집 도구, 웹검색 도구, 자동 설치기.

MCP는 claude만 `--mcp-config` 패스스루가 동작합니다. codex는 비대화형 승인 이슈, gemini는 전역 설정 사이드이펙트 때문에 v1 미적용이며, 지정해도 노드가 죽지 않고 `debug`에 사유를 남깁니다.
