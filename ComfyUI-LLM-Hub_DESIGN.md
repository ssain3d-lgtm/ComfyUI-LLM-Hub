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
<!-- 수정(별칭): openai_compat 에 더해 ollama / vllm / llamacpp 를 드롭다운에
     추가했다. 구현은 openai_compat 하나 그대로이고, 고르면 그 서버의 표준
     포트(11434 / 8000 / 8080)가 기본 주소로 잡히는 얇은 별칭이다.
     이유는 발견성이다 -- llama.cpp 를 쓰려면 "openai_compat 이 그거다" 를 먼저
     알아야 했는데 드롭다운 어디에도 llama.cpp 라는 글자가 없었다.
     이름은 반드시 목록 맨 뒤에만 붙인다(저장된 워크플로우 보호). -->
<!-- 수정(server_model): openai_compat 계열에도 모델 드롭다운을 붙였다.
     lmstudio_model 과 같은 방식(INPUT_TYPES 가 조회 -> /object_info 재요청으로
     갱신)이라 서버 라우트는 여전히 없다.
     단, **loopback 주소만 조회한다.** INPUT_TYPES 는 /object_info 요청마다
     불리므로, config 에 유료 API 주소를 적어둔 사람은 페이지를 열 때마다 남의
     서버로 요청이 나가게 된다. 목록 하나 채우자고 할 일이 아니다.
     토큰도 설정에 적힌 주소에만 보낸다(표준 포트 3개는 누구 서버인지 모른다). -->
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
<!-- 수정(extra_body): "백엔드별 고급 파라미터 전체 노출" 은 여전히 비목표다.
     위젯을 파라미터마다 만들지 않는다는 뜻이며, 대신 HTTP 백엔드에 한해
     extra_body(JSON) 한 칸으로 서버가 받는 필드를 그대로 넘길 수 있게 했다.
     위젯 수는 하나만 늘고(맨 뒤에 추가) 노출 범위는 서버가 정한다. -->
<!-- 수정(batch_mode): 설계에 없던 항목. 이미지 배치를 장별로 나눠 호출하는
     one_per_image 를 추가했다. 기존 동작(all_in_one)이 기본값이라 저장된
     워크플로우는 영향이 없다. 배치 캡션은 비목표에 적힌 적이 없고, 유사 노드
     비교에서 "가장 흔한 용도인데 불가능하다" 로 나온 구멍이다. -->

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

<!-- 수정: 저장소 이름을 ComfyUI-LLM-Hub 로 바꾸면서 아래 파일들을 저장소 루트로 옮겼다.
     ComfyUI 노드팩은 custom_nodes/ 아래로 바로 clone 하는 것이 표준이라,
     저장소 루트가 곧 노드팩 폴더가 되는 구조가 맞다. -->

