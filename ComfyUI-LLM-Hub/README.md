# ComfyUI-LLM-Hub

ComfyUI에서 LLM 백엔드를 드롭다운으로 골라 텍스트를 생성하는 커스텀 노드팩입니다.
로컬 모델(LM Studio)과 이미 쓰고 있는 구독 CLI(Claude Code / Codex / Gemini)를
같은 노드 하나로 바꿔가며 쓸 수 있습니다.

- **4개 백엔드**: `lmstudio` / `claude` / `codex` / `gemini`
- **파일 접근**: 지정한 폴더 안의 파일을 LLM이 읽고 답할 수 있음
- **이미지 · 비디오 입력**: 멀티모달 프롬프트 지원
- **항상 3개 출력**: `text` / `status` / `debug` — 노드가 예외로 워크플로우를 죽이지 않음

---

## 1. 설치

1. 이 폴더를 통째로 ComfyUI의 `custom_nodes` 아래에 둡니다.

   ```
   ComfyUI/custom_nodes/ComfyUI-LLM-Hub/
   ```

2. `install.bat`을 더블클릭합니다. (ComfyUI 내장 파이썬을 찾아 `requests`를 설치하고
   `config.json`을 만들어 줍니다.)

   수동 설치:

   ```
   <ComfyUI가 쓰는 python> -m pip install -r requirements.txt
   ```

3. ComfyUI를 재시작하고 **LLM Hub Generate** 노드를 추가합니다. (카테고리: `LLM Hub`)

### 의존성

pip 의존성은 **`requests` 하나**입니다.
`Pillow`/`numpy`는 이미지 입력에만 쓰이는데 ComfyUI에 항상 들어 있으므로 따로 설치하지 않습니다.
비디오 프레임 추출에는 **ffmpeg**(외부 실행 파일)을 씁니다 — pip 패키지가 아닙니다. (§5 참조)

---

## 2. 백엔드별 사전 준비

| 백엔드 | 준비물 | 확인 방법 |
|---|---|---|
| `lmstudio` | LM Studio 서버 실행(`http://127.0.0.1:1234`), 모델 로드 | 브라우저에서 `http://127.0.0.1:1234/v1/models` 접속 |
| `claude` | Claude Code 설치 + Pro/Max 로그인 | 터미널에서 `claude` 실행 → 로그인 상태 확인 |
| `codex` | Codex CLI 설치 + ChatGPT 로그인 | `codex login` |
| `gemini` | Gemini CLI 설치 + 구글 계정 로그인 | `gemini` 실행 후 로그인 |

- 파일 접근(`file_access`)을 쓰려면 LM Studio에서 **tool use를 지원하는 모델**(Qwen 계열 권장)을 로드하세요.
- 이미지/비디오를 쓰려면 LM Studio에서 **VLM(비전) 모델**을 로드해야 합니다.
- CLI가 PATH에 없으면 `config.json`의 `cli_paths`에 절대경로를 적으면 됩니다.

---

## 3. 노드 입력

| 입력 | 설명 |
|---|---|
| `backend` | 사용할 백엔드 |
| `prompt` | 유저 프롬프트 |
| `system_prompt` | 시스템 프롬프트 |
| `model` | 비워두면 백엔드 기본값 |
| `file_access` | 켜면 `workspace_dir` 안의 파일을 읽을 수 있음 |
| `workspace_dir` | 작업 루트 폴더 (file_access를 켰다면 필수) |
| `temperature` / `max_tokens` | **lmstudio에만 적용.** CLI 3종은 해당 플래그를 노출하지 않아 무시되고 `debug`에 기록됩니다 |
| `timeout_sec` | 기본 300초 |
| `video_max_frames` | 비디오를 프레임으로 바꿀 때 뽑을 장수 (기본 8) |
| `seed` | **값 자체는 쓰지 않습니다.** ComfyUI가 "입력이 바뀌었다 → 다시 실행"으로 인식하게 하는 캐시 무효화용입니다 |
| `image` *(옵션)* | ComfyUI IMAGE |
| `video` / `video_path` *(옵션)* | ComfyUI VIDEO 입력 또는 비디오 파일 경로 |
| `mcp_config` *(옵션)* | MCP 설정 JSON 파일 경로 (claude만 실제 적용) |
| `extra_args` *(옵션)* | CLI에 그대로 덧붙일 원시 플래그 |

