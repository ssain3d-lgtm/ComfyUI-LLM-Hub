# ComfyUI-LLM-Hub

<img src="example_workflows/icon.png" width="96" align="right" alt="">

**English** | [한국어](#한국어)

---

A ComfyUI custom node pack that lets you pick an LLM backend from a dropdown and
generate text inside your workflow. Run a local model (LM Studio) or the
subscription CLIs you already pay for (Claude Code / Codex / Gemini) through the
same node, switching between them without rewiring anything.

- **5 backends**: `lmstudio` / `claude` / `codex` / `gemini` / `openai_compat` (Ollama, vLLM, llama.cpp)
- **File access**: let the model read files inside a folder you choose
- **Image and video input**: multimodal prompts
- **Live monitor on the node**: watch the text as it is generated (plain / markdown)
- **Automatic VRAM release**: unload the LM Studio model right after the response, or after an idle timeout
- **Always three outputs**: `text` / `status` / `debug` — the node never kills your workflow with an exception

> **Only `requests` is added to pip.** Everything else uses the standard library or
> packages ComfyUI already ships.

> **Note on language.** Everything the node shows you is **English** — widget names,
> tooltips, buttons, the on-node monitor, `status` values, error messages and the
> `/llmhub/health` page. Only the source comments and this file's second half are
> Korean.

## 1. Installation

1. Clone this repository into ComfyUI's `custom_nodes` folder.

   ```
   cd ComfyUI/custom_nodes
   git clone https://github.com/ssain3d-lgtm/ComfyUI-LLM-Hub.git
   ```

   If you downloaded a zip, extract it to `ComfyUI/custom_nodes/ComfyUI-LLM-Hub/`.
   (`nodes.py`, `backends/` and `utils/` must be directly inside that folder.)

2. Double-click `install.bat`. It finds the Python that ComfyUI uses, installs
   `requests`, and creates `config.json`.

   Manual install:

   ```
   <the python ComfyUI uses> -m pip install -r requirements.txt
   ```

3. Restart ComfyUI and add the **LLM Hub Generate** node (category: `LLM Hub`).

   To update later, run `git pull` inside the node pack folder.

### Dependencies

The only pip dependency is **`requests`**.
`Pillow` and `numpy` are used for image input but ship with ComfyUI, so they are
not installed separately. Video frame extraction uses **ffmpeg**, an external
executable — not a pip package. See §5.

### Example workflows

After installing, open **Workflow → Browse Templates** in the ComfyUI menu to load
the examples in this pack. (The `example_workflows/` folder is detected automatically.)

| Example | What it does |
|---|---|
| `01_image_prompt` | Generate an image prompt — wire `text` into CLIP Text Encode |
| `02_read_folder` | Read and summarize documents in a folder (`file_access`) |
| `03_lmstudio_vram` | Local LM Studio model, VRAM released right after the response |

Each example includes a note node describing what you need to prepare.
Values that differ per machine, such as folder paths, are filled in as examples —
replace them with your own.

## 2. Per-backend setup

| Backend | Requirements | How to verify |
|---|---|---|
| `lmstudio` | LM Studio server running (`http://127.0.0.1:1234`), model loaded | Open `http://127.0.0.1:1234/v1/models` in a browser |
| `claude` | Claude Code installed + Pro/Max login | Run `claude` in a terminal and check you are logged in |
| `codex` | Codex CLI installed + ChatGPT login | `codex login` |
| `gemini` | Gemini CLI installed + Google account login | Run `gemini` and log in |
| `ollama` / `vllm` / `llamacpp` | That server running locally | See §2-1 below |
| `openai_compat` | Any other OpenAI-compatible server, or a hosted provider | See §2-1 below |

- To use `file_access`, load a **tool-capable model** in LM Studio (the Qwen family works well).
- To use images or video, load a **VLM (vision) model** in LM Studio.
- If a CLI is not on your PATH, put its absolute path in `cli_paths` in `config.json`.

### 2-1. OpenAI-compatible servers (Ollama / vLLM / llama.cpp)

All three expose an OpenAI-compatible `/v1/chat/completions` endpoint. The
`openai_compat` backend reuses **exactly the same code path as LM Studio** and
only changes the address.

**Pick the server straight from the `backend` dropdown.** `ollama`, `vllm` and
`llamacpp` are the same backend as `openai_compat` with that server's standard
port already filled in, so there is nothing to type:

| `backend` | Address it uses | Start the server with |
|---|---|---|
| `ollama` | `http://127.0.0.1:11434` | `ollama serve` |
| `vllm` | `http://127.0.0.1:8000` | `vllm serve <model>` |
| `llamacpp` | `http://127.0.0.1:8080` | `llama-server -m <model.gguf> --port 8080` |
| `openai_compat` | `openai_compat.base_url` from `config.json` | anything else |

If your server is somewhere else — another port, another machine — put the address
in `openai_base_url` and it wins over the preset. On `ollama` / `vllm` / `llamacpp`
that box is **folded into the advanced options** (click **`▾`** on the title bar),
because the address is already set and an empty box sitting in plain sight reads
like something you have to fill in. On `openai_compat` it stays visible, since
there is no preset to fall back on. **Leave `/v1` off the end**; the node appends
`/v1/chat/completions` itself.

Type the model name into the `model` field above (for example `qwen3:8b`). The
dropdown is LM Studio only.

#### Hosted providers work too

`openai_compat` is not limited to servers on your own machine. Anything that
speaks the OpenAI chat-completions API works, because the only things this
backend needs are an address and a bearer token. Set the token once —

```
setx OPENAI_COMPAT_API_KEY "sk-..."
```

— or put it in `config.json` as `openai_compat.api_token`, then point
`openai_base_url` at the provider:

| Provider | `openai_base_url` |
|---|---|
| OpenAI | `https://api.openai.com` |
| OpenRouter | `https://openrouter.ai/api` |
| DeepSeek | `https://api.deepseek.com` |
| Groq | `https://api.groq.com/openai` |
| Together | `https://api.together.xyz` |

> **Leave `/v1` off the end.** The node appends `/v1/chat/completions` itself.
> `https://api.openai.com/v1` becomes `.../v1/v1/chat/completions` and 404s.

The token is sent as `Authorization: Bearer <token>` and is never written to the
`debug` output. Remember that these providers bill per token, unlike a local
server — see §7 *Cost*.

**Differences from LM Studio:**

- `ttl` is not sent. It is an LM Studio-specific field and stricter servers may return 400.
- **There is no automatic VRAM release.** Use `ollama stop <model>`, or shut the vLLM / llama.cpp server down.
- If your server needs an API key, set `openai_compat.api_token` in `config.json` or the `OPENAI_COMPAT_API_KEY` environment variable. **The LM Studio token is never reused** — sending your token to somebody else's server would be a leak.

> ⚠️ **This backend has not been verified against real hardware.**
> It reuses the code path verified with LM Studio, but per-server differences
> (SSE chunk shape, error response format) cannot be confirmed without testing.
> If you hit a problem, please report it with the `debug` output.

## 3. Node inputs

| Input | Description |
|---|---|
| `backend` | Which backend to use |
| `prompt` | User prompt |
| `system_prompt` | System prompt |
| `model` | Leave empty for the backend default |
| `file_access` | When on, the model can read files inside `workspace_dir` |
| `workspace_dir` | Working root folder (required when `file_access` is on) |
| `temperature` / `max_tokens` | **`lmstudio` / `openai_compat` only.** The three CLIs do not expose these flags, so the values are ignored and noted in `debug` |
| `timeout_sec` | Default 300 seconds |
| `video_max_frames` | How many frames to extract when converting video (default 8) |
| `stream_view` | Monitor display mode: `plain` (default) / `markdown` / `off` |
| `lmstudio_model` | LM Studio model dropdown. `(auto)` falls back to the `model` field and config |
| `server_model` | Model dropdown for `openai_compat` / `ollama` / `vllm` / `llamacpp`, read from whichever of those servers is running **on this machine**. `(auto)` falls back to the `model` field |
| `lmstudio_ttl_sec` | LM Studio idle TTL in seconds. Unloads from VRAM after this long with no request |
| `lmstudio_unload_after` | Unload from VRAM immediately after the response (on by default) |
| `openai_base_url` | Server address. Only needed when the server is **not** on its standard port (`openai_compat` and its `ollama`/`vllm`/`llamacpp` presets) |
| `system_preset` | Load a saved system prompt into the `system_prompt` box. See §3-1 |
| `seed` | Busts ComfyUI's cache so the same prompt runs again. On `lmstudio` / `openai_compat` a **non-zero** value is also sent to the server as the sampling seed; `0` sends nothing. The three CLIs have no seed flag |
| `batch_mode` | What to do with an image batch: `all_in_one` (default) or `one_per_image`. See §5 |
| `image` *(optional)* | ComfyUI IMAGE |
| `video` / `video_path` *(optional)* | ComfyUI VIDEO input, or a path to a video file |
| `mcp_config` *(optional)* | Path to an MCP config JSON (only `claude` actually applies it) |
| `extra_args` *(optional)* | Raw extra flags for the CLI (advanced). **Flags that unlock the sandbox are blocked automatically** |
| `extra_body` *(optional)* | **`lmstudio` / `openai_compat` only.** Extra JSON fields merged into the request body. See §3-2 |

> Hover over any input for a tooltip. The `lmstudio_*` widgets are only visible when `backend` is `lmstudio`.

**Title-bar buttons.** Three small buttons sit on the right of the node's title bar.
They are icon-only so they do not cover the node's name; **hover one to see what it does.**

| Button | What it does |
|---|---|
| **`▾` / `▴`** | Expand / collapse the advanced options. Most inputs are hidden by default so the node stays small; `backend`, `prompt`, `system_prompt` and the model dropdown always stay visible. The state is saved with the workflow |
| **`✎`** | Open the system prompt editor (§3-1) |
| **`⟳`** | Refresh the model dropdown. Shown for `lmstudio` and for the OpenAI-compatible backends — see below |

Both the `▾` toggle and `⟳` are also in the node's right-click menu.

**About `⟳`.** The model dropdowns are built when ComfyUI asks the node what its inputs
are, which happens once at startup. **If ComfyUI starts before your model server, the
list is stuck at `(auto)`** until you reload the page. `⟳` re-fetches it in place, so no
ComfyUI restart and no page reload. The result appears under the button for a few
seconds. The node also tries this once, quietly, when the list looks empty.

**`server_model` only lists servers on this machine.** It probes the three standard
loopback ports (11434 / 8000 / 8080), plus `openai_compat.base_url` from `config.json`
when that is a loopback address too. Remote and paid endpoints are deliberately left
out: this runs every time ComfyUI asks the node for its inputs, and pointing that at a
hosted provider would fire a request at somebody else's server every time you open the
page. For those, type the model name into `model` instead. The API token is only ever
sent to the address you configured, never to the three standard ports.

The outputs are `text` (the generated text), `status`, and `debug` (raw response and diagnostics).

`status` has four forms:

| status | Meaning |
|---|---|
| `ok` | Completed normally (`ok - N images` in `one_per_image` mode) |
| `error: ...` | Failed, with the reason appended |
| `rate_limited` | Subscription usage limit hit |
| `stopped - ...` | You pressed Stop, or ComfyUI cancelled. **Whatever arrived before the stop is still in `text`** |

`debug` ends with a `usage:` line whenever the backend reports one — for example
`usage: prompt=812 completion=140 total=952 cost=$0.0031`. `claude` reports tokens
and cost; `lmstudio` / `openai_compat` report whatever the server sends back;
`codex` and `gemini` do not report usage at all, so the line is simply absent.

Stops and timeouts are deliberately **not** `ok`. Treating a truncated result as a
success would let downstream nodes consume it as if it were complete. Even when
`status` is not `ok`, the node returns an empty `text` and the reason instead of
raising.

## 3-1. The system prompt editor

The input box on the node is small, so long prompts are hard to read and paste
into. Click the **`✎`** button on the node's title bar to open a full-size editor.

```
┌─ System prompt ───────────────────────────────── ✕ ┐
│ [ my translator ▾ ] [Load] [Save as…] [Delete]      │
│ ┌────────────────────────────────────────────────┐ │
│ │ You are a translator.                          │ │
│ │ Answer with the translation only.              │ │
│ │                                                │ │
│ └────────────────────────────────────────────────┘ │
│ Ctrl+Enter to apply · Esc to cancel [Cancel][Apply] │
└─────────────────────────────────────────────────────┘
```

- **Write or paste** the prompt in the large box, then **Apply** to put it back on the node.
- **Save as…** stores the current text under a name of your choice. **Load** brings it back later, **Delete** removes it.
- **Cancel**, **Esc** or clicking outside closes without changing the node.

The `system_preset` dropdown at the bottom of the node does the same load without
opening the editor: **picking a preset replaces the `system_prompt` box** with the
saved text.

**Where it is stored.** `system_prompts.json` next to the node pack, created from
`system_prompts.example.json` on first run and **not tracked by git** — `git pull`
never overwrites your presets. Saving is done by the server, not the browser, so
presets are still there from another browser or machine.

You can also edit the file by hand. `prompt` may be a string or a list of strings
joined with newlines, and saving from the editor writes multi-line prompts as a list
so the file stays readable:

```json
{
  "presets": [
    { "name": "my translator",
      "prompt": ["You are a translator.", "Answer with the translation only."] }
  ]
}
```

A broken file never stops the node from loading — the dropdown just falls back to
`(none)` and the reason is printed to the ComfyUI console.

> The dropdown sits at the **bottom** of the node rather than next to `system_prompt`.
> Widget order is what ComfyUI saves values by, so inserting one in the middle would
> shift every stored value in workflows you already saved.

## 3-2. `extra_body` — the escape hatch for local servers

`temperature` and `max_tokens` are the only sampling controls with their own
widget. Everything else your server accepts goes in `extra_body` as JSON, which is
merged into the request body. It is the HTTP counterpart of `extra_args`.

```json
{"top_p": 0.9, "repeat_penalty": 1.1, "stop": ["\n\n"]}
```

The most useful thing it unlocks is **JSON output**, which turns this node into a
generator other nodes can parse:

```json
{"response_format": {"type": "json_object"}}
```

Rules:

- **`lmstudio` and `openai_compat` only.** The three CLIs have no HTTP body; they say so in `debug` rather than ignoring you silently
- **Invalid JSON stops the run** with `error: extra_body is not valid JSON — ...`. It is not swallowed, because a setting that quietly does nothing is exactly the problem this widget exists to fix
- Your fields win over the node's, except `messages` and `stream`, which the node builds itself. While `file_access` is on, `tools` and `tool_choice` are locked too — the tool loop has to get back the schema it declared
- Whatever is applied or ignored is listed in `debug`

Field names are **your server's**, not this node's. If the server rejects one you
get its own HTTP 400 text back in `status`.

## 4. File access

Turn on `file_access` and set `workspace_dir`; that folder becomes the working root.

- **claude / codex / gemini**: the CLI is launched with that folder as its `cwd` and uses its own built-in read tools.
- **lmstudio**: the node itself provides two function tools, `list_dir` and `read_file`, and drives the tool-call loop.

Everything is read-only:

| Backend | How |
|---|---|
| claude | `--allowedTools "Read,Glob,Grep"` (no Write/Edit/Bash) |
| codex | `-s read-only` sandbox |
| gemini | `--approval-mode plan` (read-only mode) |
| lmstudio | The tools the node provides are read-only |

When `file_access` is off, the CLI backends run in a **fresh empty temporary folder**
each time, so your file system is not visible to them.

> ### Security note: workspace files are untrusted input
> If a file contains something like "ignore all previous instructions and …", the
> model may follow it (prompt injection).
> **Point `workspace_dir` at the narrowest folder that works.** The node blocks paths
> that escape the folder (`../`) and symlinks pointing outside it, but it does not
> inspect the *contents* of files inside the folder.

## 5. Image and video input

### Images

All five backends support images. `lmstudio` and `openai_compat` send a base64
data URI; the CLIs either place the file in the working folder and let the model
read it (claude / gemini) or pass it with `-i` (codex).

**A batch of images is two different jobs, so `batch_mode` picks which one.**

| `batch_mode` | What happens | Use it for |
|---|---|---|
| `all_in_one` *(default)* | Every image in the batch goes into **one** request. One answer comes back | "Compare these three", "pick the best of these" |
| `one_per_image` | **One request per image.** The answers come back joined by a `=====` line, in the same order as the batch | Captioning a dataset — one caption per image |

```
IMAGE (40) ──▶ LLM Hub Generate ──▶ text
               batch_mode = one_per_image
                                     caption for image 1
                                     =====
                                     caption for image 2
                                     =====
                                     ...
```

`one_per_image` costs **N calls**, so on the three CLIs it is N times the price
and N cold starts — check §7 *Cost* before pointing it at 200 images. It is
ignored when there is a video input (frames belong to one clip) or only one image.

If an image fails, its slot is kept as an empty string so caption *n* still lines
up with image *n*, and `status` says `error: 2/40 images failed - ...` rather than
`ok`. Pressing Stop finishes the current image and leaves the rest empty.

### Video — handled differently per backend

| Backend | Native video | What actually happens |
|---|---|---|
| **gemini** | **Yes** | The video file is passed through. No frame extraction |
| claude | No | **Frames are extracted and sent as images** |
| codex | No | **Frames are extracted and sent as images** |
| lmstudio | No | **Frames are extracted and sent as images** |

Gemini is the only one that accepts video natively. The others see **a handful of
still frames sampled at even intervals**, not the whole clip, so fast motion and
anything that depends on audio will be missed. Control the frame count with
`video_max_frames`.

**Frame extraction needs ffmpeg.**

- [Download ffmpeg](https://ffmpeg.org/download.html) and add it to your PATH.
- If `opencv-python` (`cv2`) already exists in your ComfyUI environment, it is used automatically as a fallback.
- With neither available, `debug` explains how to install one and only text generation proceeds.

Use either `video` (ComfyUI VIDEO input) or `video_path` (a path string). If both
are set, `video_path` wins.

## 5-2. Live monitor window

The node shows the text as it is being generated. Pick the mode with `stream_view`.

| Value | When to use it |
|---|---|
| `plain` (default) | **For image prompts.** Shows exactly the characters the model produced, symbols like `**` included, so you can see the literal string that will reach CLIP Text Encode |
| `markdown` | **For document summaries and analysis.** Renders headings, bullets and code blocks so it is easier to read |
| `off` | No display. Streaming is skipped entirely, and **the panel itself is removed** so the node shrinks |

- The **`Copy`** button in the panel header copies the generated text to the clipboard, so you can use it without wiring the `text` output anywhere.
- You can **switch modes mid-generation**; it redraws immediately.
- Scrolling up pauses auto-scroll; scrolling back to the bottom resumes it.
- Backends that use tools show progress such as `Tool: Read` at the top.
- The monitor contents are not saved into the workflow file.

How each backend streams (all measured, not assumed):

| Backend | Method |
|---|---|
| claude | `--output-format stream-json --include-partial-messages --verbose` → token level |
| gemini | `-o stream-json` → `message` events (role=assistant, delta) |
| codex | `--json` JSONL |
| lmstudio | SSE (`stream: true`) → token level |

> LM Studio does not stream when `file_access=True`. Assembling tool calls from
> fragments is unreliable, so for correctness only tool progress is displayed.

## 5-3. LM Studio model selection and VRAM

ComfyUI needs VRAM for image models, so an LM Studio model holding onto it is a problem.

**Model selection** — pick from the `lmstudio_model` dropdown.
If LM Studio is not running you will only see `(auto)`. **Start LM Studio, then refresh
your browser** to populate the list. With `(auto)`, the model is chosen from the
`model` field → `default_model` in `config.json` → whatever the server has loaded.

**VRAM release** — two mechanisms work together.

1. `lmstudio_unload_after` (on by default) — runs `lms unload <model>` right after the
   response for an **immediate** release. LM Studio's `lms` CLI must be on your PATH;
   if it is missing this is skipped and the reason is written to `debug` (generation
   still succeeds).
2. `lmstudio_ttl_sec` (default 300) — sends a `ttl` with the request so LM Studio unloads
   the model itself after that many seconds without a request. This is the safety net for
   when `lms` is unavailable. `0` means don't send it (LM Studio's own 60-minute default applies).

If you call the node repeatedly and reloading each time is too slow, turn
`lmstudio_unload_after` off and rely on `lmstudio_ttl_sec` alone.

## 6. Configuration file (`config.json`)

Created automatically on first run by copying `config.example.json`. (`config.json` is
never committed to git.)

```json
{
  "lmstudio": {
    "base_url": "http://127.0.0.1:1234",
    "api_token": "",
    "default_model": "",
    "ttl_sec": 300,
    "unload_after": true
  },
  "cli_paths": { "claude": "claude", "codex": "codex", "gemini": "gemini", "lms": "lms" },
  "defaults": {
    "gemini_model": "gemini-3-flash",
    "gemini_approval_mode": "plan",
    "claude_system_prompt_mode": "append"
  },
  "tool_loop_max_iters": 8,
  "max_file_read_bytes": 262144
}
```

- `gemini_model`: Pro models burn through the subscription quota quickly, so the default is Flash.
- `gemini_approval_mode`: `plan` is the read-only mode. If responses come back shaped like a "plan document", try `default`.
- `claude_system_prompt_mode`:
  - `append` (default) — appends to Claude Code's own system prompt. Tool-use ability is preserved.
  - `replace` — replaces the built-in prompt entirely. Use it **when you need style or language instructions to stick.**
    In `append` mode the built-in prompt is strong enough to dilute instructions like "answer in English only" — measured, not guessed.
- `lmstudio.ttl_sec` / `lmstudio.unload_after`: these become the **widget defaults** on the node. Per-run widget values take priority.
- `cli_paths.lms`: path to the LM Studio CLI, used for immediate VRAM release.
- `allow_unsafe_extra_args` (default `false`): whether `extra_args` may contain flags that unlock the read-only sandbox (`--dangerously-*`, `--allowedTools Bash`, `-s danger-full-access`, `--yolo`, …). **Blocked by default.** Only set it to `true` if you genuinely need it.
- Secrets such as `api_token` are never included in `debug` output.

## 7. Troubleshooting

### Start here: the self-check page

With ComfyUI running, open this in a browser:

```
http://127.0.0.1:8188/llmhub/health
```

It prints a plain-text report of everything the node actually depends on — whether
each CLI resolves, whether ffmpeg or cv2 is available, whether LM Studio answers,
whether the frontend JS is where ComfyUI will serve it from, and the version that is
really running. `[FAIL]` marks a required item; `[ -- ]` is optional and only disables
that one feature. Add `?json=1` for machine-readable output.

Paste this report into any bug report — it answers most of the first round of questions.

| status / symptom | Cause and fix |
|---|---|
| `git pull` says "Already up to date" but nothing changed | You are probably standing on a feature branch, not `main`. `git pull` only updates the branch you are on. Check with `git branch --show-current`, then `git checkout main` and pull again |
| `error: no response from the LM Studio server` | LM Studio is not running, or the port differs. Check the Server tab |
| `error: claude login required` | Run `claude` once in a terminal and log in |
| `error: codex login required` | `codex login` |
| `error: gemini login required` | Run `gemini` and log in with your Google account |
| `error: '...' executable not found` | The CLI is not on your PATH. Put the absolute path in `cli_paths` in `config.json` |
| `rate_limited` | Subscription limit reached. Claude is 5-hour/weekly, Gemini is daily, Codex is plan credits. For Gemini, switching to a Flash model helps |
| `error: workspace_dir needed` | `file_access` is on but the folder is empty or does not exist |
| `error: timeout(...)` | Increase `timeout_sec`. The CLIs need 2–10 s for a cold start |
| `tool loop limit` in `debug` | LM Studio only kept calling tools. Raise `tool_loop_max_iters` or make the prompt more specific |
| `unsupported: temperature` in `debug` | Expected. The CLI backends do not expose that parameter |
| ffmpeg notice after adding a video | Install ffmpeg and add it to your PATH (§5) |
| Monitor window does not appear | Hard-refresh the browser (`Ctrl+Shift+R`) so the JS extension reloads, then open the console (F12) and look for `[LLM Hub] v… monitor extension loaded`. **No such line means the JS never loaded** — check the self-check page above. Also check whether `stream_view` is `off`, which hides the panel on purpose |
| `the lms CLI was not found` in `debug` | Add LM Studio's `lms` to your PATH or set `cli_paths.lms` in `config.json`. Without it, TTL still handles the unload |
| `lmstudio_model` dropdown is empty | Start LM Studio, then **refresh the browser** |

### Speed

The CLI backends need a 2–10 s cold start plus a full agent loop, so a single call takes
seconds to tens of seconds. **For workflows that call the node many times, use the
`lmstudio` backend.** The three CLIs suit batch-style prompt generation better.

### Cost — the three CLIs bill your subscription on every call

`lmstudio` is local and free. The other three **charge your account every time the node
runs.** Measured on 2026-08-12 with the same short prompt:

| `claude_model` | Actual model | Cost per call |
|---|---|---|
| `haiku` | claude-haiku-4-5 | $0.014 |
| `opus` | claude-opus-5 | $0.047 |
| `sonnet` | claude-sonnet-5 | $0.102 |
| `fable` | claude-fable-5 | $0.196 |

**Every call is a new session, so the CLI's own system prompt (about 9,000 tokens) is
billed again each time** — even for a one-word "hi". Setting
`claude_system_prompt_mode` to `replace` in `config.json` drops input tokens from
9,218 to 5,782, saving roughly 36% of the cost, but **speed barely changes**
(4.59 s → 4.43 s). The bottleneck is the ~2 s CLI startup plus the round trip, not
the prompt size.

Check this before running a batch: 100 prompts costs $1.4 even on haiku, and $19.6 on fable.

## 8. Tests

Offline verification, no logins or servers required:

```
python -m unittest discover -s tests -p "test_*.py"
```

**384 tests, all passing on Linux and Windows.** Both platforms run in CI on every
pull request, so the badge on a PR is the real answer — Linux on Python 3.10 and 3.12,
Windows on 3.12.

Earlier versions of this file claimed three tests failed on Windows. When CI actually
ran there, the count was **two**, and both were assertions expecting `/` as the path
separator — the product was right (it hands a Windows program Windows separators), the
test was Linux-naive. Both are fixed. The baseline is now **0 on both platforms**.

Five tests skip when `ffmpeg` is absent; that is expected, not a failure.

Smoke tests against real backends (requires a logged-in environment):

```
python tests/test_backends.py --backend claude --prompt "안녕이라고만 답해"
python tests/test_backends.py --backend lmstudio --workspace tests/fixtures --file-access --prompt "test.txt 를 요약해"
python tests/test_backends.py --backend gemini --image tests/fixtures/sample.png --prompt "이 그림 설명해"
python tests/test_backends.py --backend claude --video tests/fixtures/sample_video.mp4 --prompt "이 영상 설명해"
```

## 9. Not included in v1

Multi-turn session persistence (`--resume`), file write/edit tools, web search tools,
a widget for every backend-specific parameter, an auto-installer.

*(There is no widget per parameter, but on `lmstudio` / `openai_compat` you can pass
whatever the server accepts through `extra_body` — see §3-2.)*

MCP support varies by backend:

- **claude**: give `mcp_config` a JSON path and it is passed through with `--mcp-config` (works)
- **codex**: MCP tool approval is auto-cancelled in non-interactive mode, so v1 does not support it. The workaround flags unlock the sandbox, so they are not used
- **gemini**: requires editing the global `settings.json`, which has broad side effects. Not applied in v1
- **lmstudio**: the official API-MCP is planned for v1.5. For now the node's built-in tool loop is used

Setting it anyway never kills the node — the reason is written to `debug`.

## License and trademarks

MIT. See [LICENSE](LICENSE).

This is an unofficial, independent project. It is not affiliated with, endorsed by,
or sponsored by Anthropic, OpenAI, Google, LM Studio, or Comfy Org. "Claude",
"Codex", "Gemini", "LM Studio", "Ollama" and "ComfyUI" are the trademarks of their
respective owners and are used here only to identify the tools this node pack can
talk to. You need your own account and subscription for each backend you use, and
your use of them is governed by that vendor's own terms.

---

<a id="한국어"></a>

# 한국어

[English](#comfyui-llm-hub) | **한국어**

ComfyUI에서 LLM 백엔드를 드롭다운으로 골라 텍스트를 생성하는 커스텀 노드팩입니다.
로컬 모델(LM Studio)과 이미 쓰고 있는 구독 CLI(Claude Code / Codex / Gemini)를
같은 노드 하나로 바꿔가며 쓸 수 있습니다.

- **5개 백엔드**: `lmstudio` / `claude` / `codex` / `gemini` / `openai_compat`(Ollama·vLLM·llama.cpp)
- **파일 접근**: 지정한 폴더 안의 파일을 LLM이 읽고 답할 수 있음
- **이미지 · 비디오 입력**: 멀티모달 프롬프트 지원
- **실시간 모니터링 창**: 생성 중인 텍스트를 노드 안에서 바로 확인 (plain / markdown)
- **VRAM 자동 해제**: LM Studio 모델을 응답 직후 또는 유휴 시간 뒤 내림
- **항상 3개 출력**: `text` / `status` / `debug` — 노드가 예외로 워크플로우를 죽이지 않음

---

## 1. 설치

1. ComfyUI의 `custom_nodes` 폴더에서 이 저장소를 clone 합니다.

   ```
   cd ComfyUI/custom_nodes
   git clone https://github.com/ssain3d-lgtm/ComfyUI-LLM-Hub.git
   ```

   zip으로 받았다면 압축을 풀어 `ComfyUI/custom_nodes/ComfyUI-LLM-Hub/` 에 두면 됩니다.
   (폴더 안에 `nodes.py`, `backends/`, `utils/` 가 바로 보여야 합니다.)

2. `install.bat`을 더블클릭합니다. (ComfyUI 내장 파이썬을 찾아 `requests`를 설치하고
   `config.json`을 만들어 줍니다.)

   수동 설치:

   ```
   <ComfyUI가 쓰는 python> -m pip install -r requirements.txt
   ```

3. ComfyUI를 재시작하고 **LLM Hub Generate** 노드를 추가합니다. (카테고리: `LLM Hub`)

   업데이트는 노드팩 폴더에서 `git pull` 하면 됩니다.

### 의존성

pip 의존성은 **`requests` 하나**입니다.
`Pillow`/`numpy`는 이미지 입력에만 쓰이는데 ComfyUI에 항상 들어 있으므로 따로 설치하지 않습니다.
비디오 프레임 추출에는 **ffmpeg**(외부 실행 파일)을 씁니다 — pip 패키지가 아닙니다. (§5 참조)

---

### 예제 워크플로우

설치하면 ComfyUI 메뉴의 **Workflow → Browse Templates** 에서 이 팩의 예제를 바로 열 수 있습니다.
(`example_workflows/` 폴더가 자동으로 인식됩니다.)

| 예제 | 내용 |
|---|---|
| `01_image_prompt` | 이미지 프롬프트 생성 — `text` 를 CLIP Text Encode 로 연결 |
| `02_read_folder` | 지정 폴더의 문서를 읽고 요약 (`file_access`) |
| `03_lmstudio_vram` | LM Studio 로컬 모델 + 응답 직후 VRAM 자동 해제 |

각 예제에는 무엇을 준비해야 하는지 적은 메모 노드가 함께 들어 있습니다.
폴더 경로처럼 환경마다 다른 값은 예시로 채워져 있으니 본인 경로로 바꿔 쓰세요.

## 2. 백엔드별 사전 준비

| 백엔드 | 준비물 | 확인 방법 |
|---|---|---|
| `lmstudio` | LM Studio 서버 실행(`http://127.0.0.1:1234`), 모델 로드 | 브라우저에서 `http://127.0.0.1:1234/v1/models` 접속 |
| `claude` | Claude Code 설치 + Pro/Max 로그인 | 터미널에서 `claude` 실행 → 로그인 상태 확인 |
| `codex` | Codex CLI 설치 + ChatGPT 로그인 | `codex login` |
| `gemini` | Gemini CLI 설치 + 구글 계정 로그인 | `gemini` 실행 후 로그인 |
| `ollama` / `vllm` / `llamacpp` | 해당 서버를 로컬에서 실행 | 아래 §2-1 참조 |
| `openai_compat` | 그 밖의 OpenAI 호환 서버, 또는 유료 API | 아래 §2-1 참조 |

- 파일 접근(`file_access`)을 쓰려면 LM Studio에서 **tool use를 지원하는 모델**(Qwen 계열 권장)을 로드하세요.
- 이미지/비디오를 쓰려면 LM Studio에서 **VLM(비전) 모델**을 로드해야 합니다.
- CLI가 PATH에 없으면 `config.json`의 `cli_paths`에 절대경로를 적으면 됩니다.

### 2-1. OpenAI 호환 서버 (Ollama / vLLM / llama.cpp)

Ollama·vLLM·llama.cpp 는 모두 OpenAI 호환 `/v1/chat/completions` 를 제공합니다.
`openai_compat` 백엔드는 **LM Studio 와 똑같은 코드 경로**를 쓰고 주소만 바꿉니다.

**`backend` 드롭다운에서 서버를 바로 고르면 됩니다.** `ollama` / `vllm` / `llamacpp`
는 `openai_compat` 과 같은 백엔드에 그 서버의 표준 포트만 미리 넣어둔 것이라,
주소를 칠 필요가 없습니다.

| `backend` | 쓰는 주소 | 서버 실행 |
|---|---|---|
| `ollama` | `http://127.0.0.1:11434` | `ollama serve` |
| `vllm` | `http://127.0.0.1:8000` | `vllm serve <모델>` |
| `llamacpp` | `http://127.0.0.1:8080` | `llama-server -m <모델.gguf> --port 8080` |
| `openai_compat` | `config.json` 의 `openai_compat.base_url` | 그 밖의 서버 |

서버가 다른 포트나 다른 PC 에 있으면 `openai_base_url` 에 주소를 적으세요 —
미리 넣어둔 값보다 우선합니다. `ollama` / `vllm` / `llamacpp` 에서는 이 칸이
**고급 옵션 안에 접혀 있습니다** (제목 줄의 **`▾`**). 주소가 이미 잡혀 있는데
빈 칸이 눈에 띄는 자리에 있으면 "채워야 도는구나" 로 읽히기 때문입니다.
`openai_compat` 에서는 기본값이 없으므로 그대로 보입니다.
**끝에 `/v1` 을 붙이지 마세요.** 노드가 `/v1/chat/completions` 를 직접 이어 붙입니다.

모델 이름은 위쪽 `model` 칸에 직접 적습니다 (예: `qwen3:8b`). 드롭다운은 LM Studio 전용입니다.

#### 유료 API 서비스도 됩니다

`openai_compat` 은 내 컴퓨터의 서버 전용이 아닙니다. 이 백엔드가 필요로 하는 것은
주소와 토큰뿐이라, OpenAI 챗 API 규격을 말하는 곳이면 어디든 붙습니다. 토큰을 한 번
설정해두고 —

```
setx OPENAI_COMPAT_API_KEY "sk-..."
```

— 또는 `config.json` 의 `openai_compat.api_token` 에 넣고, `openai_base_url` 을
그 서비스로 향하게 하면 됩니다.

| 서비스 | `openai_base_url` |
|---|---|
| OpenAI | `https://api.openai.com` |
| OpenRouter | `https://openrouter.ai/api` |
| DeepSeek | `https://api.deepseek.com` |
| Groq | `https://api.groq.com/openai` |
| Together | `https://api.together.xyz` |

> **끝에 `/v1` 을 붙이지 마세요.** 노드가 `/v1/chat/completions` 를 직접 이어 붙입니다.
> `https://api.openai.com/v1` 로 적으면 `.../v1/v1/chat/completions` 가 되어 404 가 납니다.

토큰은 `Authorization: Bearer <토큰>` 으로 전송되며 `debug` 출력에는 절대 실리지
않습니다. 로컬 서버와 달리 이쪽은 토큰 단위로 과금된다는 점만 기억하세요 (§7 비용 참고).

**LM Studio 와 다른 점:**

- `ttl` 을 보내지 않습니다. LM Studio 전용 필드라 다른 서버는 400 을 낼 수 있습니다
- **VRAM 자동 해제가 없습니다.** Ollama 는 `ollama stop <모델>`, vLLM·llama.cpp 는 서버를 내려야 합니다
- API 키가 필요하면 `config.json` 의 `openai_compat.api_token` 이나 환경변수 `OPENAI_COMPAT_API_KEY` 를 쓰세요. **LM Studio 토큰은 재사용하지 않습니다** (남의 서버에 토큰이 새면 안 되니까요)

> ⚠️ **이 백엔드는 실기기 검증을 하지 못했습니다.**
> LM Studio 로 검증된 코드 경로를 그대로 쓰지만, 서버마다 다른 부분(SSE 청크 모양,
> 오류 응답 형식)은 실측 없이 확인할 수 없습니다. 문제가 있으면 `debug` 출력과 함께 알려주세요.

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
| `temperature` / `max_tokens` | **`lmstudio` / `openai_compat` 에만 적용.** CLI 3종은 해당 플래그를 노출하지 않아 무시되고 `debug`에 기록됩니다 |
| `timeout_sec` | 기본 300초 |
| `video_max_frames` | 비디오를 프레임으로 바꿀 때 뽑을 장수 (기본 8) |
| `stream_view` | 모니터링 창 표시 방식: `plain`(기본) / `markdown` / `off` |
| `lmstudio_model` | LM Studio 모델 드롭다운. `(auto)`면 `model` 칸/설정을 따름 |
| `server_model` | `openai_compat` / `ollama` / `vllm` / `llamacpp` 용 모델 드롭다운. **이 컴퓨터에** 떠 있는 서버에서 읽어옵니다. `(auto)`면 `model` 칸을 따름 |
| `lmstudio_ttl_sec` | LM Studio 유휴 TTL(초). 이 시간 요청이 없으면 VRAM에서 내림 |
| `lmstudio_unload_after` | 응답 직후 즉시 VRAM에서 내림 (기본 켜짐) |
| `openai_base_url` | 서버 주소. 표준 포트가 **아닐 때만** 채우면 됩니다 (`openai_compat` 과 `ollama`/`vllm`/`llamacpp` 프리셋용) |
| `system_preset` | 저장해둔 시스템 프롬프트를 `system_prompt` 칸으로 불러옵니다. §3-1 참조 |
| `seed` | ComfyUI 캐시를 무효화해 같은 프롬프트를 다시 돌리게 합니다. `lmstudio` / `openai_compat` 에서는 **0이 아닌 값**이면 샘플링 시드로 서버에도 함께 보냅니다(0이면 안 보냅니다). CLI 3종에는 시드 플래그가 없습니다 |
| `batch_mode` | 이미지 배치를 어떻게 다룰지: `all_in_one`(기본) 또는 `one_per_image`. §5 참조 |
| `image` *(옵션)* | ComfyUI IMAGE |
| `video` / `video_path` *(옵션)* | ComfyUI VIDEO 입력 또는 비디오 파일 경로 |
| `mcp_config` *(옵션)* | MCP 설정 JSON 파일 경로 (claude만 실제 적용) |
| `extra_args` *(옵션)* | CLI에 덧붙일 원시 플래그(고급). **샌드박스를 푸는 위험 플래그는 자동 차단** |
| `extra_body` *(옵션)* | **`lmstudio` / `openai_compat` 전용.** 요청 본문에 합칠 추가 JSON 필드. §3-2 참조 |

> 각 입력 위에 마우스를 올리면 한국어 설명(툴팁)이 나옵니다. `lmstudio_*` 위젯은 backend가 `lmstudio`일 때만 보입니다.

**제목 줄 버튼.** 노드 제목 줄 오른쪽에 작은 버튼 세 개가 있습니다.
노드 이름을 가리지 않도록 아이콘만 그리므로, **마우스를 올리면 설명이 뜹니다.**

| 버튼 | 하는 일 |
|---|---|
| **`▾` / `▴`** | 고급 옵션 펼치기 / 접기. 위 입력 대부분은 기본적으로 숨겨져 노드가 작게 유지되고, `backend` / `prompt` / `system_prompt` 와 모델 드롭다운은 접어도 항상 보입니다. 펼침 상태는 워크플로우에 함께 저장됩니다 |
| **`✎`** | 시스템 프롬프트 편집창 열기 (§3-1) |
| **`⟳`** | 모델 드롭다운 다시 받기. `lmstudio` 와 OpenAI 호환 백엔드에서 보입니다 — 아래 참조 |

`▾` 와 `⟳` 는 노드 우클릭 메뉴에도 있습니다.

**`⟳` 에 대해.** 모델 드롭다운은 ComfyUI 가 노드에게 "입력이 뭐냐" 고
물을 때 만들어지는데, 그건 시작할 때 한 번뿐입니다. **ComfyUI 가 LM Studio 서버보다
먼저 뜨면 목록이 `(auto)` 하나로 굳어** 페이지를 새로 열기 전까지 그대로입니다.
`⟳` 는 그 자리에서 다시 받아옵니다 — ComfyUI 재시작도, 페이지 새로고침도 필요
없습니다. 결과는 버튼 아래에 몇 초간 표시됩니다. 목록이 비어 보이면 노드가 조용히
한 번은 알아서 시도하기도 합니다.

**`server_model` 은 이 컴퓨터의 서버만 조회합니다.** 표준 loopback 포트 3개
(11434 / 8000 / 8080)와, `config.json` 의 `openai_compat.base_url` 이 loopback 일
때 그것까지만 봅니다. 원격·유료 주소는 일부러 뺐습니다 — 이 조회는 ComfyUI 가
노드에게 입력을 물을 때마다 실행되는데, 거기에 유료 API 를 물리면 **페이지를 열
때마다 남의 서버로 요청이 나갑니다.** 그런 서버는 `model` 칸에 이름을 직접 적으세요.
토큰은 설정에 적힌 주소에만 보내고, 표준 포트 3개에는 절대 보내지 않습니다.

출력은 `text`(생성된 텍스트) / `status` / `debug`(원시 응답·진단)입니다.

`status` 값은 네 가지입니다.

| status | 뜻 |
|---|---|
| `ok` | 정상 완료 (`one_per_image` 모드에서는 `ok - N images`) |
| `error: ...` | 실패. 뒤에 한국어 사유가 붙습니다 |
| `rate_limited` | 구독 사용량 한도 |
| `stopped - ...` | 사용자가 Stop 을 눌렀거나 ComfyUI Cancel. **받은 부분까지는 `text` 에 들어 있습니다** |

백엔드가 사용량을 알려주면 `debug` 마지막에 `usage:` 줄이 붙습니다 — 예:
`usage: prompt=812 completion=140 total=952 cost=$0.0031`. `claude` 는 토큰과 비용을,
`lmstudio` / `openai_compat` 은 서버가 준 값을 그대로 보여줍니다. `codex` 와 `gemini` 는
사용량을 알려주지 않아 이 줄이 아예 없습니다.

중지와 타임아웃은 `ok` 가 아닙니다. 잘린 결과를 성공으로 보면 다운스트림이 그대로 쓰게 되므로 일부러 구분합니다.
`status`가 `ok`가 아니어도 노드는 예외를 던지지 않고 빈 `text`와 함께 이유를 돌려줍니다.

---

## 3-1. 시스템 프롬프트 편집창

노드 안의 입력칸은 작아서 긴 프롬프트를 붙여넣으면 전체가 안 보입니다.
노드 **제목 줄의 `✎` 버튼**을 누르면 큰 편집창이 열립니다.

```
┌─ System prompt ───────────────────────────────── ✕ ┐
│ [ 내 번역기 ▾ ] [Load] [Save as…] [Delete]          │
│ ┌────────────────────────────────────────────────┐ │
│ │ 너는 번역가다.                                  │ │
│ │ 번역문만 답하라.                                │ │
│ │                                                │ │
│ └────────────────────────────────────────────────┘ │
│ Ctrl+Enter to apply · Esc to cancel [Cancel][Apply] │
└─────────────────────────────────────────────────────┘
```

- 큰 칸에 **쓰거나 붙여넣고** **Apply** 를 누르면 노드에 반영됩니다.
- **Save as…** 로 지금 내용을 이름 붙여 저장하고, **Load** 로 나중에 그대로 불러옵니다. **Delete** 로 지웁니다.
- **Cancel** / **Esc** / 바깥 클릭은 노드를 건드리지 않고 닫습니다.

노드 맨 아래 `system_preset` 드롭다운으로도 같은 불러오기를 할 수 있습니다.
**프리셋을 고르면 `system_prompt` 칸이 저장된 내용으로 바뀝니다.**

**어디에 저장되나.** 노드팩 폴더의 `system_prompts.json` 입니다. 첫 실행 때
`system_prompts.example.json` 을 복사해 만들어지고 **git 추적 대상이 아니라서**
`git pull` 이 여러분의 프리셋을 덮어쓰지 않습니다. 저장은 브라우저가 아니라
서버가 하므로 다른 브라우저나 다른 기기에서 열어도 그대로 있습니다.

파일을 직접 고쳐도 됩니다. `prompt` 는 문자열이거나 문자열 목록(줄바꿈으로 이음)이고,
편집창에서 저장할 때도 여러 줄이면 목록으로 씁니다 — 나중에 파일을 열어봤을 때
읽을 수 있게 하려는 것입니다:

```json
{
  "presets": [
    { "name": "내 번역기",
      "prompt": ["너는 번역가다.", "번역문만 답하라."] }
  ]
}
```

파일이 깨져도 노드는 정상적으로 뜹니다. 드롭다운만 `(none)` 이 되고 이유가
ComfyUI 콘솔에 찍힙니다.

> 드롭다운이 `system_prompt` 옆이 아니라 노드 **맨 아래**에 있습니다. ComfyUI 는
> 위젯 순서로 값을 저장하기 때문에, 중간에 끼워 넣으면 이미 저장해둔 워크플로우의
> 값이 전부 한 칸씩 밀립니다.

## 3-2. `extra_body` — 로컬 서버용 탈출구

전용 위젯이 있는 샘플링 옵션은 `temperature` 와 `max_tokens` 둘뿐입니다. 서버가
받는 나머지 필드는 `extra_body` 에 JSON 으로 적으면 요청 본문에 그대로 합쳐집니다.
`extra_args` 의 HTTP 판이라고 보면 됩니다.

```json
{"top_p": 0.9, "repeat_penalty": 1.1, "stop": ["\n\n"]}
```

가장 쓸모 있는 건 **JSON 출력**입니다. 이걸 켜면 이 노드가 다른 노드가 파싱할 수
있는 생성기가 됩니다.

```json
{"response_format": {"type": "json_object"}}
```

규칙:

- **`lmstudio` / `openai_compat` 전용.** CLI 3종은 HTTP 본문이 없어서, 조용히 버리지 않고 `debug` 에 그렇게 적습니다
- **JSON 이 잘못되면 실행을 멈춥니다** (`error: extra_body is not valid JSON — ...`). 삼키지 않는 이유는, 적어둔 설정이 조용히 아무 일도 안 하는 것이 바로 이 위젯을 만든 이유이기 때문입니다
- 사용자가 적은 값이 노드 값보다 우선합니다. 단 `messages` 와 `stream` 은 노드가 직접 만드는 것이라 예외입니다. `file_access` 가 켜져 있으면 `tools` / `tool_choice` 도 잠깁니다 — 툴 루프는 자기가 선언한 스키마를 그대로 되받아야 하니까요
- 무엇이 적용되고 무엇이 무시됐는지는 `debug` 에 나옵니다

필드 이름은 이 노드가 아니라 **서버의 것**입니다. 서버가 거부하면 그 서버의 HTTP 400
문구가 `status` 에 그대로 돌아옵니다.

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

5개 백엔드 모두 지원합니다. lmstudio·openai_compat 는 base64 data URI로,
CLI 3종은 파일을 작업 폴더에 넣고 읽게 하거나(claude/gemini) `-i` 플래그로 넘깁니다(codex).


**이미지 배치는 성격이 다른 두 가지 작업이라, `batch_mode` 로 고릅니다.**

| `batch_mode` | 하는 일 | 쓰는 곳 |
|---|---|---|
| `all_in_one` *(기본)* | 배치의 모든 이미지가 **한 요청**에 들어갑니다. 답은 하나 | "이 셋을 비교해줘", "이 중에 제일 나은 것" |
| `one_per_image` | **장당 한 요청.** 답들이 `=====` 줄로 이어져 배치와 같은 순서로 돌아옵니다 | 데이터셋 캡션 — 장당 한 줄 |

```
IMAGE (40장) ──▶ LLM Hub Generate ──▶ text
                 batch_mode = one_per_image
                                       1번 그림 캡션
                                       =====
                                       2번 그림 캡션
                                       =====
                                       ...
```

`one_per_image` 는 **N번 호출**합니다. CLI 3종에서는 N배 요금이고 콜드 스타트도 N번이니,
200장에 걸기 전에 §7 비용을 확인하세요. 비디오 입력이 있거나(프레임은 한 영상의 조각입니다)
이미지가 한 장이면 무시됩니다.

어떤 장이 실패해도 그 자리는 빈 문자열로 남겨 **n번째 캡션이 n번째 그림과 계속 맞도록**
합니다. 그리고 `status` 는 `ok` 가 아니라 `error: 2/40 images failed - ...` 가 됩니다.
Stop 을 누르면 진행 중인 장까지만 하고 나머지는 빈 자리로 남습니다.

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

## 5-2. 실시간 모니터링 창

노드 안에 생성 중인 내용이 실시간으로 표시됩니다. `stream_view`로 방식을 고릅니다.

| 값 | 언제 쓰나 |
|---|---|
| `plain` (기본) | **이미지 프롬프트 생성용.** 모델이 낸 문자를 있는 그대로 보여줍니다. `**` 같은 기호도 그대로 보이므로 CLIP Text Encode로 넘어갈 실제 문자열을 확인할 수 있습니다 |
| `markdown` | **문서 요약/분석용.** 제목·불릿·코드블록을 렌더링해 읽기 편합니다 |
| `off` | 표시하지 않음. 스트리밍 자체를 하지 않고 **패널도 사라져 노드가 작아집니다** |

- 패널 헤더의 **`Copy`** 버튼으로 생성된 텍스트를 클립보드에 담을 수 있습니다. `text` 출력을 어딘가에 연결하지 않아도 결과를 꺼낼 수 있습니다.
- 모드는 **생성 중에 바꿔도** 즉시 다시 그려집니다.
- 위로 스크롤하면 자동 스크롤이 멈추고, 맨 아래로 내리면 다시 따라갑니다.
- 도구를 쓰는 백엔드는 상단에 `Tool: Read` 같은 진행 상황이 표시됩니다.
- 모니터링 창 내용은 워크플로우 파일에 저장되지 않습니다.

백엔드별 스트리밍 방식(모두 실측):

| 백엔드 | 방식 |
|---|---|
| claude | `--output-format stream-json --include-partial-messages --verbose` → 토큰 단위 |
| gemini | `-o stream-json` → `message`(role=assistant, delta) 이벤트 |
| codex | `--json` JSONL |
| lmstudio | SSE (`stream: true`) → 토큰 단위 |

> `file_access=True`인 LM Studio는 스트리밍하지 않습니다. 도구 호출이 조각으로 오면 조립이 불안정해서, 정확성을 위해 도구 진행 상황만 표시합니다.

---

## 5-3. LM Studio 모델 선택과 VRAM 관리

ComfyUI는 이미지 모델이 VRAM을 써야 하므로, LM Studio가 모델을 물고 있으면 문제가 됩니다.

**모델 선택** — `lmstudio_model` 드롭다운에서 고릅니다.
LM Studio가 꺼져 있으면 `(auto)`만 보입니다. **LM Studio를 켠 뒤 브라우저를 새로고침**하면 목록이 채워집니다.
`(auto)`면 `model` 칸 → `config.json`의 `default_model` → 서버가 로드한 모델 순으로 정해집니다.

**VRAM 해제** — 두 가지가 함께 걸려 있습니다.

1. `lmstudio_unload_after` (기본 켜짐) — 응답 직후 `lms unload <모델>`로 **즉시** 내립니다.
   LM Studio의 `lms` CLI가 PATH에 있어야 합니다. 없으면 건너뛰고 `debug`에 이유를 남깁니다(생성은 정상).
2. `lmstudio_ttl_sec` (기본 300초) — 요청에 `ttl`을 실어 보내, 그 시간 동안 요청이 없으면 LM Studio가 알아서 내립니다.
   `lms`가 없을 때의 안전망입니다. `0`이면 보내지 않습니다(LM Studio 기본값 60분 적용).

반복 호출이 많아 매번 다시 로드하는 게 느리다면 `lmstudio_unload_after`를 끄고 `lmstudio_ttl_sec`만 쓰세요.

---

## 6. 설정 파일 (`config.json`)

첫 실행 시 `config.example.json`을 복사해 자동 생성됩니다. (`config.json`은 git에 올라가지 않습니다.)

```json
{
  "lmstudio": {
    "base_url": "http://127.0.0.1:1234",
    "api_token": "",
    "default_model": "",
    "ttl_sec": 300,
    "unload_after": true
  },
  "cli_paths": { "claude": "claude", "codex": "codex", "gemini": "gemini", "lms": "lms" },
  "defaults": {
    "gemini_model": "gemini-3-flash",
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
- `lmstudio.ttl_sec` / `lmstudio.unload_after`: **노드 위젯의 기본값**이 됩니다. 개별 실행은 위젯 값이 우선합니다.
- `cli_paths.lms`: LM Studio CLI 경로. 즉시 VRAM 해제에 씁니다.
- `allow_unsafe_extra_args`(기본 `false`): `extra_args`로 읽기 전용 잠금을 푸는 위험 플래그(`--dangerously-*`, `--allowedTools Bash`, `-s danger-full-access`, `--yolo` 등)를 허용할지. **기본은 차단**입니다. 꼭 필요할 때만 `true`로 여세요.
- `api_token` 같은 비밀값은 `debug` 출력에 절대 포함되지 않습니다.

---

## 7. 트러블슈팅

### 먼저 여기부터: 자가 진단 페이지

ComfyUI를 켠 상태에서 브라우저로 여세요.

```
http://127.0.0.1:8188/llmhub/health
```

노드가 실제로 의존하는 것들을 한 번에 확인해 줍니다 — CLI 4종이 잡히는지, ffmpeg/cv2가 있는지,
LM Studio가 응답하는지, 프론트엔드 JS가 ComfyUI가 서빙할 위치에 있는지, 그리고 **지금 실제로 도는 버전**.

`[FAIL]`은 필수 항목이 깨진 것이고, `[ -- ]`는 없어도 되는 항목(해당 기능만 못 씁니다)입니다.
`?json=1`을 붙이면 기계용 JSON으로 나옵니다.

문제를 물어보실 때 이 결과를 통째로 붙여넣으시면 첫 라운드 질문이 대부분 해결됩니다.

| status / 증상 | 원인과 해결 |
|---|---|
| `git pull` 했는데 "Already up to date" 라고만 나옴 | 지금 `main`이 아니라 피처 브랜치에 서 있을 가능성이 큽니다. `git pull`은 **서 있는 브랜치만** 당겨옵니다. `git branch --show-current`로 확인하고 `git checkout main` 후 다시 pull 하세요 |
| `error: no response from the LM Studio server` | LM Studio가 꺼져 있거나 포트가 다릅니다. 서버 탭에서 실행 여부와 포트를 확인하세요 |
| `error: claude login required` | 터미널에서 `claude`를 한 번 실행해 로그인하세요 |
| `error: codex login required` | `codex login` |
| `error: gemini login required` | `gemini`를 실행해 구글 계정으로 로그인하세요 |
| `error: '...' executable not found` | CLI가 PATH에 없습니다. `config.json`의 `cli_paths`에 절대경로를 넣으세요 |
| `rate_limited` | 구독 한도에 걸렸습니다. Claude는 5시간/주간, Gemini는 일일, Codex는 플랜 크레딧 기준입니다. Gemini는 Flash 모델로 바꾸면 완화됩니다 |
| `error: workspace_dir needed` | `file_access`를 켰는데 폴더가 비었거나 존재하지 않습니다 |
| `error: timeout(...)` | `timeout_sec`를 늘리세요. CLI는 콜드스타트에만 2~10초가 걸립니다 |
| `debug`에 `tool loop limit` | LM Studio가 도구 호출만 반복했습니다. `tool_loop_max_iters`를 늘리거나 프롬프트를 더 구체적으로 쓰세요 |
| `debug`에 `unsupported: temperature` | 정상입니다. CLI 백엔드는 해당 파라미터를 노출하지 않습니다 |
| 비디오를 넣었는데 `ffmpeg` 안내가 뜸 | ffmpeg을 설치하고 PATH에 추가하세요 (§5) |
| 모니터링 창이 안 보임 | 브라우저를 **하드 새로고침**(`Ctrl+Shift+R`)한 뒤 F12 콘솔에 `[LLM Hub] v… monitor extension loaded` 줄이 있는지 보세요. **그 줄이 없으면 JS가 아예 로드되지 않은 것**이니 위 자가 진단 페이지를 확인하세요. `stream_view`가 `off`면 의도적으로 숨긴 것입니다 |
| `debug`에 `the lms CLI was not found` | LM Studio 설치 폴더의 `lms`를 PATH에 넣거나 `config.json`의 `cli_paths.lms`에 절대경로를 지정하세요. 없어도 TTL로는 해제됩니다 |
| `lmstudio_model` 드롭다운이 비어 있음 | LM Studio를 켠 뒤 **브라우저를 새로고침**하세요 |

### 속도

CLI 백엔드는 콜드스타트 2~10초에 에이전트 루프까지 돌기 때문에 한 번 호출에 수 초에서 수십 초가 걸립니다.
**대량 반복 호출 워크플로우에는 `lmstudio` 백엔드를 쓰는 편이 낫습니다.** CLI 3종은 배치성 프롬프트 생성에 적합합니다.

### 비용 — CLI 3종은 호출마다 구독 크레딧이 나갑니다

`lmstudio` 는 로컬이라 무료입니다. 나머지 셋은 **노드를 한 번 실행할 때마다 계정에
과금됩니다.** 실측(2026-08-12, 같은 짧은 프롬프트):

| `claude_model` | 실제 모델 | 1회 비용 |
|---|---|---|
| `haiku` | claude-haiku-4-5 | $0.014 |
| `opus` | claude-opus-5 | $0.047 |
| `sonnet` | claude-sonnet-5 | $0.102 |
| `fable` | claude-fable-5 | $0.196 |

**매 호출이 새 세션이라 CLI 자체의 시스템 프롬프트(약 9,000 토큰)가 매번 다시
청구됩니다.** "안녕" 한 마디에도 그렇습니다. `config.json` 의
`claude_system_prompt_mode` 를 `replace` 로 바꾸면 입력 토큰이 9,218 → 5,782 로
줄어 비용이 약 36% 절감되지만, **속도는 거의 그대로입니다**(4.59s → 4.43s).
병목이 프롬프트 크기가 아니라 CLI 기동 약 2초 + 왕복이기 때문입니다.

배치로 돌리기 전에 반드시 확인하세요. 프롬프트 100개면 haiku 로도 $1.4,
fable 이면 $19.6 입니다.

---

## 8. 테스트

로그인/서버 없이 도는 오프라인 검증:

```
python -m unittest discover -s tests -p "test_*.py"
```

**384종이며 리눅스와 Windows 양쪽에서 전부 통과합니다.** PR 마다 CI 가 두 플랫폼을
모두 돌리므로 PR 화면의 초록/빨강이 실제 답입니다 — 리눅스는 Python 3.10 · 3.12,
Windows 는 3.12.

이전 판에는 "Windows 에서 3종 실패" 라고 적혀 있었습니다. **CI 를 실제로 돌려보니
2종이었고**, 둘 다 경로 구분자를 `/` 로 기대하는 단정이었습니다 — 네이티브
프로그램에 네이티브 구분자를 넘기는 제품 동작이 옳고 테스트가 리눅스 가정이었던
것입니다. 둘 다 고쳤습니다. **이제 기준선은 양쪽 다 0** 입니다.

`ffmpeg` 이 없으면 5종이 skip 됩니다. 이건 실패가 아니라 정상입니다.

실제 백엔드 스모크 테스트(로그인된 환경 필요):

```
python tests/test_backends.py --backend claude --prompt "안녕이라고만 답해"
python tests/test_backends.py --backend lmstudio --workspace tests/fixtures --file-access --prompt "test.txt 를 요약해"
python tests/test_backends.py --backend gemini --image tests/fixtures/sample.png --prompt "이 그림 설명해"
python tests/test_backends.py --backend claude --video tests/fixtures/sample_video.mp4 --prompt "이 영상 설명해"
```

---

## 9. v1에서 하지 않는 것

멀티턴 세션 유지(`--resume`), 파일 쓰기/편집 도구, 웹검색 도구,
백엔드별 고급 파라미터마다 위젯을 만드는 것, 자동 설치기.

*(파라미터마다 위젯을 만들지는 않지만, `lmstudio` / `openai_compat` 에서는 서버가
받는 필드를 `extra_body` 로 그대로 넘길 수 있습니다 — §3-2 참조.)*

MCP는 백엔드마다 상황이 다릅니다:

- **claude**: `mcp_config`에 JSON 경로를 주면 `--mcp-config`로 전달됩니다 (동작)
- **codex**: 비대화형 모드에서 MCP 도구 승인이 자동 취소되는 이슈가 있어 v1 미지원. 우회 플래그는 샌드박스를 해제하므로 쓰지 않습니다
- **gemini**: 전역 `settings.json`을 건드려야 해서 사이드이펙트가 큽니다. v1 미적용
- **lmstudio**: 공식 API-MCP는 v1.5 예정. 지금은 노드 내장 도구 루프를 씁니다

지정해도 노드가 죽지 않고 `debug`에 사유를 남깁니다.

---

## 라이선스와 상표

MIT 입니다. [LICENSE](LICENSE) 를 보세요.

이것은 비공식 개인 프로젝트입니다. Anthropic, OpenAI, Google, LM Studio, Comfy Org
어느 곳과도 제휴·후원·승인 관계가 없습니다. "Claude", "Codex", "Gemini",
"LM Studio", "Ollama", "ComfyUI" 는 각 소유자의 상표이며, 이 노드팩이 어떤 도구와
통신할 수 있는지를 나타내기 위해서만 쓰였습니다. 각 백엔드는 **본인 계정과 구독**이
있어야 쓸 수 있고, 그 사용은 해당 업체의 이용약관을 따릅니다.
