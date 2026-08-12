# ComfyUI-LLM-Hub 설계 문서 (v1.0)

- 작성일: 2026-08-12
- 대상: Claude Opus (Claude Code에서 구현 담당)
- 목적: ComfyUI에서 LLM 백엔드를 드롭다운으로 선택(LM Studio 로컬 / ChatGPT 구독 Codex CLI / Claude 구독 Claude Code / Gemini 구독 Gemini CLI)해 텍스트를 생성하고, 지정 폴더의 파일을 LLM이 읽을 수 있게 하는 커스텀 노드팩
- 사용 형태: 1인 개인용, 본인 구독 계정, Windows 환경

<!-- 수정: 구현 완료. 이 문서의 커맨드 템플릿 중 실측과 달랐던 부분은 각 항목에
     "수정" 주석으로 표시했다. 실측일 2026-08-12, 대상 버전:
       claude  (Claude Code CLI, --help 실측)
       codex   (@openai/codex, codex exec --help 실측)
       gemini  (@google/gemini-cli, gemini --help 실측)
     추가 구현: 비디오 입력(§16) — 설계 이후 요청으로 반영. -->

---

## 0. 구현자(Opus) 필독 지시사항

1. **CLI 플래그는 실측 우선.** 이 문서의 커맨드 템플릿은 설계 시점 기준이다. 코딩 전 반드시 `claude --help`, `codex exec --help`, `gemini --help`를 실행해 플래그를 실측 확인하고, 문서와 다르면 **실측을 따르되** 본 문서 해당 부분에 `<!-- 수정: ... -->` 주석을 남겨라.
2. 마일스톤(§12) 순서대로 구현한다. 각 마일스톤 완료 기준을 통과한 뒤 다음으로 넘어가고, 마일스톤 단위로 git commit 한다.
3. 모든 소스 파일은 UTF-8. **`.bat` 파일은 ASCII 전용**(한글/유니코드 금지 — Windows cmd 인코딩 오류 방지).
4. `subprocess`에 `shell=True` 절대 금지. 인자는 리스트로 전달.
5. LM Studio API-MCP(`/api/v1/chat`의 `integrations`)를 구현할 경우 스키마를 추측하지 말고 https://lmstudio.ai/docs/developer/core/mcp 를 확인 후 구현하라. 단 v1 기본 경로는 §8.1의 "내장 툴 루프"다(스키마 불확실성 제거).
6. 외부 pip 의존성은 `requests` 하나로 유지한다. 추가가 꼭 필요하면 사유를 README에 기록.

<!-- 수정(0-3): install.bat 은 ASCII 전용으로 작성했고, tests/test_cli_backends.py 의
     TestNoShellTrue 가 AST 로 shell=True 사용을 자동 검사한다(0-4 자동 보증).
     pip 의존성은 requests 하나를 유지했다(0-6). 비디오 프레임 추출은 pip 패키지가 아니라
     외부 실행 파일 ffmpeg(없으면 이미 설치된 cv2)을 쓰므로 의존성이 늘지 않는다. -->

---

## 1. 요구사항

### 기능 요구사항

- F1. `backend` 드롭다운: `lmstudio` / `claude` / `codex` / `gemini`
- F2. `system_prompt`, `prompt`(유저 프롬프트) 입력 → 생성 텍스트 `STRING` 출력
- F3. 이미지 입력(옵션, ComfyUI `IMAGE`) → 멀티모달 경로로 전달
- F4. `workspace_dir` 지정 시 해당 폴더 내 파일을 LLM이 읽을 수 있음(백엔드별 방식은 §8)
- F5. `seed` 입력으로 재실행 제어(ComfyUI 캐시 무효화 용도)
- F6. `status`, `debug` 출력으로 에러/원시 응답 확인 가능
- F7. `mcp_config` 입력 슬롯(옵션): 지원 백엔드에 네이티브 MCP 설정 전달(§8 참조, v1은 패스스루 수준)

