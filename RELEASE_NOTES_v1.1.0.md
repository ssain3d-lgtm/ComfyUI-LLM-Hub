Since v1.0.0 one more backend was added, and the friction found by actually running
this inside ComfyUI has been cleared out. Everything the node shows you is now in
English, and the test count went from 138 to **350**.

## New

### Image batches: one caption per image

A batch of images used to go into a **single** request and come back as **one**
answer, which made the most common job in ComfyUI — captioning a dataset —
impossible. `batch_mode` picks which behaviour you want.

| `batch_mode` | What happens |
|---|---|
| `all_in_one` *(default)* | Every image in one request, one answer |
| `one_per_image` | One request per image; answers joined by a `=====` line, in batch order |

A failed image keeps its slot as an empty string, so caption *n* still lines up
with image *n*, and `status` says `error: 2/40 images failed - ...` instead of
`ok`. Stop is checked between images — a 40-image batch was otherwise
unstoppable. It costs N calls, so on the three CLIs it is N times the price.

### `extra_body`: JSON output and every other server field

`temperature` and `max_tokens` were the only settings that reached the server.
`extra_body` merges arbitrary JSON into the request body — the HTTP counterpart
of `extra_args`:

```json
{"top_p": 0.9, "response_format": {"type": "json_object"}}
```

`response_format` is the important one: it turns this node into a generator whose
output other nodes can parse. **Invalid JSON stops the run** rather than being
ignored — a setting that quietly does nothing is the problem this widget exists
to fix. `messages` and `stream` stay under the node's control.

### `seed` actually reaches the server

In ComfyUI a seed means "run it again and get the same thing", and this node had
that exact control while never sending it. On `lmstudio` / `openai_compat` a
**non-zero** seed now goes into the request. `0` sends nothing, so a default
install's requests are unchanged. Since "same seed → same output" could not be
verified against real hardware, a server that answers 400 with a seed present is
**retried once without it** — one field never kills a whole generation.

### Token usage and cost in `debug`

```
usage: prompt=812 completion=140 total=952 cost=$0.0031
```

The same line from every backend that reports one. Missing figures are omitted
rather than printed as `0`, because writing `0` would be a lie about what was
spent. `codex` and `gemini` do not report usage, so the line is simply absent.

### `openai_compat` reaches hosted providers

This already worked and was never documented. Set `OPENAI_COMPAT_API_KEY` (or
`openai_compat.api_token`) and point `openai_base_url` at any OpenAI-compatible
service — OpenRouter, DeepSeek, Groq, Together, api.openai.com. **Leave `/v1`
off the end**; the node appends `/v1/chat/completions` itself.

### System prompt editor

The input box on the node is small, so long prompts are hard to read and paste into.
Click the **`✎`** button on the title bar to open a full-size editor.

```
┌─ System prompt ───────────────────────────────── ✕ ┐
│ [ my translator ▾ ] [Load] [Save as…] [Delete]      │
│ ┌────────────────────────────────────────────────┐ │
│ │  (large editor — pasted text is fully visible)  │ │
│ └────────────────────────────────────────────────┘ │
│ Ctrl+Enter to apply · Esc to cancel [Cancel][Apply] │
└─────────────────────────────────────────────────────┘
```

- **Save as…** stores the prompt you just wrote under a name; **Load** brings it back later
- The `system_preset` dropdown at the bottom of the node loads one too
- **Esc** / **Cancel** / clicking outside closes without touching the node

Presets live in `system_prompts.json`. It follows the same rule as `config.json`: **not
tracked by git**, so `git pull` never overwrites your presets. The server does the
saving, not the browser, so they are still there from another browser or machine.

### OpenAI-compatible backend (`openai_compat`)

Use Ollama, vLLM and llama.cpp through the same node. All three expose an
OpenAI-compatible `/v1/chat/completions`, so rather than writing something new this
**inherits the code path already verified with LM Studio and only changes the address.**

