Three local-server backends became one click instead of a lookup, the system prompt
moved into its own editor, and a three-way review of the codebase turned up a set of
failures that were reporting themselves as success. Tests went from 350 to **439**.

## New

### `ollama` / `vllm` / `llamacpp` in the backend dropdown

llama.cpp already worked — through `openai_compat`. The problem was that there was no
way to *find that out*: the dropdown contained no string resembling "llama.cpp", so you
had to already know which entry was the right one.

| `backend` | Address it starts with | Start the server with |
|---|---|---|
| `ollama` | `http://127.0.0.1:11434` | `ollama serve` |
| `vllm` | `http://127.0.0.1:8000` | `vllm serve <model>` |
| `llamacpp` | `http://127.0.0.1:8080` | `llama-server -m <model.gguf> --port 8080` |

These are not new implementations — all three are the same backend as `openai_compat`
with that server's standard port filled in. Pick one and there is nothing to type. If
your server is on another port or another machine, `openai_base_url` still wins, and
for those three it is folded into the advanced options because an empty address box
sitting in plain sight reads like something you have to fill in.

### A model dropdown for those backends (`server_model`)

Previously only LM Studio had one; everywhere else you typed the exact model id from
memory — `qwen3:8b` or `qwen3:8b-instruct`? Get it wrong and the server returns a 404,
which tells you it failed but still not what the right name is.

`server_model` lists whatever is loaded on the OpenAI-compatible server running on this
machine. **`⟳`** refreshes it in place, so starting the server after ComfyUI no longer
means restarting anything.

> **Only servers on this machine are listed** — the three standard loopback ports, plus
> `openai_compat.base_url` when that is a loopback address too. This lookup runs every
> time ComfyUI asks the node for its inputs, and pointing that at a hosted provider
> would fire a request at somebody else's server every time you open the page. For a
> remote or paid endpoint, type the name into `model`. The API token is only ever sent
> to the address you configured, never to the three standard ports.

### The system prompt has its own editor now

The box is no longer on the node — it is folded into the advanced options, and
**`✎`** on the title bar is where you write, save and load one. The space it used to
take went to the `prompt` box, which is the one you type in on every run.

Two consequences of hiding it, both handled rather than left as surprises: **`✎` is
drawn pressed while a system prompt is set**, so a saved workflow does not look empty;
and **`▾` still reveals the raw box**, because folding something away is not the same
as removing it.

## Fixed

### Failures that reported themselves as success

A three-way review (correctness / design / test-and-docs) found the same shape in six
different places. This is the class of bug this project keeps hitting: a real failure
that is pixel-identical to "nothing happened".

- **`codex` returned `status: ok` after a failed run.** Any non-zero exit was accepted
  as long as *some* text had arrived, so a run that died on `stream error: exceeded
  retry limit` came back green with a truncated answer. `claude` and `gemini` had always
  handled this correctly — only `codex` had drifted.
- **Images and video that never reached the model reported `ok`.** A read-only
  `workspace_dir` or a typo in `video_path` left the model answering from the prompt
  alone — and in a captioning run that writes a hallucinated caption for every frame.
  The run now stops before generating, and says why.
- **`rate_limited` painted a blank panel.** It is one of the four documented statuses
  and the most common everyday failure for a subscription-CLI user, but it lacked the
  `error:` prefix the monitor checked for, so the explanation went only to `debug`.
- **`stream_view = off` removed the Stop button and the status line.** People choose
  `off` to make the node smaller and were getting "no feedback at all" — including no
  way to stop a 300-second CLI run short of cancelling the whole ComfyUI queue. Now only
  the body folds; the header stays.
- **Re-running without changing anything wiped the panel.** ComfyUI serves the node
  from cache, so no stream event arrives — and the panel had already been cleared. The
  last result is now restored instead, which also means a reopened workflow shows what
  it produced rather than an empty box.
- **Tool-loop exhaustion was diagnosed as "the model produced no text."** The model had
  produced plenty; it was cut off mid-investigation. It now says so, and names
  `tool_loop_max_iters` as the thing to raise.

### Presets could destroy what you had typed

Picking anything in the `system_preset` dropdown overwrote `system_prompt` immediately,
with no confirmation and no undo — and since the box is now folded away, the text that
vanished was not even on screen. It asks first. Overwriting an existing preset asks too,
which it never did while *deleting* one always has.

Loading a preset in the editor and then pressing Cancel used to leave the node labelled
with the new preset while still holding the old text, and it saved into the workflow
that way. The label now moves only when the text does.

### Guards that were not guarding

Three tests passed while the thing they existed to protect was broken. Each was found by
deliberately breaking the product and watching the suite stay green.

- **The widget-order guard had a hole.** ComfyUI stores widget values positionally, so
  inserting one anywhere but the end shifts every value in already-saved workflows —
  the bug that killed the node once before. Everything added since v1.1 was pinned by
  nothing: an insertion made consistently across `INPUT_TYPES`, `WIDGET_ORDER` and the
  example workflows passed all 408 tests. `FROZEN_WIDGET_ORDERS` now records the exact
  order shipped at each release and requires every one to remain a prefix.
- **A monitor test was reading the wrong 177 lines of JavaScript**, because the string
  it split on occurs twice. Deleting the two lines it was meant to protect changed
  nothing.
- **A CLI test grepped for a function call rather than its effect.** Keeping the call
  and deleting what it fed left `extra_body` silently swallowed again, with the suite
  green.

### Also

- The `seed` tooltip still said "the value itself is never used" after v1.1 started
  sending it to the server as the sampling seed
- §2-1 of the README said the model dropdown was LM Studio-only, and the title-bar
  button table said `system_prompt` always stays visible — both true when written
- The documented `config.json` was missing the `openai_compat` section that §2-1 tells
  you to edit, and `allow_unsafe_extra_args`. Pasting the sample over your own file
  dropped both keys. A test now compares the samples against `config.example.json`

## Verification

- **439 automated tests**, passing on Linux (3.10 · 3.12) and Windows in CI
- Every fix in the "guards" and "silent failures" sections was **mutation-tested**:
  the product was broken again on a scratch copy and the suite had to go red. All of
  them passed silently beforehand
- **The frontend still has no automated render check** — CI runs Python only. The
  monitor header staying visible on `off`, the restored result after a cached re-run,
  and the preset confirmation dialogs have had syntax checking and API review only
- `openai_compat` and its three aliases remain **unverified against real hardware**

## Upgrading

```
cd ComfyUI/custom_nodes/ComfyUI-LLM-Hub
git pull
```

You need **both a ComfyUI restart and a hard browser refresh (`Ctrl+Shift+R`)**.

Nothing breaks compatibility. `server_model` was appended to the end of the widget
order, so workflows saved with v1.1.0 open unchanged — and that is now checked by a
test rather than by remembering to be careful.