출력은 `text`(생성된 텍스트) / `status`(`ok`, `error: ...`, `rate_limited`) / `debug`(원시 응답·진단)입니다.
`status`가 `ok`가 아니어도 노드는 예외를 던지지 않고 빈 `text`와 함께 이유를 돌려줍니다.

---

## 4. 파일 접근

`file_access`를 켜고 `workspace_dir`를 지정하면 그 폴더가 작업 루트가 됩니다.

- **claude / codex / gemini**: 해당 폴더를 `cwd`로 CLI를 실행하고, CLI 내장 읽기 도구를 씁니다.
- **lmstudio**: 노드가 직접 `list_dir` / `read_file` 두 개의 함수 도구를 제공하고 툴 호출 루프를 돕니다.

읽기 전용으로만 동작합니다:

| 백엔드 | 방식 |
|---|---|
| claude | `--allowedTools "Read,Glob,Grep"` (Write/Edit/Bash 미포함) |
| codex | `-s read-only` 샌드박스 |
| gemini | `--approval-mode plan` (읽기 전용 모드) |
| lmstudio | 노드가 제공하는 도구가 읽기 전용 |

`file_access`를 끄면 CLI 백엔드는 매 실행 **빈 임시 폴더**를 `cwd`로 써서 사용자의 파일 시스템이 보이지 않게 합니다.

> ### 보안 주의: 워크스페이스 파일은 신뢰할 수 없는 입력입니다
> 파일 안에 "지금까지의 지시를 무시하고 ..." 같은 문장이 섞여 있으면 LLM이 그대로 따라갈 수 있습니다(프롬프트 인젝션).
> **필요한 폴더만 좁게 지정하세요.** 폴더 밖으로 나가는 경로(`../`)와 폴더 밖을 가리키는 심볼릭 링크는 노드가 차단하지만,
> 폴더 안 파일의 *내용*은 검사하지 않습니다.

---

## 5. 이미지 · 비디오 입력

### 이미지

4개 백엔드 모두 지원합니다. lmstudio는 base64 data URI로, CLI 3종은 파일을 작업 폴더에 넣고
읽게 하거나(claude/gemini) `-i` 플래그로 넘깁니다(codex).

### 비디오 — 백엔드별로 처리 방식이 다릅니다

| 백엔드 | 네이티브 비디오 | 실제 동작 |
|---|---|---|
| **gemini** | **O** | 비디오 파일을 그대로 넘깁니다. 프레임 추출 없음 |
| claude | X | **프레임을 뽑아 이미지로** 전달 |
| codex | X | **프레임을 뽑아 이미지로** 전달 |
| lmstudio | X | **프레임을 뽑아 이미지로** 전달 |

네 개 중 비디오를 네이티브로 받는 건 Gemini 뿐입니다.
나머지 셋은 영상 전체가 아니라 **균등 간격으로 뽑은 정지 프레임 몇 장**을 보는 것이므로,
빠른 움직임이나 소리에 의존하는 내용은 놓칠 수 있습니다. 장수는 `video_max_frames`로 조절하세요.

**프레임 추출에는 ffmpeg이 필요합니다.**