| Server | Default address |
|---|---|
| Ollama | `http://127.0.0.1:11434` |
| vLLM | `http://127.0.0.1:8000` |
| llama.cpp | `http://127.0.0.1:8080` |

Put the address in the node's `openai_base_url` field. The LM Studio-only `ttl` field
is not sent (stricter servers return 400) and `lms unload` is not run. **The LM Studio
token is never reused** either — that is deliberate; sending your token to somebody
else's server would be a leak.

> ⚠️ **Not verified against real hardware.** It uses a verified code path, but
> per-server differences (SSE chunk shape, error response format) cannot be confirmed
> without testing.

### Stop button

Interrupt a generation from the monitor header. ComfyUI's own Cancel is recognized too.
Whatever arrived stays in `text`, and `status` is `stopped - ...` rather than `ok`.
**Treating a truncated result as a success would let downstream nodes consume it**, so
the two are deliberately distinguished.

### Advanced-options collapse button

**`▾`** on the title bar. Collapsed, only `backend` / `prompt` /
`system_prompt` and the model dropdown remain, so the node stays small. The same
toggle is in the right-click menu.

The three title-bar buttons (`▾` `✎` `⟳`) are icon-only and show what they do on
hover — text labels took up 190 px and covered the node's own title.

They are drawn on the canvas rather than made with `addWidget` — widgets take a slot in
the `widgets_values` array, so inserting one in the middle **shifts every stored value
in workflows already saved with this node**.

### Refresh the LM Studio model list without a restart

The `lmstudio_model` dropdown is built when ComfyUI asks the node what its inputs are,
which happens once at startup. **If ComfyUI starts before the LM Studio server, the list
is stuck at `(auto)`** and stays that way until you reload the page — and all you see is
"the dropdown won't open", with no hint as to why.

The **`⟳`** button on the title bar re-fetches it in place: no ComfyUI restart, no page
reload. It reuses ComfyUI's own `/object_info` route rather than adding a server route,
so nothing on the Python side changed. One request no matter how many nodes are on the
canvas, and the result is shown under the button for a few seconds. The node also tries
once, quietly, when the list looks empty.

### Failures are visible on the node

When a run ended with no text, the panel just stayed blank. The status line is a single
ellipsized row, so a failure looked exactly like "nothing happened" — one real case had
three consecutive failures from an empty `workspace_dir` with nothing on screen, and the
cause only turned up by digging through the server's run history.

Now the reason is written into the panel body in red. Only failures and user stops get
promoted; `ok` does not, because a large "ok" in the body reads like the model's answer.

### Self-check page

```
http://127.0.0.1:8188/llmhub/health
```

Shows, in one screen: whether the four CLIs resolve, whether ffmpeg/cv2 are available,
whether LM Studio answers, whether the frontend JS is where ComfyUI serves it from,
and the version actually running. `[FAIL]` is required, `[ -- ]` is optional.
`?json=1` for machine-readable output.

The browser console (F12) also gets `[LLM Hub] v1.1.0 monitor extension loaded`.
**No such line means the JS never loaded at all**, and that is the only way to tell.

### Copy button on the monitor

Puts the generated text on the clipboard, so you can take it without wiring the `text`
output anywhere. Opening ComfyUI at a LAN address (`http://192.168.x.x:8188`) makes the
browser withhold `navigator.clipboard`; there is an older fallback for that case.

### GitHub Actions CI

Tests run automatically on every pull request. Linux (Python 3.10 · 3.12) and
Windows (3.12) are all required.

It paid for itself immediately. The first run caught **a real bug in the self-check**,
and revealed that the README's long-standing "3 Windows failures" was **actually 2**,
both of them problems in the tests rather than the product (see "Fixed" below).

## Changed