```
ComfyUI-LLM-Hub/            # = 저장소 루트 (custom_nodes/ 아래로 clone)
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
<!-- 수정(seed): 유사 노드 비교에서 나온 지적. ComfyUI 에서 seed 는 "돌리면 같은
     결과" 라는 약속인데, 그 모양의 손잡이만 달아두고 재현은 안 되는 상태였다.
     → HTTP 백엔드(lmstudio / openai_compat)에 한해 0 이 아닌 값을 payload 의
     seed 필드로 함께 보낸다. 0 = 안 보냄이라 기본 설치의 요청 내용은 예전과
     같다. 실기기로 "같은 시드 → 같은 결과" 를 확인하지는 못했으므로(§0-1),
     서버가 400 을 내면 시드만 빼고 한 번 재시도한다. CLI 3종은 그대로 미사용. -->
5. `temperature`/`max_tokens`는 지원하는 백엔드에만 전달(미지원 CLI는 무시하고 debug에 "unsupported: temperature" 기록).

기본 모델 규칙:

- lmstudio: 빈값이면 요청에 model 필드 생략 → 서버가 로드된 모델로 처리하는지 실측, 안 되면 `/v1/models` 조회로 첫 모델 사용
- claude / codex: 빈값이면 `--model`/`-m` 플래그 자체를 생략(CLI 기본값 사용)
- gemini: 빈값이면 `config.json`의 `defaults.gemini_model`(기본 `"gemini-2.5-flash"` — Pro 모델은 구독 쿼터 소진이 빠르므로 명시 시에만)

<!-- 수정(gemini 기본 모델): 실측(2026-08-12, gemini-cli 0.55.1) 결과 기본값을
     "gemini-3-flash" 로 잡았다. Flash 를 쓴다는 원칙은 그대로다.
     README 의 설정 예시 두 곳이 2.5 로 남아 있었는데(공개 직전 점검에서 발견),
     그대로 복사하면 구버전 모델이 고정되므로 config.example.json 값에 맞췄다. -->

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


---

## 17. 실시간 모니터링 창 + LM Studio VRAM 관리 (설계 이후 추가)

<!-- 수정: 설계 §1 비목표에 "스트리밍 출력"이 있었으나 사용자 요청으로 v1 에 포함한다. -->

### 17.1 스트리밍 (실측한 수단)

| 백엔드 | 플래그 | 이벤트 |
|---|---|---|
| claude | `--output-format stream-json --include-partial-messages --verbose` | `stream_event.content_block_delta.delta.text_delta.text` |
| gemini | `-o stream-json` | `{"type":"message","role":"assistant","content":...,"delta":true}` (CLI 번들 소스에서 확인) |
| codex | `--json` | 스키마 미실측(로그인 불가) → 관대한 파서 + `-o` 파일 폴백 |
| lmstudio | `stream: true` (SSE) | `choices[].delta.content` |

전송 경로: 백엔드 → `utils/stream.py`(PromptServer 웹소켓, `llmhub.stream`)
→ `web/js/llmhub_monitor.js`. 누적 전문을 보내 프론트가 순서를 맞출 필요가 없다.

<!-- 수정(중요, 오탐): claude 는 정상 호출에도 매번
     {"type":"rate_limit_event", ..., "rateLimitType":"five_hour"} 를 흘린다.
     이 원문을 detect_rate_limit() 에 넘기면 "rate_limit" 부분 문자열에 걸려
     모든 스트리밍 호출이 rate_limited 로 잘못 분류된다(실측으로 발견).
     → 스트림 원문은 절대 오류 판정에 넘기지 않고, 최종 result 객체만 넘긴다.
     codex/gemini 스트리밍 경로도 같은 이유로 원문을 넘기지 않는다. -->

<!-- 수정(인코딩): LM Studio SSE 응답에 charset 이 없으면 requests 가 ISO-8859-1 로
     디코딩해 한글이 깨진다(테스트로 발견). → resp.encoding = "utf-8" 을 명시한다. -->

<!-- 수정(타임아웃): stream=True 에서 requests 의 timeout 은 청크 사이 간격만 잰다.
     → 벽시계 기준 deadline 을 따로 걸어 timeout_sec 가 전체 시간을 막게 했다. -->

`file_access=True` 인 LM Studio 는 스트리밍하지 않는다. tool_calls 가 delta 로
조각나 오면 조립이 불안정하므로, 검증된 비스트리밍 툴 루프를 유지하고
도구 진행 상황만 status 로 보여준다.

### 17.2 표시 방식

`stream_view` = `plain`(기본) / `markdown` / `off`.
기본을 plain 으로 둔 이유: 이 노드의 주 용도인 이미지 프롬프트 생성에서는
CLIP Text Encode 로 넘어갈 **문자 그대로**를 봐야 하고, 렌더링하면 `**` 같은
기호가 화면에서 사라져 실제 출력과 화면이 달라진다. 문서 요약에는 markdown 이 낫다.

마크다운 렌더러는 외부 CDN 없이 직접 구현했다(오프라인 동작).
<!-- 수정(보안): LLM 출력은 신뢰할 수 없는 입력이다. 링크 렌더링에 스킴 화이트리스트
     (http/https/mailto)를 걸어 javascript: 가 ComfyUI 오리진에서 실행되지 않게 했다. -->

### 17.3 LM Studio VRAM 관리 (공식 문서 확인 후 구현)

- `ttl`(초) 필드를 OpenAI 호환 엔드포인트가 받는다. JIT 로드 모델 기본 60분.
- `lms unload <model>` 로 즉시 해제. 대상 모델은 응답 JSON 의 `model` 필드로 특정한다.
- `/api/v0/models` 가 `state`(loaded/not-loaded)와 `type` 을 주므로 드롭다운을 만든다.
  버전에 따라 로드된 모델만 반환하는 이슈가 있어 `/v1/models` 결과와 합친다.

### 17.4 코드 리뷰에서 잡아 고친 것

<!-- 수정: 리뷰(high) 결과 10건을 모두 수정했다.
     1. web JS: onNodeCreated 시점의 node.id 는 -1 이라 id 키 Map 이 영영 매칭되지 않았다
        (모니터링 창이 아예 안 뜨는 치명적 결함) → 패널을 노드 객체에 직접 붙인다.
     2. lmstudio_model COMBO 는 서버 상태에 따라 목록이 줄어드는데, 그때 ComfyUI 기본
        검증이 저장된 값을 거부해 워크플로우 전체가 실패했다 → VALIDATE_INPUTS 로 우회.
     3. 새 위젯을 기존 위젯 사이에 끼워 넣어 예전 워크플로우의 widgets_values 가 밀렸다
        → 새 위젯은 required/optional 모두 맨 뒤에 배치.
     4. codex 스트리밍이 stdout 폴백 본문을 버려 -o 가 비면 "응답이 비어 있음" 이 됐다
        → 스트리밍으로 모은 평문을 폴백으로 넘긴다.
     5. config 의 ttl_sec/unload_after 가 읽히기만 하고 안 쓰였다
        → 위젯 기본값으로 반영 + 요청이 None 이면 config 를 따르게.
     6. gemini 스트리밍이 비정상 종료도 ok 로 표시했다 → result 이벤트/종료 코드 확인.
     7. 재실행 시 이전 결과가 남아 현재 결과처럼 보였다 → execution_start 에서 초기화.
     8. 예외 경로에서 emitter.finish() 를 안 해 "생성 중..." 으로 멈춰 있었다.
     9. SSE 타임아웃 (17.1 참조), 10. javascript: 링크 (17.2 참조). -->


---

## 18. 2차 리뷰(code-review + security-review) 수정

<!-- 수정: Fable 5 세션에서 code-review(high)와 security-review 스킬을 다시 돌려
     발견한 8+1건을 모두 고쳤다. -->

### 보안 (security-review)

- **[HIGH] extra_args 샌드박스 무력화 (RCE 표면).** 각 CLI 백엔드는 읽기 전용을
  강제하는데(claude `--tools ""`, codex `-s read-only`, gemini `--approval-mode plan`),
  사용자 `extra_args` 가 그 뒤에 붙어 `--dangerously-skip-permissions --allowedTools Bash`
  (claude), `-s danger-full-access`(codex), `--yolo`(gemini) 로 잠금을 풀 수 있었다.
  ComfyUI 를 `--listen` 으로 LAN 에 열면 원격 워크플로우가 호스트 셸을 얻는다.
  → `utils/proc.py:screen_extra_args` 로 위험 플래그(및 그 값)를 차단.
     접두사 마커(`--dangerously*`)와 정확 마커(값 페어링 포함)를 구분한다.
     `config.json` 의 `allow_unsafe_extra_args=true` 로만 열 수 있다(기본 차단).
- 그 외 표면은 안전 판정: JS 마크다운 렌더러(escapeHtml 선행 + 링크 스킴 화이트리스트),
  fs_tools 경로 검증, api_token 미노출, stream.py 페이로드.

### 정확성 (code-review)

- **오류 오분류(claude/codex/gemini).** stdout 은 정상 종료 시 모델 답변인데
  detect_login/rate_limit 를 stdout 에도 걸어, "429 가 뭐야?" 같은 질문의 정답이
  rate_limited 로 오분류돼 버려졌다. → 진단은 stderr(항상) + stdout(비정상 종료 시만).
- **프로세스 트리 kill.** Windows .cmd 셔틀은 proc.kill() 로 cmd 만 죽고 node.exe 가
  남는다. → taskkill /T 로 트리 전체 정리.
- **video_io fps 폴백 off-by-one + 스테일 프레임.** ffmpeg image2 는 01 부터 저장하는데
  00 부터 수집해 마지막 프레임을 놓치고, 이전 영상 프레임이 섞였다.
  → 추출 전 폴더 비우기 + `-start_number 0` + 실제 파일 이름순 수집.
- **stage_media 파괴적 덮어쓰기.** cwd 에 동명의 사용자 파일이 있으면 덮어썼다.
  → 전용 `_llmhub_media/` 하위로 격리.
- **lmstudio 스트리밍 타임아웃 status=ok.** 잘린 응답을 성공으로 위장했다 → error 로.
- **VALIDATE_INPUTS 과잉 우회.** `**kwargs` 는 모든 입력 검증을 끈다 →
  `lmstudio_model` 만 명시해 우회 범위를 좁혔다.
- **다중 비디오 프레임 클로버.** 영상별 `_llmhub_frames_{i}/` 로 격리.
- **posix shlex.** 리눅스/맥에서 posix=False 는 따옴표를 argv 에 남긴다 → 플랫폼 분기.

### 실사용성

- 20개 위젯 전부에 한국어 tooltip 추가.
- `lmstudio_*` 위젯은 backend=lmstudio 일 때만 표시(web JS 토글).
- 실사용 시나리오 검증: 이미지 프롬프트 생성 / file_access 폴더 미입력 /
  없는 폴더 / 위험 플래그 입력 — 모두 명확한 한국어 status 로 안내.