<!-- 수정(F3): 설계 이후 요청으로 비디오 입력(F8)을 추가했다. §16 참조. -->

### 비기능 요구사항

- N1. Windows 11 / Python 3.12 / ComfyUI 0.31.x 기준 (다른 OS는 덤)
- N2. CLI 미설치·미로그인 시 **원인을 알 수 있는 한국어 에러 메시지**를 status로 반환
- N3. 타임아웃 기본 300초, 노드 입력으로 조절
- N4. 노드가 예외로 워크플로우 전체를 죽이지 않음 — 항상 3개 출력을 반환

### 비목표 (v1에서 하지 않음)

- 스트리밍 출력, 멀티턴 세션 유지(`--resume` 등), 파일 쓰기/편집 툴, 웹검색 툴, 백엔드별 고급 파라미터 전체 노출, 자동 설치기

---

## 2. 사용자 환경 전제 (README에 명시할 것)

- LM Studio 서버 실행 중(`http://127.0.0.1:1234`), tool use 지원 모델 로드(Qwen 계열 권장). 이미지 입력을 쓰려면 VLM 모델.
- Claude Code 설치 + Pro/Max 구독 로그인 완료 (`claude` 단독 실행으로 확인)
- Codex CLI 설치 + ChatGPT 로그인 완료 (`codex login`)
- Gemini CLI 설치 + 구글 계정 로그인 완료
- 각 CLI가 PATH에 있거나, `config.json`의 `cli_paths`에 절대경로 지정
- (선택) 네이티브 MCP 서버를 쓸 경우 Node.js 설치(`npx` 실행 가능)

<!-- 수정: 비디오 입력을 claude/codex/lmstudio 에서 쓰려면 ffmpeg 이 PATH 에 있어야 한다.
     (gemini 는 네이티브 지원이라 불필요) README §5 에 기재했다. -->

---

## 3. 아키텍처

```
[ComfyUI Node: LLM Hub Generate]
        │  LLMRequest (dataclass)
        ▼
   [backend factory]
        ├── LMStudioBackend ──── HTTP → 127.0.0.1:1234 /v1/chat/completions
        │                         └ file_access 시: 노드 자체 제공 툴(list_dir/read_file) 루프
        ├── ClaudeCodeBackend ── subprocess → claude -p (내장 Read/Glob/Grep)
        ├── CodexBackend ─────── subprocess → codex exec (read-only 샌드박스)
        └── GeminiBackend ────── subprocess → gemini (내장 read_file/glob)
        │  LLMResponse (dataclass)
        ▼
[출력: text(STRING) → CLIP Text Encode 등 / status / debug]
```

설계 원칙:

- **어댑터 패턴.** 백엔드마다 파일 하나. 공통 인터페이스(§6)만 지키면 백엔드 추가/삭제가 자유롭다.
- **파일 접근 v1 전략 = "확실한 경로 먼저".** CLI 3종은 자체 파일 읽기 툴이 있으므로 `cwd=workspace_dir`로 실행하면 끝. LM Studio는 노드가 파이썬으로 직접 `list_dir`/`read_file` 툴을 제공하는 툴 루프(§8.1)를 기본으로 한다. 네이티브 MCP는 확장 슬롯(`mcp_config`)으로 열어둔다.

---

## 4. 저장소 구조