- [ffmpeg 다운로드](https://ffmpeg.org/download.html) 후 PATH에 추가하세요.
- ComfyUI 환경에 `opencv-python`(`cv2`)이 이미 있으면 그것도 자동으로 사용합니다.
- 둘 다 없으면 `debug`에 설치 안내가 나오고 텍스트 생성만 진행됩니다.

비디오는 `video`(ComfyUI VIDEO 입력) 또는 `video_path`(파일 경로 문자열) 중 아무거나 쓰면 됩니다.
`video_path`가 있으면 그쪽이 우선입니다.

---

## 6. 설정 파일 (`config.json`)

첫 실행 시 `config.example.json`을 복사해 자동 생성됩니다. (`config.json`은 git에 올라가지 않습니다.)

```json
{
  "lmstudio": {
    "base_url": "http://127.0.0.1:1234",
    "api_token": "",
    "default_model": ""
  },
  "cli_paths": { "claude": "claude", "codex": "codex", "gemini": "gemini" },
  "defaults": {
    "gemini_model": "gemini-2.5-flash",
    "gemini_approval_mode": "plan",
    "claude_system_prompt_mode": "append"
  },
  "tool_loop_max_iters": 8,
  "max_file_read_bytes": 262144
}
```

- `gemini_model`: Pro 모델은 구독 쿼터를 빨리 소진하므로 기본값은 Flash입니다.
- `gemini_approval_mode`: `plan`은 읽기 전용 모드입니다. 응답이 "계획서" 형식으로 나온다면 `default`로 바꿔보세요.
- `claude_system_prompt_mode`:
  - `append`(기본) — Claude Code 기본 시스템 프롬프트에 덧붙입니다. 도구 사용 능력이 유지됩니다.
  - `replace` — 기본 프롬프트를 통째로 바꿉니다. **문체·언어 지시를 강하게 먹이고 싶을 때** 쓰세요.
    `append` 모드에서는 기본 프롬프트가 강해서 "영어로만 답해" 같은 지시가 희석되는 것을 실측으로 확인했습니다.
- `api_token` 같은 비밀값은 `debug` 출력에 절대 포함되지 않습니다.

---

## 7. 트러블슈팅

| status / 증상 | 원인과 해결 |
|---|---|
| `error: LM Studio 서버 응답 없음` | LM Studio가 꺼져 있거나 포트가 다릅니다. 서버 탭에서 실행 여부와 포트를 확인하세요 |
| `error: claude 로그인 필요` | 터미널에서 `claude`를 한 번 실행해 로그인하세요 |
| `error: codex 로그인 필요` | `codex login` |
| `error: gemini 로그인 필요` | `gemini`를 실행해 구글 계정으로 로그인하세요 |
| `error: '...' 실행 파일을 찾을 수 없습니다` | CLI가 PATH에 없습니다. `config.json`의 `cli_paths`에 절대경로를 넣으세요 |
| `rate_limited` | 구독 한도에 걸렸습니다. Claude는 5시간/주간, Gemini는 일일, Codex는 플랜 크레딧 기준입니다. Gemini는 Flash 모델로 바꾸면 완화됩니다 |
| `error: workspace_dir 확인 필요` | `file_access`를 켰는데 폴더가 비었거나 존재하지 않습니다 |
| `error: timeout(...)` | `timeout_sec`를 늘리세요. CLI는 콜드스타트에만 2~10초가 걸립니다 |
| `debug`에 `tool loop limit` | LM Studio가 도구 호출만 반복했습니다. `tool_loop_max_iters`를 늘리거나 프롬프트를 더 구체적으로 쓰세요 |
| `debug`에 `unsupported: temperature` | 정상입니다. CLI 백엔드는 해당 파라미터를 노출하지 않습니다 |
| 비디오를 넣었는데 `ffmpeg` 안내가 뜸 | ffmpeg을 설치하고 PATH에 추가하세요 (§5) |

### 속도

CLI 백엔드는 콜드스타트 2~10초에 에이전트 루프까지 돌기 때문에 한 번 호출에 수 초에서 수십 초가 걸립니다.
**대량 반복 호출 워크플로우에는 `lmstudio` 백엔드를 쓰는 편이 낫습니다.** CLI 3종은 배치성 프롬프트 생성에 적합합니다.

---

## 8. 테스트

로그인/서버 없이 도는 오프라인 검증:

```
python -m unittest discover -s tests -p "test_*.py"
```

실제 백엔드 스모크 테스트(로그인된 환경 필요):

```
python tests/test_backends.py --backend claude --prompt "안녕이라고만 답해"
python tests/test_backends.py --backend lmstudio --workspace tests/fixtures --file-access --prompt "test.txt 를 요약해"
python tests/test_backends.py --backend gemini --image tests/fixtures/sample.png --prompt "이 그림 설명해"
python tests/test_backends.py --backend claude --video tests/fixtures/sample_video.mp4 --prompt "이 영상 설명해"
```

---

## 9. v1에서 하지 않는 것

스트리밍 출력, 멀티턴 세션 유지(`--resume`), 파일 쓰기/편집 도구, 웹검색 도구,
백엔드별 고급 파라미터 전체 노출, 자동 설치기.

MCP는 백엔드마다 상황이 다릅니다:

- **claude**: `mcp_config`에 JSON 경로를 주면 `--mcp-config`로 전달됩니다 (동작)
- **codex**: 비대화형 모드에서 MCP 도구 승인이 자동 취소되는 이슈가 있어 v1 미지원. 우회 플래그는 샌드박스를 해제하므로 쓰지 않습니다
- **gemini**: 전역 `settings.json`을 건드려야 해서 사이드이펙트가 큽니다. v1 미적용
- **lmstudio**: 공식 API-MCP는 v1.5 예정. 지금은 노드 내장 도구 루프를 씁니다

지정해도 노드가 죽지 않고 `debug`에 사유를 남깁니다.