- **Everything the node shows you is in English**: widget names, tooltips, buttons, the monitor, `status` values, error messages, the self-check page. Source comments stay Korean
- **`stream_view=off` now removes the monitor panel.** Before, an empty panel still took up 240 px — people turn it off to make the node smaller, so leaving the biggest element in place made no sense
- **Collapsing the advanced options now shrinks the node too.** It used to stay at the expanded size
- **Results are saved with the workflow.** `OUTPUT_NODE` means the last result is still there when you reopen it. The monitor is live-only and volatile, so this side handles persistence
- **The README is bilingual** (English and Korean)
- **Three example workflows** appear under **Workflow → Browse Templates**

## Fixed

### Previously saved workflows were dying *(most serious)*

`openai_base_url` was added in the middle instead of at the end. ComfyUI stores values
by **position**, not by name, so every workflow saved before that had its values shifted
by one, and

```
error: internal node error - 'bool' object has no attribute 'strip'
```

killed the node outright. **All three shipped example workflows were in that state.**

Fixed in three layers — the widget was moved back to the end, `WIDGET_ORDER` now states
the order explicitly so a test can compare against it, and wrong-typed values no longer
crash the node. That last one is needed because **fixing the order does not fix
workflows that are already saved**.

### Everything else

- **A bug that threw away whole answers.** A correct answer to a question like "what is a 429?" was misclassified as `rate_limited` because it contained `429`. Error detection now only looks at stderr
- **`extra_args` could unlock the read-only sandbox** *(security)*. Flags like `--dangerously-*`, `--yolo` and `-s danger-full-access` are now filtered out **along with their values**. Open it with `allow_unsafe_extra_args` in `config.json` if you really need to (blocked by default)
- **Korean text turning to mojibake.** When the LM Studio SSE response has no charset, `requests` decodes it as ISO-8859-1
- **Two bugs where the monitor never appeared at all.** `WEB_DIRECTORY` was one level too deep so the JS 404'd entirely (with nothing in the log), and the panel was looked up by node id at a moment when that id was still `-1`
- **The self-check raised a false alarm.** claude/codex/gemini were marked required, so a perfectly good LM Studio-only install reported "required checks failed". It reproduced, in the backends, exactly the false alarm being avoided with ffmpeg. All backends are optional now, with a "backends detected" line summarizing *(caught by the first CI run)*
- **Monitor header buttons folding vertically.** A long tool name squeezed `Copy` into two stacked characters
- **Overwriting the user's own files.** Media copied into the working folder now goes to an isolated subfolder
- **`VALIDATE_INPUTS` was disabling validation for every input**
- **The README's gemini default model did not match the real value.** Copying it would have pinned an older model

## Verification

- **350 automated tests** (v1.0.0 had 138), **passing on both Linux and Windows.** CI runs both platforms on every pull request
- To verify without LM Studio, a fake server (including SSE) was written with the standard library
- claude real-account smoke passed (text / system prompt / file reading / image / video). During the image test the model's attempt to use Bash was **actually blocked** via `permission_denials`

## Still to confirm

- **`openai_compat` is unverified against real hardware** (no Ollama/vLLM/llama.cpp available here)
- **The lmstudio / codex / gemini real-account smokes are unverified** (they need a login). Please check them with the commands in README §8
- **CI cannot verify the frontend.** It only runs Python — the editor, the title-bar buttons, the panel hiding and the two new widgets have had syntax checking and API review only
- **Whether a given server honours `seed`** is unverified. If it does not, results simply are not reproducible; if it rejects the field, the request is retried without it
- **The `usage` shape from anything other than LM Studio** is unverified. An unrecognised shape prints no line rather than a wrong one

## Upgrading

```
cd ComfyUI/custom_nodes/ComfyUI-LLM-Hub
git pull
```

You need **both a ComfyUI restart and a hard browser refresh (`Ctrl+Shift+R`)**. Python
is only read at startup and the JS is cached by the browser — doing one without the
other leaves the new features half-visible.

If `git pull` only says "Already up to date", you are standing on a feature branch
rather than `main`. Check with `git branch --show-current`.

Nothing breaks compatibility. **Widget order is now pinned by `WIDGET_ORDER`** and
compared by a test, so the kind of accident described above cannot recur.