```
ComfyUI-LLM-Hub/
├─ __init__.py              # NODE_CLASS_MAPPINGS / NODE_DISPLAY_NAME_MAPPINGS
├─ nodes.py                 # ComfyUI 노드 클래스 (LLMHubGenerate)
├─ backends/
│  ├─ __init__.py           # get_backend(name) 팩토리
│  ├─ base.py               # LLMRequest / LLMResponse / BaseBackend
│  ├─ lmstudio.py
│  ├─ claude_code.py
│  ├─ codex.py
│  └─ gemini.py
├─ utils/
│  ├─ proc.py               # 서브프로세스 공통 러너 (§9)
│  ├─ image_io.py           # IMAGE 텐서 → PNG 임시 저장
│  ├─ video_io.py           # 비디오 경로 해석 + 프레임 추출  <!-- 수정: 추가 (§16) -->
│  ├─ config.py             # config.json 로더        <!-- 수정: 추가 -->
│  └─ fs_tools.py           # LM Studio용 list_dir/read_file 구현 + 경로 검증
├─ config.example.json      # §10, 실제 config.json은 .gitignore
├─ requirements.txt         # requests
├─ install.bat              # ASCII 전용. ComfyUI 내장 python으로 pip install
├─ tests/
│  ├─ test_backends.py      # ComfyUI 없이 단독 실행하는 스모크 테스트 (§13)
│  ├─ test_offline.py       # 로그인 없이 도는 오프라인 검증  <!-- 수정: 추가 -->
│  ├─ test_cli_backends.py  # CLI 커맨드 조립/파싱 검증       <!-- 수정: 추가 -->
│  ├─ test_video.py         # 비디오 입력 검증                <!-- 수정: 추가 -->
│  ├─ mock_lmstudio.py      # 가짜 LM Studio 서버(표준 라이브러리) <!-- 수정: 추가 -->
│  └─ fixtures/             # test.txt, sample.png, sample_video.mp4
└─ README.md                # 설치, 로그인 확인법, 트러블슈팅, 보안 주의
```

<!-- 수정: utils/config.py 는 설정 로더로 추가했다(표준 라이브러리만 사용, pip 의존성 없음).
     테스트는 4개 파일로 나눴다. LM Studio 와 CLI 로그인이 없는 환경에서도
     로직을 검증할 수 있도록 가짜 LM Studio 서버와 run_cli 가로채기를 쓴다. -->

---

## 5. ComfyUI 노드 스펙 (nodes.py)

클래스명 `LLMHubGenerate`, 표시명 "LLM Hub Generate", CATEGORY `"LLM Hub"`.

동작 규칙:

1. `image`가 있으면 `utils/image_io.py`로 PNG 저장 → 경로 리스트. 저장 위치는 `workspace_dir/_llmhub_tmp/`(file_access 시) 또는 ComfyUI temp 폴더. 실행 종료 후 삭제하지 않는다(디버깅 편의), 단 매 실행 시 같은 폴더를 비우고 시작.
2. `LLMRequest` 구성 → 팩토리로 백엔드 생성 → `generate()` 호출.
3. 에러 시에도 예외를 밖으로 던지지 않는다: `text=""`, `status="error: <요약>"`, `debug=<stderr/원시응답 꼬리>` 반환.
4. `seed` 값 자체는 사용하지 않는다 — ComfyUI가 "입력 변경 → 재실행"으로 인식하게 하는 캐시 무효화 용도. 코드 주석으로 명시. (`IS_CHANGED`는 구현하지 않는다.)
5. `temperature`/`max_tokens`는 지원하는 백엔드에만 전달(미지원 CLI는 무시하고 debug에 "unsupported: temperature" 기록).

기본 모델 규칙:

- lmstudio: 빈값이면 요청에 model 필드 생략 → 서버가 로드된 모델로 처리하는지 실측, 안 되면 `/v1/models` 조회로 첫 모델 사용
- claude / codex: 빈값이면 `--model`/`-m` 플래그 자체를 생략(CLI 기본값 사용)
- gemini: 빈값이면 `config.json`의 `defaults.gemini_model`(기본 `"gemini-2.5-flash"` — Pro 모델은 구독 쿼터 소진이 빠르므로 명시 시에만)

<!-- 수정(lmstudio 기본 모델): LM Studio 실서버를 이 환경에서 띄울 수 없어 양쪽 경로를 모두 구현했다.
     model 을 생략해 보내고, 서버가 모델 관련 4xx(400/404/422 + 본문에 "model")로 거절하면
     /v1/models 의 첫 모델로 딱 1회 재시도한다. 두 경로 모두 가짜 서버로 테스트했다. -->
<!-- 수정(입력 추가): video / video_path / video_max_frames 를 추가했다(§16). -->

