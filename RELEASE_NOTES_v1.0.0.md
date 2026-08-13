A ComfyUI custom node pack that lets you pick an LLM backend from a dropdown and
generate text inside your workflow. Run a local model (LM Studio) or the
subscription CLIs you already pay for (Claude Code / Codex / Gemini) through the
same node, switching between them without rewiring anything.

## Features

**Four backends, one node**
`lmstudio` (HTTP) / `claude` / `codex` / `gemini` (subprocess). Switch instantly from the dropdown.

**Read files in a folder you choose**
Turn on `file_access` and point it at a folder; the model reads what is inside and answers from it.
The three CLIs use their own built-in read tools. LM Studio uses the `list_dir` / `read_file` tool loop provided by the node itself.

**Image and video input**
All four backends take images. For video, what each backend actually supports was investigated and split accordingly.

| Backend | Native video | Handling |
|---|---|---|
| gemini | Yes (only one) | The file is passed through |
| claude / codex / lmstudio | No | Frames sampled at even intervals, sent as images |

Frame extraction prefers `ffmpeg` (an external executable) and falls back to `cv2` if already present.

**Live monitor on the node**
The text appears on the node as it is generated. `stream_view` picks `plain` (default) / `markdown` / `off`, and switching mid-generation redraws immediately.
`plain` is the default because generating image prompts means you need to see the **literal characters** that will reach CLIP. The markdown renderer is hand-written with no external CDN, so it works offline.

**LM Studio model selection + automatic VRAM release**
ComfyUI needs VRAM for image models, so an LM Studio model holding onto it is a problem.

- `lmstudio_model` dropdown for picking the model
- `lmstudio_unload_after` (on by default) — releases **immediately** after the response via `lms unload`
- `lmstudio_ttl_sec` (default 300) — releases when idle. The safety net for when the `lms` CLI is unavailable

**Your workflow never dies**
`text` / `status` / `debug` are always returned. Not logged in, server down, folder missing — every failure comes back as a `status` string, and no exception escapes the node.

## Security

- The workspace path is validated by `realpath` — `../` escapes and symlinks pointing outside the folder are blocked
- Read-only by design: claude `Read,Glob,Grep` (no Write/Edit/Bash), codex `-s read-only`, gemini `--approval-mode plan`
- Flags in `extra_args` that unlock the sandbox (`--dangerously-*`, `--yolo`, `-s danger-full-access`, …) are blocked
- No `shell=True` in `subprocess` — pinned by a test that inspects the AST
- A scheme allow-list on links in the monitor (`javascript:` blocked)
- `config.json` is gitignored, and secrets such as `api_token` never appear in the debug output

> **Warning**: workspace file contents are untrusted input. If a file contains instructions, the model may follow them (prompt injection), so point it at the narrowest folder that works.

## Things measurement caught during development

CLI flags were never taken from documentation — every one was measured with `--help`, and that turned up several problems.

- **gemini `--approval-mode plan` is silently downgraded.** In an untrusted folder the read-only mode is dropped with only a warning. `--skip-trust` has to be passed alongside it to keep it
- **claude emits `rate_limit_event` even on normal calls.** The `rate_limit` substring inside it made every streaming call come back as `rate_limited`
- **A bug that threw away whole answers.** A correct answer to a question like "what is a 429?" was treated as an error because it contained `429`. Error detection now only looks at stderr
- **When the LM Studio SSE response has no charset**, requests decodes it as ISO-8859-1 and **Korean turns to mojibake**
- **claude has no `--max-turns`.** The official way to disable all tools is `--tools ""`
- Plus: overwriting the user's own files, a process leak through the Windows `.cmd` shim, and off-by-one ffmpeg frame numbering

## Verification

- **138 automated tests passing.** To verify without LM Studio, a fake server (including SSE) was written with the standard library; for the CLIs, `run_cli` is intercepted to check command assembly and parsing
- **claude real-account smoke passed** — text generation / system prompt / file reading / image / video
- During the image test, the model's attempt to use Bash was **actually blocked** via `permission_denials`
- Streaming was measured: 7 deltas arriving over 3.2 s, with the accumulated text matching the final result

## Installation

```
cd ComfyUI/custom_nodes
git clone https://github.com/ssain3d-lgtm/ComfyUI-LLM-Hub.git
```

Then run `install.bat` (it finds the Python ComfyUI uses, installs `requests`, and creates `config.json`) and restart ComfyUI. The **LLM Hub Generate** node appears under the `LLM Hub` category.

The pip dependency is **`requests` alone**. See the [README](README.md) for configuration and troubleshooting.

## Known limitations / still to confirm

- **The monitor's JS was never rendered inside a real ComfyUI.** Only syntax checking and API review were done. If it does not appear, start with a browser refresh (to reload the JS extension)
- **The lmstudio / codex / gemini real-account smokes are unverified** (they need a login). Please check them with the commands in README §8
- The codex `--json` event schema could not be measured, so a lenient parser handles it. Even when parsing fails the final body is read from the `-o` file, so the result itself is fine
- Whether gemini `plan` mode reshapes answers into a "plan document" can only be confirmed with a real account. If it does, set `gemini_approval_mode` to `default` in `config.json`
- Word (.docx), Hangul (.hwp) and Excel files cannot be read. PDFs work with claude and gemini only
- The video frame path only sees a few stills, so fast motion and audio are not represented

## Not in v1

Multi-turn session persistence (`--resume`), file write/edit tools, web search tools, an auto-installer.

For MCP, only claude's `--mcp-config` passthrough works. codex has a non-interactive approval issue and gemini would need global settings side effects, so neither is applied in v1 — setting it anyway never kills the node, and the reason is written to `debug`.