---

## 6. 공통 데이터 모델 (backends/base.py)

문서 원안대로 `LLMRequest` / `LLMResponse` / `BaseBackend` 를 구현했다.

<!-- 수정: LLMRequest 에 video_paths(list), video_max_frames(int) 를 추가했다(§16).
     CLI 백엔드 공용 헬퍼도 base.py 에 모았다:
       merge_system_prompt(§8.5) / workspace_hint(§7) / validate_workspace(§7)
       detect_login_error / detect_rate_limit / tail_lines / truncate_debug
       stage_media  — 미디어를 CLI 의 cwd 안으로 복사(CLI 읽기 툴이 cwd 밖을 못 보기 때문)
       frames_for_unsupported_video — 비디오 미지원 백엔드용 프레임 변환 -->

---

## 7. 워크스페이스 규칙 (공통)

- `file_access=True`인데 `workspace_dir`가 비었거나 존재하지 않으면 즉시 `status="error: workspace_dir 확인 필요"`.
- `file_access=False`면: CLI 백엔드는 매 실행 임시 빈 폴더를 만들어 `cwd`로 사용(자기 파일 시스템을 못 보게), LM Studio는 툴 자체를 요청에 넣지 않음.
- `file_access=True`면 시스템 프롬프트 끝에 자동으로 한 줄 주입:
  `"작업 루트 폴더는 {workspace_dir} 이다. 파일이 필요하면 먼저 목록을 확인한 뒤 필요한 파일만 읽어라."`

---

## 8. 백엔드별 상세

### 8.1 LMStudioBackend (HTTP, backends/lmstudio.py)

원안대로 구현했다. `POST {base_url}/v1/chat/completions`, 이미지 base64 data URI,
`file_access=True` 시 `list_dir`/`read_file` 툴 선언 + `tool_choice="auto"` 툴 루프,
최대 `tool_loop_max_iters`(기본 8)회, 초과 시 debug에 "tool loop limit".
`mcp_config` 지정 시 debug에 "lmstudio: mcp_config는 v1.5 예정, 내장 툴 루프 사용" 기록.

<!-- 수정: 바이너리 판정 로직에서 버그를 잡았다. 초기 구현은 ASCII 범위 밖 바이트 비율로
     판정해 한글 UTF-8 텍스트 파일을 "binary file" 로 오분류했다.
     → UTF-8 디코드가 되면 텍스트로 본다(청크 끝에서 멀티바이트가 잘린 경우 예외 처리). -->
<!-- 수정: 비디오는 OpenAI 호환 chat/completions 에 콘텐츠 타입 자체가 없어 미지원.
     프레임 추출로 대체한다(§16). -->

### 8.2 ClaudeCodeBackend (subprocess, backends/claude_code.py)

**실측 커맨드:**

```
claude -p --output-format json
       [--model {model}]
       [--append-system-prompt {system} | --system-prompt {system}]
       --allowedTools "Read,Glob,Grep"   # file_access=True
       --tools ""                        # file_access=False (내장 툴 전체 차단)
       [--mcp-config {path} --strict-mcp-config]
```

<!-- 수정(--max-turns): 현재 claude CLI 에 --max-turns 플래그가 없다(--help 실측).
     → 넣지 않는다. 회귀 방지를 위해 테스트로 부재를 고정했다. -->
<!-- 수정(툴 차단): 설계는 --allowedTools "" 를 제안했으나, 실측 --help 에 따르면
     내장 툴 전체를 끄는 공식 수단은 --tools "" 다("Use \"\" to disable all tools").
     → file_access=False 에는 --tools "" 를 쓴다. -->
<!-- 실측 확인(stdin): `echo "..." | claude -p` 로 stdin 프롬프트 동작을 확인했다. 원안대로 stdin 사용. -->
<!-- 실측 확인(JSON): --output-format json 의 응답에 result / is_error / usage /
     total_cost_usd / subtype / permission_denials 가 들어온다. 원안대로 result 를 text 로 쓴다. -->
<!-- 수정(시스템 프롬프트): --append-system-prompt 와 --system-prompt 가 둘 다 존재한다.
     실측 결과 append 는 정상 동작하지만(마커 토큰 주입으로 확인) Claude Code 기본
     시스템 프롬프트가 강해 문체/언어 지시가 희석될 수 있다.
     → config.json 의 defaults.claude_system_prompt_mode 로 append(기본)/replace 선택 가능하게 했다. -->
<!-- 수정(이미지): 이미지는 cwd 안에 있어야 Read 툴이 볼 수 있다. file_access=False 라도
     이미지가 있으면 Read 하나만 열어준다(워크스페이스 노출 없이 이미지만 읽게).
     실측 T4 에서 Bash 시도가 permission_denials 로 차단되는 것을 확인했다. -->

### 8.3 CodexBackend (subprocess, backends/codex.py)

**실측 커맨드:**

```
codex exec -s read-only --skip-git-repo-check
           [-m {model}] [-i {image}...]
           -o {last_message_file}
           -
```

<!-- 수정(프롬프트 전달): 설계는 "6,000자 초과 시에만 stdin" 이었으나, 실측 --help 에
     "If not provided as an argument (or if `-` is used), instructions are read from stdin"
     이 명시돼 있다. → 길이와 무관하게 항상 '-' + stdin 을 쓴다.
     Windows 인자 길이 문제가 조건부가 아니라 구조적으로 사라진다. -->
<!-- 수정(출력): 실측으로 -o/--output-last-message FILE 플래그를 확인했다.
     → stdout 전체를 text 로 쓰는 대신 이 파일에서 최종 메시지를 읽는다(로그 혼입 방지).
     파일이 비면 stdout 으로 폴백한다. 임시 파일은 항상 정리한다. -->
<!-- 확인(MCP): v1 미지원 방침 유지. mcp_config 지정 시 debug 기록만 한다.
     --dangerously-bypass-approvals-and-sandbox 는 샌드박스 해제라 채택하지 않는다. -->
<!-- 수정(비디오): -i 는 이미지 전용이라 비디오 미지원 → 프레임 추출로 대체(§16). -->

### 8.4 GeminiBackend (subprocess, backends/gemini.py)

**실측 커맨드:**

```
gemini -o json [-m {model}] --approval-mode plan --skip-trust
       (프롬프트는 stdin)
```

<!-- 수정(승인 모드): 실측 --help 에 --approval-mode 의 값으로 plan(read-only mode)이 있다.
     → 읽기 전용이 필요한 v1 요구에 정확히 맞으므로 plan 을 쓴다. --yolo 는 쓰지 않는다. -->
<!-- 수정(중요, --skip-trust): 신뢰되지 않은 폴더에서 실행하면 gemini 가
     "Approval mode overridden to \"default\" because the current folder is not trusted"
     를 출력하며 plan 모드를 조용히 강등한다(실측). 즉 --approval-mode plan 만으로는
     읽기 전용이 보장되지 않는다. → --skip-trust 를 함께 전달해야 plan 이 유지된다.
     이 조합을 테스트로 고정했다. -->
<!-- 실측 확인(stdin): -p 는 "Appended to input on stdin (if any)" 이고,
     -p 없이 stdin 만 파이프해도 헤드리스로 동작한다. → 프롬프트는 stdin 으로 보낸다. -->
<!-- 실측 확인(JSON): -o json 의 오류 응답은 {"session_id":..., "error":{"type","message","code"}} 다.
     인증 실패는 code 41 로 온다(미로그인 상태에서 직접 확인).
     → code 41 및 인증 문구를 로그인 안내로 매핑했다. 정상 응답은 response 필드를 읽는다. -->
<!-- 확인(MCP): 전역 settings.json 사이드이펙트 때문에 v1 미적용 방침 유지. debug 기록만. -->
<!-- 수정(비디오): 4개 백엔드 중 유일하게 네이티브 지원(§16). -->

### 8.5 시스템 프롬프트 병합 규칙 (플래그 없는 CLI 공통)

```
### SYSTEM
{system_prompt}
{§7의 워크스페이스 안내 한 줄 (file_access=True일 때)}

### TASK
{user_prompt}
```

<!-- 적용 대상: codex, gemini. claude 는 전용 플래그가 있어 병합을 쓰지 않는다. -->

---

## 9. 서브프로세스 공통 러너 (utils/proc.py)

원안대로 구현했다. `Popen(text=True, encoding="utf-8", errors="replace")`,
Windows `CREATE_NO_WINDOW`, env 상속 + `PYTHONIOENCODING=utf-8`,
`shutil.which()` 로 실제 경로 해석(.cmd 셔틀 대응), `TimeoutExpired` 시 kill,
`extra_args` 는 `shlex.split(posix=False)`.

<!-- 수정: 타임아웃 시 kill 후 communicate() 를 한 번 더 호출해 파이프를 비운다.
     그렇지 않으면 좀비/파이프 잔류가 생길 수 있다(T5 대응). 테스트로 고정했다. -->

---

## 10. 설정 파일 (config.example.json)

<!-- 수정: defaults 에 두 항목을 추가했다.
       gemini_approval_mode      (기본 "plan") — 응답이 계획서 형식이면 "default" 로 변경 가능
       claude_system_prompt_mode (기본 "append") — 문체 지시를 강하게 하려면 "replace" -->

---

## 11. 보안 규칙

원안대로 구현했다. realpath 기준 workspace 하위 검증, `../` 차단, 심볼릭 링크 거부,
`max_file_read_bytes` 상한, CLI 읽기 전용 지향, README에 프롬프트 인젝션 주의 명시.

<!-- 검증: 경로 탈출(../, 깊은 ../, 절대경로, 외부 심볼릭 링크) 차단을 테스트로 고정했다.
     LM Studio 툴 루프에서도 탈출 시도가 "access denied" 로 막히는지 별도 검증한다. -->

---

## 12. 마일스톤 & 완료 기준

| 단계 | 상태 |
|---|---|
| M1 | 완료 — 뼈대 + LMStudioBackend + 노드 등록 |
| M2 | 완료 — LM Studio 툴 루프 + 경로 검증 |
| M3 | 완료 — ClaudeCodeBackend (T1~T4 실측 통과) |
| M4 | 완료 — GeminiBackend (오프라인 검증까지. 로그인 스모크는 사용자 환경 필요) |
| M5 | 완료 — CodexBackend (오프라인 검증까지. 로그인 스모크는 사용자 환경 필요) |
| M6 | 완료 — 이미지/비디오 입력, README, install.bat(ASCII), mcp_config 패스스루 |

---

## 13. 테스트 (tests/)

<!-- 수정: 이 환경에는 LM Studio 도, codex/gemini 로그인도 없다.
     "돌려보지 않고 됐다고 하지 않기" 위해 검증 수단을 나눴다.

     tests/test_backends.py    — 원안의 수동 스모크 harness (--video 옵션 추가)
     tests/mock_lmstudio.py    — 가짜 LM Studio(표준 라이브러리 http.server)
     tests/test_offline.py     — 경로 보안 / 툴 루프 / 프롬프트 규칙 / 노드 계약
     tests/test_cli_backends.py— run_cli 를 가로채 커맨드 조립·파싱 검증
     tests/test_video.py       — 비디오 경로 해석 / 프레임 추출 / 백엔드 분기

     실측 스모크 결과(claude, 로그인된 환경):
       T1 단순 생성        ok  ("안녕")
       T2 시스템 프롬프트   ok  (마커 토큰 주입으로 반영 확인)
       T3 파일 읽기        ok  (fixtures/test.txt 의 고유 문장을 응답에 반영)
       T4 이미지          ok  (도형/색 정확히 서술, Bash 시도는 차단됨)
       T4-video          ok  (ffmpeg 3프레임 추출 → 프레임별 변화까지 서술)
       T5 타임아웃        ok  (좀비 없음, 단위 테스트로 고정)
     lmstudio/codex/gemini 의 실계정 스모크는 사용자 환경에서 §8 명령으로 확인 필요. -->

---

## 14. 알려진 리스크 & 트레이드오프

원안 유지. 추가로 확인/발견한 것:

<!-- 수정: 추가 리스크
     - gemini 의 approval-mode 조용한 강등: --skip-trust 누락 시 읽기 전용이 풀린다(위 §8.4).
       --skip-trust 는 "해당 폴더를 신뢰한다"는 의미이므로 workspace_dir 는 사용자가
       의도한 폴더만 지정해야 한다(README 보안 항목과 동일한 주의).
     - gemini plan 모드가 응답을 "계획" 형식으로 바꿀 가능성: 실계정 검증 불가 →
       config 로 default 전환 가능하게 열어뒀다.
     - claude append 시스템 프롬프트 희석: replace 모드로 회피 가능(§8.2).
     - 비디오 프레임 변환의 한계: 프레임 사이 움직임과 오디오는 볼 수 없다(§16). -->

---

## 15. Opus 킥오프 지시문 (복붙용)

> 이 폴더의 `ComfyUI-LLM-Hub_DESIGN.md`를 정독하고 ComfyUI-LLM-Hub를 마일스톤 M1부터 순서대로 구현하라. (…원문 유지…)

---

## 16. 비디오 입력 (설계 이후 추가)

<!-- 수정: 설계 문서에 없던 요구사항. 사용자 요청으로 추가하며, 백엔드별 지원 여부를 실측했다. -->

### 실측한 백엔드별 비디오 지원

| 백엔드 | 네이티브 비디오 | 근거 |
|---|---|---|
| **gemini** | **지원** | Gemini CLI 가 `video/*` MIME 파일을 `inlineData` 로 모델에 전달한다. `read_file`/`read_many_files` 경로에서는 오디오/비디오 파트가 버려지지 않고 `BINARY_INJECTION` 으로 재주입된다(CLI 번들 코드 확인) |
| claude | 미지원 | Read 툴은 이미지/PDF 만 받는다 |
| codex | 미지원 | `-i/--image` 는 이미지 전용 |
| lmstudio | 미지원 | OpenAI 호환 `/v1/chat/completions` 에 비디오 콘텐츠 타입이 없다 |

### 처리 방식

- **gemini**: 비디오 파일을 cwd 로 staging 하고 `@파일명` 으로 참조한다. 프레임 추출을 하지 않는다.
- **나머지 3종**: `video_max_frames`(기본 8)장을 균등 간격으로 뽑아 이미지로 전달한다.

### 프레임 추출기

pip 의존성을 늘리지 않기 위해 외부 실행 파일과 기존 패키지만 쓴다:

1. `ffmpeg` (PATH) — 우선 사용
2. `cv2` (ComfyUI 환경에 이미 있으면) — 대체
3. 둘 다 없으면 프레임 없이 진행하고 debug 에 설치 안내를 남긴다(노드는 죽지 않는다)

길이는 `ffprobe` 로 구하고, `ffprobe` 가 없으면 `ffmpeg -i` 의 stderr 에서 `Duration:` 을 파싱한다
(ffmpeg 만 설치된 환경 대응). 길이를 끝내 못 구하면 초당 1장으로 폴백한다.

### 입력

- `video` — ComfyUI `VIDEO` 입력 (문자열 경로 / dict / `save_to()` 객체를 모두 처리)
- `video_path` — 파일 경로 직접 입력 (지정 시 `video` 보다 우선)
- `video_max_frames` — 프레임 변환 시 장수

### 한계

프레임 변환 경로는 정지 화면 몇 장만 보는 것이라 빠른 움직임과 오디오는 반영되지 않는다.
영상 자체의 이해가 중요하면 `gemini` 백엔드를 쓰는 편이 정확하다.
