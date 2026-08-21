// ComfyUI-LLM-Hub — 노드 위 실시간 모니터링 창
//
// 백엔드(utils/stream.py)가 "llmhub.stream" 이벤트로 누적 전문을 보내면
// 여기서 노드 안의 패널에 그려준다. 외부 CDN 을 쓰지 않는다(오프라인 동작).

import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";

const NODE_NAME = "LLMHubGenerate";
const EVENT_NAME = "llmhub.stream";

// version.py 와 같은 값이어야 한다(테스트로 고정). 진단할 때 제일 먼저 묻는 게
// "브라우저가 지금 몇 버전 JS 를 들고 있느냐" 인데, 캐시된 옛 파일이 남아 있으면
// 파이썬만 새 버전이고 화면은 옛날인 상태가 된다. 그때 이 줄이 답을 준다.
const VERSION = "1.1.0";

// utils/presets.py 의 PRESET_NONE 과 같은 값이어야 한다.
const PRESET_NONE = "(none)";

// nodes.py 의 AUTO_MODEL 과 같은 값이어야 한다. 목록에 이것밖에 없다는 것은
// LM Studio 조회가 실패했다는 뜻이라, 여기서는 "비었다" 의 판정 기준이 된다.
const AUTO_MODEL = "(auto)";

// 패널은 노드 객체에 직접 붙인다.
// onNodeCreated 시점에는 node.id 가 아직 -1 이라(그래프 추가 시 배정됨)
// id 를 키로 Map 에 넣어두면 이벤트의 실제 id 와 영원히 매칭되지 않는다.
const PANEL_KEY = "__llmhubMonitor";

function panelFor(nodeId) {
  const node = app.graph?.getNodeById?.(Number(nodeId));
  return node ? [node, node[PANEL_KEY]] : [null, null];
}

// --------------------------------------------------------------------------
// 아주 작은 마크다운 렌더러 (HTML 이스케이프 후 인라인 규칙만 적용)
// --------------------------------------------------------------------------

function escapeHtml(text) {
  return text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

// LLM 출력은 신뢰할 수 없는 입력이다. http/https/mailto 외의 스킴은
// 링크로 만들지 않는다(javascript: 가 ComfyUI 오리진에서 실행되는 것을 막는다).
const SAFE_SCHEME = /^(https?:|mailto:)/i;

function renderLink(_match, label, href) {
  // escapeHtml 이 이미 &amp; 등으로 바꿔놨으므로 스킴 판정만 한다.
  if (!SAFE_SCHEME.test(href.trim())) {
    return `${label} (${href})`;
  }
  const safeHref = href.replace(/"/g, "&quot;");
  return `<a href="${safeHref}" target="_blank" rel="noopener noreferrer">${label}</a>`;
}

function renderInline(text) {
  return text
    .replace(/`([^`\n]+)`/g, '<code>$1</code>')
    .replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>")
    .replace(/(^|[^*])\*([^*\n]+)\*/g, "$1<em>$2</em>")
    .replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, renderLink);
}

function renderMarkdown(source) {
  const lines = escapeHtml(source).split("\n");
  const out = [];
  let inCode = false;
  let listType = null;

  const closeList = () => {
    if (listType) {
      out.push(listType === "ul" ? "</ul>" : "</ol>");
      listType = null;
    }
  };

  for (const line of lines) {
    // 코드 펜스
    if (/^\s*```/.test(line)) {
      closeList();
      out.push(inCode ? "</code></pre>" : '<pre class="llmhub-code"><code>');
      inCode = !inCode;
      continue;
    }
    if (inCode) {
      out.push(line + "\n");
      continue;
    }

    if (!line.trim()) {
      closeList();
      continue;
    }

    const heading = line.match(/^(#{1,6})\s+(.*)$/);
    if (heading) {
      closeList();
      const level = Math.min(heading[1].length + 2, 6); // 노드 안이라 크기를 낮춘다
      out.push(`<h${level}>${renderInline(heading[2])}</h${level}>`);
      continue;
    }

    if (/^\s*>\s?/.test(line)) {
      closeList();
      out.push(`<blockquote>${renderInline(line.replace(/^\s*>\s?/, ""))}</blockquote>`);
      continue;
    }

    if (/^\s*([-*+])\s+/.test(line)) {
      if (listType !== "ul") {
        closeList();
        out.push("<ul>");
        listType = "ul";
      }
      out.push(`<li>${renderInline(line.replace(/^\s*([-*+])\s+/, ""))}</li>`);
      continue;
    }

    if (/^\s*\d+[.)]\s+/.test(line)) {
      if (listType !== "ol") {
        closeList();
        out.push("<ol>");
        listType = "ol";
      }
      out.push(`<li>${renderInline(line.replace(/^\s*\d+[.)]\s+/, ""))}</li>`);
      continue;
    }

    if (/^\s*([-*_]\s*){3,}$/.test(line)) {
      closeList();
      out.push("<hr>");
      continue;
    }

    closeList();
    out.push(`<p>${renderInline(line)}</p>`);
  }

  closeList();
  if (inCode) out.push("</code></pre>");
  return out.join("");
}

// --------------------------------------------------------------------------
// 클립보드
// --------------------------------------------------------------------------
// navigator.clipboard 는 "보안 컨텍스트" 에서만 존재한다. localhost 는 예외라
// 있지만, ComfyUI 를 http://192.168.x.x:8188 처럼 LAN 주소로 열면 아예 없다.
// 그래서 구식 execCommand 폴백을 남겨둔다.
async function copyToClipboard(text) {
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch (error) {
    // 권한 거부 등 — 아래 폴백으로 내려간다
  }
  try {
    const area = document.createElement("textarea");
    area.value = text;
    // 화면 밖으로 밀면 iOS 가 스크롤을 튕긴다. 제자리에 두고 투명하게 만든다.
    area.style.position = "fixed";
    area.style.top = "0";
    area.style.opacity = "0";
    document.body.appendChild(area);
    area.select();
    const ok = document.execCommand("copy");
    document.body.removeChild(area);
    return ok;
  } catch (error) {
    return false;
  }
}

// --------------------------------------------------------------------------
// 패널 생성
// --------------------------------------------------------------------------

// 모니터 창 높이. stream_view=off 로 숨길 때 되돌릴 값이라 모듈 범위에 둔다.
const PANEL_HEIGHT = 240;

// 결과 없이 끝났을 때 본문에 대신 적을 문장.
//
// 아무 상태나 옮기지는 않는다 -- "ok" 를 본문에 크게 써두면 그게 답처럼 보인다.
// 실패와 사용자 중단만 옮긴다. 그 둘이야말로 빈 화면과 구별되어야 하는 것들이다.
function noticeFor(status, done) {
  if (!done) return "";
  const text = String(status || "").trim();
  return /^(error|stopped)\b/i.test(text) ? text : "";
}

function createPanel(node) {
  const root = document.createElement("div");
  root.className = "llmhub-monitor";
  root.innerHTML = `
    <div class="llmhub-head">
      <span class="llmhub-status">Idle</span>
      <span class="llmhub-meta"></span>
      <button class="llmhub-copy" type="button" title="Copy the generated text to the clipboard">Copy</button>
      <button class="llmhub-stop" type="button" title="Stop generation on this node">■ Stop</button>
    </div>
    <div class="llmhub-body"></div>
  `;

  const statusEl = root.querySelector(".llmhub-status");
  const metaEl = root.querySelector(".llmhub-meta");
  const bodyEl = root.querySelector(".llmhub-body");
  const stopEl = root.querySelector(".llmhub-stop");
  const copyEl = root.querySelector(".llmhub-copy");

  // 캔버스가 이 클릭을 노드 드래그로 삼키지 않게 한다.
  for (const name of ["pointerdown", "mousedown", "click"]) {
    stopEl.addEventListener(name, (event) => event.stopPropagation());
    copyEl.addEventListener(name, (event) => event.stopPropagation());
  }

  let copyTimer = null;
  copyEl.addEventListener("click", async () => {
    const text = control.lastText || "";
    // 빈 값을 쓰면 클립보드에 들어 있던 것이 조용히 지워진다.
    let label;
    if (!text) label = "Nothing to copy";
    else label = (await copyToClipboard(text)) ? "Copied" : "Copy failed";

    copyEl.textContent = label;
    clearTimeout(copyTimer);
    copyTimer = setTimeout(() => {
      copyEl.textContent = "Copy";
    }, 1200);
  });
  stopEl.addEventListener("click", async () => {
    stopEl.disabled = true;
    stopEl.textContent = "Stopping…";
    try {
      await api.fetchApi("/llmhub/stop", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ node: String(node.id) }),
      });
    } catch (error) {
      // 중지 요청이 실패해도 패널이 멈춘 것처럼 보이면 안 된다.
      statusEl.textContent = "Stop request failed";
      stopEl.disabled = false;
      stopEl.textContent = "■ Stop";
    }
  });

  let stick = true; // 사용자가 위로 스크롤하면 자동 스크롤을 멈춘다
  bodyEl.addEventListener("scroll", () => {
    stick = bodyEl.scrollHeight - bodyEl.scrollTop - bodyEl.clientHeight < 24;
  });

  const control = {
    root,
    lastText: "",
    // lastText 가 결과가 아니라 실패 사유일 때 true. 색을 다르게 칠하려고 둔다 --
    // 붉은 글씨가 아니면 사용자는 이걸 모델이 낸 답으로 읽는다.
    lastIsNotice: false,
    render(text, mode) {
      bodyEl.classList.remove("llmhub-thinking");
      bodyEl.classList.toggle("llmhub-notice", !!this.lastIsNotice);
      if (mode === "markdown") {
        bodyEl.innerHTML = renderMarkdown(text || "");
      } else {
        bodyEl.textContent = text || "";
      }
      if (stick) bodyEl.scrollTop = bodyEl.scrollHeight;
    },
    renderThinking(text) {
      // 사고 과정은 항상 원문 그대로 흐리게. 마크다운으로 렌더하면 답처럼 보여서
      // 어느 쪽이 최종 결과인지 헷갈린다.
      bodyEl.classList.add("llmhub-thinking");
      bodyEl.classList.remove("llmhub-notice");
      bodyEl.textContent = text || "";
      if (stick) bodyEl.scrollTop = bodyEl.scrollHeight;
    },
    setStatus(status, elapsed, done) {
      statusEl.textContent = status || (done ? "Done" : "Generating…");
      // 한 줄로 잘리므로, 잘린 내용은 마우스를 올려 볼 수 있게 남긴다.
      // (도구 사용 줄은 파일 경로가 길어서 거의 항상 잘린다.)
      statusEl.title = statusEl.textContent;
      statusEl.classList.toggle("llmhub-running", !done);
      metaEl.textContent = elapsed != null ? `${elapsed}s` : "";
      // 돌고 있을 때만 보인다. 멈출 것이 없을 때 눌러봐야 아무 일도 안 일어나는
      // 버튼이 남아 있으면 그게 고장처럼 보인다.
      this.setRunning(!done);
    },
    setRunning(running) {
      stopEl.style.display = running ? "" : "none";
      if (running) {
        stopEl.disabled = false;
        stopEl.textContent = "■ Stop";
      }
    },
    clear() {
      this.lastText = "";
      this.lastIsNotice = false;
      bodyEl.textContent = "";
      bodyEl.classList.remove("llmhub-thinking");
      bodyEl.classList.remove("llmhub-notice");
      stick = true;
    },
  };

  // 위젯으로 붙여 노드 크기와 함께 움직이게 한다.
  const widget = node.addDOMWidget("llmhub_monitor", "div", root, {
    serialize: false,           // 워크플로우 json 에 내용이 저장되지 않게
    hideOnZoom: false,
    getValue: () => "",
    setValue: () => {},
  });
  // options.serialize 만으로는 부족하다. litegraph 의 serialize()/configure() 는
  // widget.serialize 를 본다(ComfyUI 자체 코드도 r.serialize=!1 을 따로 찍는다).
  widget.serialize = false;
  // 접힌 상태에서는 이 창이 노드의 주인공이다.
  //
  // 이 프론트엔드는 DOM 위젯 높이를 computeLayoutSize 로 잡는다(코어의 video DOM
  // 위젯도 `computeLayoutSize = () => ({minHeight, minWidth})` 를 쓴다). computeSize
  // 만 주면 무시돼서 패널이 엉뚱한 높이로 잡히고 위 위젯을 덮는다.
  // 옛 프론트엔드는 computeSize 를 보므로 둘 다 둔다.
  widget.computeSize = (width) => [width, PANEL_HEIGHT];
  widget.computeLayoutSize = () => ({ minHeight: PANEL_HEIGHT, minWidth: 200 });

  // 이 위젯은 반드시 widgets 배열의 맨 끝에 있어야 한다.
  //   serialize():  widgets_values[전체배열 인덱스] = 값   (건너뛴 자리에 구멍)
  //   configure():  구멍을 무시하고 순서대로 읽는다        (압축해서 읽음)
  // 둘이 어긋나 있어서, 직렬화되지 않는 위젯이 중간에 끼면 그 뒤 위젯 값이 전부
  // 한 칸씩 밀린다. 맨 끝일 때만 구멍이 배열 끝이라 무해하다.
  // → 모니터를 위로 올리고 싶으면 옮기지 말고 사이 위젯을 숨겨라.
  control.widget = widget;
  // 아직 아무것도 안 돌고 있다. 멈출 게 없을 때 버튼이 보이면 고장처럼 보인다.
  control.setRunning(false);

  return control;
}

function viewMode(node) {
  const widget = node.widgets?.find((w) => w.name === "stream_view");
  return widget ? widget.value : "plain";
}

// stream_view 를 off 로 두면 스트리밍 자체를 하지 않는다. 그런데 지금까지는
// 빈 패널이 240px 를 그대로 차지하고 있었다 -- 끄는 이유가 보통 "노드를 작게
// 쓰려고" 인데 정작 제일 큰 것이 안 없어지니 앞뒤가 안 맞는다.
//
// DOM 위젯이라 숨기는 방법이 위젯들과 다르다. 어느 프론트엔드가 무엇을 보는지
// 확인할 수 없어 셋 다 건다: 실제 요소, 그리고 두 가지 크기 계산 API.
function applyMonitorVisibility(node, mode) {
  const widget = node[PANEL_KEY]?.widget;
  if (!widget) return;

  // mode 를 넘기는 쪽은 위젯 callback 이다. 위젯이 값을 먼저 쓰고 callback 을
  // 부르는 게 관용이지만, 그 순서에 기대지 않으려고 값을 직접 받는다.
  const hidden = (mode ?? viewMode(node)) === "off";
  if (widget.element) widget.element.style.display = hidden ? "none" : "";
  widget.hidden = hidden;
  if (hidden) {
    widget.computeSize = () => [0, -4];
    widget.computeLayoutSize = () => ({ minHeight: 0, minWidth: 0 });
  } else {
    widget.computeSize = (width) => [width, PANEL_HEIGHT];
    widget.computeLayoutSize = () => ({ minHeight: PANEL_HEIGHT, minWidth: 200 });
  }
}

// backend 값에 따라 그 백엔드가 실제로 쓰는 위젯만 보인다.
// (ComfyUI 위젯 숨김 관용구: type 을 바꾸고 computeSize 를 0 으로)
//
// 어느 위젯이 어느 백엔드에 유효한지는 추측이 아니라 nodes.py 의 tooltip 이 근거다:
//   temperature / max_tokens  "lmstudio 에만 적용, CLI 3종은 무시"
//   mcp_config                "claude 만 실제 적용"
//   video_max_frames          "claude/codex/lmstudio 만 해당(gemini 는 영상을 그대로 넘김)"
//
// 숨겨도 값은 그대로 직렬화된다(litegraph 는 위젯을 저장할 때 type 을 보지 않는다).
// 그래서 required 위젯을 숨겨도 프롬프트에서 빠지지 않는다.
// ollama / vllm / llamacpp 는 openai_compat 과 같은 구현이라 위젯이 보이는
// 조건도 같다. 상수로 빼서 펼치고 싶지만 일부러 이름을 다 적는다 --
// tests/test_widget_visibility.py 가 이 리터럴을 **텍스트로 파싱**해서
// nodes.py 의 백엔드 목록과 대조하기 때문에, 스프레드를 쓰면 파서가 아무것도
// 못 읽고 검사가 조용히 통과해버린다.
const BACKEND_ONLY = {
  openai_base_url: ["openai_compat", "ollama", "vllm", "llamacpp"],
  claude_model: ["claude"],
  lmstudio_model: ["lmstudio"],
  lmstudio_ttl_sec: ["lmstudio"],
  lmstudio_unload_after: ["lmstudio"],
  temperature: ["lmstudio", "openai_compat", "ollama", "vllm", "llamacpp"],
  max_tokens: ["lmstudio", "openai_compat", "ollama", "vllm", "llamacpp"],
  mcp_config: ["claude"],
  video_max_frames: ["lmstudio", "claude", "codex", "openai_compat", "ollama", "vllm", "llamacpp"],
  // extra_body 는 HTTP payload 에 합치는 물건이라 CLI 3종에는 합칠 자리가 없다.
  extra_body: ["lmstudio", "openai_compat", "ollama", "vllm", "llamacpp"],
};

// 접었을 때 숨는 위젯. 여기 없는 것 = 항상 보이는 것이다:
//   backend / prompt / system_prompt / lmstudio_model
// 모니터 창을 system_prompt 바로 밑으로 끌어올리는 방법이 이것뿐이다 —
// DOM 위젯 자체는 위로 옮길 수 없다(createPanel 의 주석 참고).
const ADVANCED = [
  "model", "file_access", "workspace_dir", "temperature", "max_tokens",
  "timeout_sec", "seed", "video_max_frames", "stream_view", "video_path",
  "mcp_config", "extra_args", "lmstudio_ttl_sec", "lmstudio_unload_after",
  "batch_mode", "extra_body",
  // INPUT_TYPES 에 없는 이름이다. seed 에 control_after_generate:True 를 주면
  // 프론트엔드가 짝꿍 위젯을 하나 더 만들어 붙인다. seed 만 숨기면 이게 홀로 남아
  // "고급을 접었는데 웬 randomize 줄이 남아 있는" 모양이 된다.
  "control_after_generate",
];

// 백엔드에 따라서만 "고급" 이 되는 위젯.
//
// openai_base_url 은 openai_compat 에서는 항상 보여야 한다 -- 미리 잡아둔 주소가
// 없어서 사용자가 직접 넣거나 config.json 에 적어야 하기 때문이다.
// 반대로 ollama / vllm / llamacpp 는 고른 순간 표준 포트가 이미 잡힌다. 그런데도
// 빈 주소 칸이 눈에 잘 띄는 자리(모델 드롭다운이 있던 그 자리)에 남아 있으면
// "여기를 채워야 도는구나" 로 읽힌다 -- 안 채워도 도는데.
// 그래서 이 셋에서는 접어두고, 표준 포트가 아닌 곳에 띄운 사람만 펼쳐서 쓴다.
const ADVANCED_FOR = {
  openai_base_url: ["ollama", "vllm", "llamacpp"],
};

const SHOW_ADVANCED_PROP = "showAdvanced";

// 위젯을 숨기거나 되살린 뒤 노드 높이를 그만큼 조정한다.
//
// setDirtyCanvas 는 다시 그리기만 한다. node.size 는 저장된 값이라 위젯이
// 사라져도 저절로 줄지 않는다 -- 그래서 고급 옵션을 펼쳤다 접으면 칸이 펼친
// 크기 그대로 남는다.
//
// node.setSize(node.computeSize()) 로 끝내지 않는 이유: 그러면 사용자가 손으로
// 늘려둔 높이(모니터 창을 크게 쓰는 경우)까지 매번 최소 크기로 깎아버린다.
// 대신 "최소 높이가 얼마나 변했는지"만 재서 그 차이만큼 더하고 뺀다.
const LAST_MIN_KEY = "_llmhubLastMin";

function resizeToWidgets(node) {
  let min;
  try {
    min = node.computeSize?.()?.[1];
  } catch (e) {
    return;
  }
  if (typeof min !== "number" || !isFinite(min)) return;

  const previous = node[LAST_MIN_KEY];
  node[LAST_MIN_KEY] = min;

  // 첫 호출과 워크플로우 로드 직후에는 조정하지 않는다.
  // 저장된 크기는 이미 그때 상태에 맞는 값이라 여기서 또 빼면 너무 작아진다.
  if (previous === undefined) return;

  const delta = min - previous;
  if (delta === 0) return;

  const height = Math.max((node.size?.[1] ?? min) + delta, min);
  node.setSize?.([node.size?.[0] ?? node.computeSize()[0], height]);
}

function setupBackendToggle(node) {
  const backendWidget = node.widgets?.find((w) => w.name === "backend");
  if (!backendWidget) return;

  // properties 는 이름으로 저장되므로 위젯 순서에 영향을 주지 않는다.
  if (node.properties[SHOW_ADVANCED_PROP] === undefined) {
    node.properties[SHOW_ADVANCED_PROP] = false;
  }

  const apply = () => {
    const backend = backendWidget.value;
    const showAdvanced = !!node.properties[SHOW_ADVANCED_PROP];

    for (const w of node.widgets || []) {
      if (w.name === "llmhub_monitor" || w.name === "backend") continue;
      const backends = BACKEND_ONLY[w.name];
      // 이 백엔드가 안 쓰는 위젯은 펼쳐도 안 보인다 — 펼침은 "고급"만 여는 것이지
      // 무의미한 위젯까지 되살리는 게 아니다.
      const usedByBackend = !backends || backends.includes(backend);
      // 항상 고급인 것 + 이 백엔드에서만 고급인 것
      const isAdvanced =
        ADVANCED.includes(w.name) ||
        (ADVANCED_FOR[w.name] || []).includes(backend);
      const visible = usedByBackend && (showAdvanced || !isAdvanced);

      if (w._llmhubType === undefined) w._llmhubType = w.type;
      if (visible) {
        w.type = w._llmhubType;
        w.hidden = false;
        w.computeSize = undefined;
      } else {
        // type 을 바꾸는 건 예전 관용구고, 지금 프론트엔드는 w.hidden 을 본다.
        // 어느 쪽을 보는 버전인지 확인할 방법이 없어 둘 다 건다 — 한쪽만 걸면
        // 위젯 종류에 따라 일부만 숨는 얼룩덜룩한 상태가 된다.
        w.type = "hidden";
        w.hidden = true;
        w.computeSize = () => [0, -4];
      }
    }

    applyMonitorVisibility(node);
    resizeToWidgets(node);
    node.setDirtyCanvas?.(true, true);
  };

  const previous = backendWidget.callback;
  backendWidget.callback = function () {
    const r = previous?.apply(this, arguments);
    apply();
    return r;
  };
  // 저장된 워크플로우를 열면 configure() 가 backend 값을 나중에 되돌려놓는데,
  // 그때는 위젯 callback 이 불리지 않는다. onConfigure 에서 다시 부르지 않으면
  // backend=claude 로 저장한 노드가 lmstudio 위젯을 펼친 채 열린다.
  node._llmhubApplyBackendToggle = apply;
  apply();
}

function toggleAdvanced(node) {
  node.properties[SHOW_ADVANCED_PROP] = !node.properties?.[SHOW_ADVANCED_PROP];
  node._llmhubApplyBackendToggle?.();
}

// --------------------------------------------------------------------------
// 시스템 프롬프트 편집창
// --------------------------------------------------------------------------
// 노드 안의 입력칸은 너무 작아서 긴 프롬프트를 붙여넣으면 전체가 안 보인다.
// 큰 창을 띄워 거기서 쓰고, 이름을 붙여 저장하고, 저장해둔 것을 불러온다.
//
// 저장은 서버(/llmhub/presets)가 한다. 브라우저 로컬에 두면 다른 기기나
// 다른 브라우저에서 열었을 때 프리셋이 통째로 사라진다.

function presetSelectOptions(select, presets, keep) {
  select.innerHTML = "";
  const names = Object.keys(presets);
  const blank = document.createElement("option");
  blank.value = "";
  blank.textContent = names.length ? "— select a preset —" : "— no presets saved —";
  select.appendChild(blank);
  for (const name of names) {
    const option = document.createElement("option");
    option.value = name;
    option.textContent = name;
    select.appendChild(option);
  }
  select.value = names.includes(keep) ? keep : "";
}

function openPromptEditor(node) {
  const promptWidget = node.widgets?.find((w) => w.name === "system_prompt");
  if (!promptWidget) return;
  const presetWidget = node.widgets?.find((w) => w.name === "system_preset");

  let presets = {};

  const overlay = document.createElement("div");
  overlay.className = "llmhub-overlay";
  overlay.innerHTML = `
    <div class="llmhub-dialog">
      <div class="llmhub-dialog-head">
        <strong>System prompt</strong>
        <button class="llmhub-x" type="button" title="Close without applying">✕</button>
      </div>
      <div class="llmhub-dialog-bar">
        <select class="llmhub-preset-list"></select>
        <button class="llmhub-load" type="button">Load</button>
        <button class="llmhub-save" type="button">Save as…</button>
        <button class="llmhub-del" type="button">Delete</button>
        <span class="llmhub-dialog-msg"></span>
      </div>
      <textarea class="llmhub-editor" spellcheck="false"
                placeholder="Write or paste the system prompt here."></textarea>
      <div class="llmhub-dialog-foot">
        <span class="llmhub-hint">Ctrl+Enter to apply · Esc to cancel</span>
        <button class="llmhub-cancel" type="button">Cancel</button>
        <button class="llmhub-apply" type="button">Apply</button>
      </div>
    </div>
  `;

  const dialog = overlay.querySelector(".llmhub-dialog");
  const editor = overlay.querySelector(".llmhub-editor");
  const list = overlay.querySelector(".llmhub-preset-list");
  const msg = overlay.querySelector(".llmhub-dialog-msg");

  editor.value = promptWidget.value || "";

  const say = (text, bad) => {
    msg.textContent = text || "";
    msg.classList.toggle("llmhub-bad", !!bad);
  };

  const close = () => {
    document.removeEventListener("keydown", onKey, true);
    overlay.remove();
  };

  const apply = () => {
    promptWidget.value = editor.value;
    // 위젯 값을 직접 바꾸면 callback 이 안 불린다. 저장/캐시 무효화가 필요한
    // 위젯은 아니지만, 화면은 다시 그려야 새 내용이 보인다.
    promptWidget.callback?.(promptWidget.value);
    node.setDirtyCanvas?.(true, true);
    close();
  };

  function onKey(event) {
    if (event.key === "Escape") {
      event.stopPropagation();
      close();
    } else if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
      event.stopPropagation();
      apply();
    }
  }

  // ComfyUI 는 캔버스에서 Delete/Space 같은 키를 단축키로 먹는다. 편집창 안에서
  // 타이핑한 것이 노드 삭제로 이어지면 안 되므로 여기서 끊는다.
  for (const name of ["keydown", "keyup", "keypress", "pointerdown", "wheel"]) {
    dialog.addEventListener(name, (event) => event.stopPropagation());
  }
  document.addEventListener("keydown", onKey, true);
  overlay.addEventListener("pointerdown", (event) => {
    if (event.target === overlay) close(); // 바깥을 누르면 닫는다(적용은 안 한다)
  });

  overlay.querySelector(".llmhub-x").addEventListener("click", close);
  overlay.querySelector(".llmhub-cancel").addEventListener("click", close);
  overlay.querySelector(".llmhub-apply").addEventListener("click", apply);

  overlay.querySelector(".llmhub-load").addEventListener("click", () => {
    const name = list.value;
    if (!name) return say("Pick a preset to load.", true);
    editor.value = presets[name] ?? "";
    if (presetWidget) presetWidget.value = name;
    say(`Loaded '${name}'.`);
  });
  // 목록에서 고르는 것만으로도 불러온다. 두 번 누르게 할 이유가 없다.
  list.addEventListener("change", () => {
    if (list.value) overlay.querySelector(".llmhub-load").click();
  });

  overlay.querySelector(".llmhub-save").addEventListener("click", async () => {
    const suggested = list.value || "";
    const name = window.prompt("Save this system prompt as:", suggested);
    if (name === null) return; // 취소
    say("Saving…");
    try {
      const response = await api.fetchApi("/llmhub/presets", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name, prompt: editor.value }),
      });
      const data = await response.json();
      if (!response.ok || !data.ok) return say(data.error || "Could not save.", true);
      presets = data.presets || {};
      presetSelectOptions(list, presets, name.trim());
      refreshPresetWidget(node, presets, name.trim());
      say(`Saved '${name.trim()}'.`);
    } catch (error) {
      say(`Could not save: ${error}`, true);
    }
  });

  overlay.querySelector(".llmhub-del").addEventListener("click", async () => {
    const name = list.value;
    if (!name) return say("Pick a preset to delete.", true);
    if (!window.confirm(`Delete the preset '${name}'?`)) return;
    try {
      const response = await api.fetchApi("/llmhub/presets/delete", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name }),
      });
      const data = await response.json();
      if (!response.ok || !data.ok) return say(data.error || "Could not delete.", true);
      presets = data.presets || {};
      presetSelectOptions(list, presets, "");
      refreshPresetWidget(node, presets, PRESET_NONE);
      say(`Deleted '${name}'.`);
    } catch (error) {
      say(`Could not delete: ${error}`, true);
    }
  });

  document.body.appendChild(overlay);
  editor.focus();

  // 목록은 열릴 때 서버에서 받아온다. 노드가 만들어진 뒤에 저장한 프리셋도
  // 브라우저를 새로고침하지 않고 바로 보이게 하려는 것이다.
  api.fetchApi("/llmhub/presets")
    .then((response) => response.json())
    .then((data) => {
      presets = data.presets || {};
      presetSelectOptions(list, presets, presetWidget?.value);
    })
    .catch(() => say("Could not load the preset list.", true));
}

// 드롭다운에서 프리셋을 고르면 system_prompt 칸을 그 내용으로 바꾼다.
// 편집창을 열지 않고 빠르게 갈아끼우는 용도다.
function setupPresetLoader(node) {
  const widget = node.widgets?.find((w) => w.name === "system_preset");
  const promptWidget = node.widgets?.find((w) => w.name === "system_prompt");
  if (!widget || !promptWidget) return;

  const previous = widget.callback;
  widget.callback = function (value) {
    const result = previous?.apply(this, arguments);
    if (!value || value === PRESET_NONE) return result;
    // 본문은 서버에만 있다. 고를 때마다 받아오는 대신 캐시할 수도 있지만,
    // 다른 창에서 프리셋을 고쳤을 때 낡은 내용을 넣는 것이 더 나쁘다.
    api.fetchApi("/llmhub/presets")
      .then((response) => response.json())
      .then((data) => {
        const text = (data.presets || {})[value];
        if (typeof text !== "string") return;
        promptWidget.value = text;
        promptWidget.callback?.(text);
        node.setDirtyCanvas?.(true, true);
      })
      .catch(() => {});
    return result;
  };
}

// 노드의 드롭다운 위젯 목록을 서버 목록과 맞춘다. 이게 없으면 편집창에서 저장한
// 프리셋이 드롭다운에는 새로고침 전까지 안 보인다.
function refreshPresetWidget(node, presets, select) {
  const widget = node.widgets?.find((w) => w.name === "system_preset");
  if (!widget) return;
  const names = [PRESET_NONE, ...Object.keys(presets)];
  if (widget.options) widget.options.values = names;
  widget.value = names.includes(select) ? select : PRESET_NONE;
  node.setDirtyCanvas?.(true, true);
}

// --------------------------------------------------------------------------
// LM Studio 모델 목록 재조회
// --------------------------------------------------------------------------
// lmstudio_model 드롭다운은 파이썬의 INPUT_TYPES 가 LM Studio 를 조회해서 만든다.
// 그래서 ComfyUI 가 LM Studio 보다 먼저 뜨면 목록이 "(auto)" 하나로 굳고,
// 페이지를 새로 열기 전까지 그대로 남는다. 실제로 겪은 증상이고 로그에도 남는다:
//   [LLM Hub] Could not fetch the LM Studio model list (... ConnectTimeout).
// 그때 사용자에게 보이는 건 "드롭다운이 안 열린다" 뿐이라 원인을 알 길이 없다.
//
// 서버에 새 라우트를 만들지 않는다. ComfyUI 코어의 /object_info 가 요청마다
// INPUT_TYPES 를 다시 실행하므로, 그것만 다시 받아오면 목록이 갱신된다.
// 덕분에 이 기능은 파이썬을 건드리지 않고 -- 즉 ComfyUI 재시작 없이 -- 끝난다.

const MODEL_WIDGET = "lmstudio_model";
const CONNECT_KEY = "_llmhubConnectLabel";
const CONNECT_TIMER = "_llmhubConnectTimer";
const CHECKING = "Checking LM Studio…";

// 조회는 페이지 전체에서 한 번에 하나만 나간다. 캔버스에 노드가 5개 있다고
// 5번 물어볼 이유가 없고, LM Studio 가 꺼져 있으면 한 번에 3초씩 걸린다.
let modelFetchInFlight = null;

function fetchModelList() {
  if (modelFetchInFlight) return modelFetchInFlight;
  modelFetchInFlight = api
    .fetchApi(`/object_info/${NODE_NAME}`)
    .then((response) => response.json())
    .then((data) => {
      const input = data?.[NODE_NAME]?.input || {};
      const spec =
        (input.optional || {})[MODEL_WIDGET] || (input.required || {})[MODEL_WIDGET];
      const values = spec?.[0];
      if (!Array.isArray(values)) throw new Error("object_info has no model list");
      return values;
    })
    .finally(() => {
      modelFetchInFlight = null;
    });
  return modelFetchInFlight;
}

// 목록은 전역이다. 누른 노드만 고치면 나머지 노드는 낡은 채로 남아서
// "어떤 노드는 되고 어떤 노드는 안 되는" 상태가 된다.
function applyModelList(values) {
  for (const node of app.graph?._nodes || []) {
    if (node.type !== NODE_NAME && node.comfyClass !== NODE_NAME) continue;
    const widget = node.widgets?.find((w) => w.name === MODEL_WIDGET);
    if (!widget?.options) continue;
    widget.options.values = values;
    // 고른 값은 건드리지 않는다. 목록에 없는 이름이어도 파이썬의 VALIDATE_INPUTS
    // 가 통과시키므로 실행에는 지장이 없고, 여기서 (auto) 로 되돌리면 사용자가
    // 골라둔 모델이 조용히 바뀐다 -- 그게 목록이 비는 것보다 나쁘다.
    node.setDirtyCanvas?.(true, true);
  }
  return values;
}

// 버튼 라벨을 잠깐 결과로 바꿨다가 되돌린다. 별도 알림 UI 를 만들지 않는 이유는
// 사용자가 방금 누른 그 자리에 답이 나오는 게 가장 짧은 경로이기 때문이다.
function setConnectLabel(node, text, holdMs) {
  node[CONNECT_KEY] = text;
  node.setDirtyCanvas?.(true, true);
  if (node[CONNECT_TIMER]) clearTimeout(node[CONNECT_TIMER]);
  node[CONNECT_TIMER] = holdMs
    ? setTimeout(() => {
        node[CONNECT_KEY] = null;
        node[CONNECT_TIMER] = null;
        node.setDirtyCanvas?.(true, true);
      }, holdMs)
    : null;
}

function refreshModels(node) {
  if (node[CONNECT_KEY] === CHECKING) return; // 연타 무시
  setConnectLabel(node, CHECKING, 0);
  fetchModelList()
    .then(applyModelList)
    .then((values) => {
      const found = values.filter((v) => v !== AUTO_MODEL).length;
      if (found) {
        setConnectLabel(node, `Found ${found} model${found > 1 ? "s" : ""}`, 3000);
        return;
      }
      // LM Studio 가 꺼져 있어도 /object_info 는 200 을 준다 -- 목록만 비어서
      // 온다. 그래서 "응답 없음" 은 예외가 아니라 여기서 판별해야 한다.
      // 파이썬의 list_model_ids 캐시가 10초라 즉시 다시 눌러도 같은 답이 온다.
      setConnectLabel(node, "No models — is the LM Studio server on? (retry in 10s)", 4000);
    })
    .catch(() => setConnectLabel(node, "Could not reach ComfyUI", 4000));
}

// 목록이 "(auto)" 하나뿐이면 ComfyUI 가 LM Studio 보다 먼저 뜬 것이다.
// 페이지당 딱 한 번 조용히 다시 받아온다. 실패해도 재시도하지 않는다 --
// LM Studio 를 아예 안 쓰는 사람이 페이지를 열 때마다 멎으면 안 된다.
let autoRefreshTried = false;

function maybeAutoRefresh(node) {
  if (autoRefreshTried) return;
  const values = node.widgets?.find((w) => w.name === MODEL_WIDGET)?.options?.values;
  if (!Array.isArray(values) || values.length > 1) return;
  autoRefreshTried = true;
  fetchModelList()
    .then(applyModelList)
    .catch(() => {});
}

function isLmStudio(node) {
  return node.widgets?.find((w) => w.name === "backend")?.value === "lmstudio";
}

// --------------------------------------------------------------------------
// 고급 옵션 버튼 (타이틀 바 오른쪽)
// --------------------------------------------------------------------------
// 우클릭 메뉴는 있는 줄도 모른다. 그래서 눈에 보이는 버튼을 하나 그린다.
//
// addWidget 으로 만들지 않는 이유: 위젯은 widgets_values 배열에 자리를 차지한다.
// 중간에 하나 끼면 이 노드로 저장해둔 예전 워크플로우의 값이 전부 한 칸씩 밀린다.
// 캔버스에 직접 그리면 저장 데이터를 아예 건드리지 않는다.
//
// 우클릭 메뉴는 그대로 남겨둔다. 프론트엔드 버전에 따라 onMouseDown 이 안 불릴
// 가능성이 있는데, 그때 조작 수단이 통째로 사라지면 안 되기 때문이다.
//
// 폭은 라벨에 맞춰 직접 잡는다. 캔버스에 그리는 것이라 CSS 처럼 내용에 맞춰
// 늘어나지 않는다 -- 좁게 잡으면 글자가 버튼 밖으로 삐져나온다.
// 아이콘만 그린다. 글자 라벨("▼ Advanced" + "✎ System prompt")을 쓰면 둘이
// 190px 을 먹어서 노드 제목을 덮어버렸다. 아이콘만 쓰면 46px 이면 된다.
//
// 대신 무슨 버튼인지 알 수 없어지므로, 마우스를 올리면 아래에 설명을 그린다.
// 캔버스에 그리는 버튼이라 HTML 의 title= 툴팁을 쓸 수 없다.
const BUTTON_WIDTH = 20;
const BUTTON_HEIGHT = 18;
const BUTTON_MARGIN = 6;
const BUTTON_GAP = 4;
const HOVER_KEY = "_llmhubBtnHover";

// 오른쪽 끝부터 이 순서로 놓인다(= 배열의 앞이 화면의 오른쪽).
//
// visible 을 안 쓰면 항상 그린다. Connect 만 이걸 쓰는데, 하는 일이 LM Studio
// 조회 하나뿐이라 다른 백엔드에서는 눌러도 의미가 없기 때문이다.
const TITLE_BUTTONS = [
  {
    key: "advanced",
    icon: (node) => (node.properties?.[SHOW_ADVANCED_PROP] ? "▴" : "▾"),
    hint: (node) =>
      node.properties?.[SHOW_ADVANCED_PROP]
        ? "Hide advanced options"
        : "Show advanced options",
    active: (node) => !!node.properties?.[SHOW_ADVANCED_PROP],
    onClick: (node) => toggleAdvanced(node),
  },
  {
    key: "prompt",
    icon: () => "✎",
    hint: () => "Edit the system prompt",
    active: () => false,
    onClick: (node) => openPromptEditor(node),
  },
  {
    key: "connect",
    visible: isLmStudio,
    icon: () => "⟳",
    hint: () => "Refresh LM Studio models",
    // 누른 직후의 결과를 호버 없이도 잠깐 띄운다. 아이콘 버튼이라 라벨을
    // 바꿔서 알릴 자리가 없다 -- 대신 호버 설명 자리를 빌려 쓴다.
    notice: (node) => node[CONNECT_KEY],
    active: (node) => node[CONNECT_KEY] === CHECKING,
    onClick: (node) => refreshModels(node),
  },
];

function buttonRects(node) {
  const titleHeight = window.LiteGraph?.NODE_TITLE_HEIGHT ?? 30;
  const y = -titleHeight + (titleHeight - BUTTON_HEIGHT) / 2;
  let right = (node.size?.[0] ?? 0) - BUTTON_MARGIN;
  // 숨긴 버튼은 자리도 차지하지 않는다. 그리기(drawTitleButtons)와 클릭 판정
  // (hitButton)이 둘 다 이 함수를 쓰므로, 여기서 한 번 걸러내면 "안 보이는데
  // 눌리는" 어긋남이 생길 수 없다.
  return TITLE_BUTTONS.filter((spec) => spec.visible?.(node) !== false).map((spec) => {
    const rect = { spec, x: right - BUTTON_WIDTH, y, w: BUTTON_WIDTH, h: BUTTON_HEIGHT };
    right -= BUTTON_WIDTH + BUTTON_GAP;
    return rect;
  });
}

function hitButton(node, pos) {
  if (node.flags?.collapsed) return null;
  for (const r of buttonRects(node)) {
    if (pos[0] >= r.x && pos[0] <= r.x + r.w && pos[1] >= r.y && pos[1] <= r.y + r.h) {
      return r;
    }
  }
  return null;
}

function drawTitleButtons(node, ctx) {
  if (node.flags?.collapsed) return;
  const hovered = node[HOVER_KEY];
  let tooltip = null;

  ctx.save();
  ctx.font = "12px sans-serif";
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  for (const r of buttonRects(node)) {
    const hover = hovered === r.spec.key;
    // notice 는 방금 누른 결과라 호버보다 우선한다.
    const notice = r.spec.notice?.(node);
    if (notice) tooltip = { rect: r, text: notice };
    else if (hover && !tooltip) tooltip = { rect: r, text: r.spec.hint(node) };
    ctx.beginPath();
    // roundRect 는 비교적 최근 API 라 없을 수 있다. 없으면 각진 사각형으로 떨어진다.
    if (ctx.roundRect) ctx.roundRect(r.x, r.y, r.w, r.h, 4);
    else ctx.rect(r.x, r.y, r.w, r.h);
    ctx.fillStyle = hover ? "#4b5563" : r.spec.active(node) ? "#3b4351" : "#2c3038";
    ctx.fill();
    ctx.strokeStyle = hover ? "#8ab4f8" : "#5a6270";
    ctx.lineWidth = 1;
    ctx.stroke();

    ctx.fillStyle = "#e8e8e8";
    ctx.fillText(r.spec.icon(node), r.x + r.w / 2, r.y + r.h / 2);
  }
  if (tooltip) drawButtonHint(ctx, tooltip.rect, tooltip.text);
  ctx.restore();
}

// 아이콘만으로는 무슨 버튼인지 알 수 없다. 마우스를 올린 동안 본문 위쪽에
// 설명을 그린다 -- 캔버스라 HTML 툴팁을 못 쓴다.
function drawButtonHint(ctx, rect, text) {
  ctx.font = "11px sans-serif";
  const padding = 6;
  const width = ctx.measureText(text).width + padding * 2;
  const height = 18;
  // 버튼 바로 아래(본문 첫 줄 위)에 오른쪽 맞춤으로. 노드 왼쪽 밖으로는 안 나가게.
  const x = Math.max(4, rect.x + rect.w - width);
  const y = rect.y + rect.h + 4;

  ctx.beginPath();
  if (ctx.roundRect) ctx.roundRect(x, y, width, height, 4);
  else ctx.rect(x, y, width, height);
  ctx.fillStyle = "#11141a";
  ctx.fill();
  ctx.strokeStyle = "#5a6270";
  ctx.lineWidth = 1;
  ctx.stroke();

  ctx.fillStyle = "#e8e8e8";
  ctx.textAlign = "left";
  ctx.textBaseline = "middle";
  ctx.fillText(text, x + padding, y + height / 2);
}

// --------------------------------------------------------------------------
// 등록
// --------------------------------------------------------------------------

app.registerExtension({
  name: "ComfyUI-LLM-Hub.Monitor",

  async setup() {
    // 이 줄이 F12 콘솔에 없으면 JS 가 아예 로드되지 않은 것이다.
    // (WEB_DIRECTORY 경로 문제로 실제로 겪었다 — 그때는 아무 흔적도 없었다.)
    console.log(
      `[LLM Hub] v${VERSION} monitor extension loaded. Diagnostics: /llmhub/health`
    );

    api.addEventListener(EVENT_NAME, (event) => {
      const data = event.detail || {};
      const [node, control] = panelFor(data.node);
      if (!node || !control) return;

      const mode = viewMode(node);
      if (mode === "off") return;

      const body = data.text || "";
      const thinking = data.thinking || "";

      if (body) {
        // 본문이 한 글자라도 오면 즉시 그쪽으로 갈아탄다. 사고 과정은 답이 아니므로
        // 답이 나오기 시작하면 더 보여줄 이유가 없다.
        control.lastIsNotice = false; // 지난 실행의 실패 문구 색이 남지 않게
        control.lastText = body;
        control.render(body, mode);
        control.setStatus(data.status, data.elapsed, data.done);
      } else if (thinking && !data.done) {
        // 이게 없으면 생성 시간의 대부분을 빈 창으로 앉아 있게 된다
        // (실측: 델타 298개가 thinking, 3개가 본문).
        control.renderThinking(thinking);
        control.setStatus("Thinking…", data.elapsed, false);
      } else {
        // 본문 없이 끝났을 때가 진짜 문제다. 상태 줄은 한 줄로 잘리므로
        // (.llmhub-status 의 ellipsis) 사용자 눈에는 "아무 일도 안 일어났다" 로
        // 보인다. 실제로 겪은 증상이다 -- workspace_dir 이 비어서 세 번 연속
        // 실패했는데 화면에는 끝까지 아무 것도 안 나왔고, 원인은 서버의 실행
        // 이력을 파서야 나왔다. 실패 사유는 본문에도 적는다.
        const notice = body ? "" : noticeFor(data.status, data.done);
        control.lastIsNotice = !!notice;
        control.lastText = body || notice;
        control.render(control.lastText, mode);
        control.setStatus(data.status, data.elapsed, data.done);
      }
    });

    // 새 실행이 시작되면 지난 결과를 지운다.
    // (이걸 안 하면 이번 실행이 아무것도 못 냈을 때 이전 결과가 현재 결과처럼 보인다.)
    api.addEventListener("execution_start", () => {
      for (const node of app.graph?._nodes || []) {
        const control = node[PANEL_KEY];
        if (control) {
          control.clear();
          control.setStatus("Idle", null, true);
        }
      }
    });
  },

  async beforeRegisterNodeDef(nodeType, nodeData) {
    if (nodeData.name !== NODE_NAME) return;

    const onCreated = nodeType.prototype.onNodeCreated;
    nodeType.prototype.onNodeCreated = function () {
      const result = onCreated?.apply(this, arguments);
      const control = createPanel(this);
      this[PANEL_KEY] = control;

      // stream_view 를 바꾸면 이미 받은 내용을 즉시 다시 그린다.
      const widget = this.widgets?.find((w) => w.name === "stream_view");
      if (widget) {
        const previous = widget.callback;
        const node = this;
        widget.callback = function (value) {
          control.render(control.lastText, value);
          // off 로 바꾸면 패널이 사라지고 노드도 그만큼 줄어야 한다.
          applyMonitorVisibility(node, value);
          resizeToWidgets(node);
          node.setDirtyCanvas?.(true, true);
          return previous?.apply(this, arguments);
        };
      }

      // backend 가 lmstudio 일 때만 lmstudio_* 위젯을 보인다(잡음 감소).
      setupBackendToggle(this);

      // 드롭다운에서 프리셋을 고르면 system_prompt 칸을 그 내용으로 채운다.
      // 이 위젯은 화면 전용이다 -- 생성 시점에 다시 합치면 같은 문장이 두 번
      // 들어간다(그래서 파이썬 쪽은 이 값을 보지 않는다).
      setupPresetLoader(this);

      // ComfyUI 가 LM Studio 보다 먼저 떴으면 모델 목록이 비어 있다. 조용히
      // 한 번만 다시 받아온다(페이지당 1회, 노드가 몇 개든 요청은 한 번).
      maybeAutoRefresh(this);

      this.size[1] = Math.max(this.size[1], 460);
      return result;
    };

    // 타이틀 바에 버튼을 그린다.
    const onDrawForeground = nodeType.prototype.onDrawForeground;
    nodeType.prototype.onDrawForeground = function (ctx) {
      const result = onDrawForeground?.apply(this, arguments);
      drawTitleButtons(this, ctx);
      return result;
    };

    // true 를 돌려주면 LiteGraph 가 노드 끌기를 시작하지 않는다.
    // 이게 없으면 버튼을 누를 때마다 노드가 딸려 움직인다.
    const onMouseDown = nodeType.prototype.onMouseDown;
    nodeType.prototype.onMouseDown = function (event, pos) {
      const hit = hitButton(this, pos);
      if (hit) {
        hit.spec.onClick(this);
        return true;
      }
      return onMouseDown?.apply(this, arguments);
    };

    // 마우스를 올리면 색이 바뀐다 — 이게 있어야 "눌리는 것" 으로 보인다.
    const onMouseMove = nodeType.prototype.onMouseMove;
    nodeType.prototype.onMouseMove = function (event, pos) {
      const key = hitButton(this, pos)?.spec.key ?? null;
      if (key !== (this[HOVER_KEY] ?? null)) {
        this[HOVER_KEY] = key;
        this.setDirtyCanvas?.(true, false);
      }
      return onMouseMove?.apply(this, arguments);
    };

    // 노드 밖으로 나가면 onMouseMove 가 더 안 불린다. 여기서 안 꺼주면
    // 강조된 채로 굳는다.
    const onMouseLeave = nodeType.prototype.onMouseLeave;
    nodeType.prototype.onMouseLeave = function () {
      if (this[HOVER_KEY]) {
        this[HOVER_KEY] = null;
        this.setDirtyCanvas?.(true, false);
      }
      return onMouseLeave?.apply(this, arguments);
    };

    // 우클릭 메뉴도 남겨둔다. 프론트엔드 버전에 따라 onMouseDown 이 안 불릴
    // 수 있는데, 그때 조작 수단이 통째로 사라지면 안 된다.
    const getExtraMenuOptions = nodeType.prototype.getExtraMenuOptions;
    nodeType.prototype.getExtraMenuOptions = function (canvas, options) {
      const result = getExtraMenuOptions?.apply(this, arguments);
      const shown = !!this.properties?.[SHOW_ADVANCED_PROP];
      options.push({
        content: shown ? "Hide advanced options" : "Show advanced options",
        callback: () => toggleAdvanced(this),
      });
      // 타이틀 바 버튼과 같은 이유로 여기에도 둔다 -- onMouseDown 이 안 불리는
      // 프론트엔드 버전에서도 목록을 되살릴 길이 남아 있어야 한다.
      if (isLmStudio(this)) {
        options.push({
          content: "Refresh LM Studio models",
          callback: () => refreshModels(this),
        });
      }
      return result;
    };

    const onConfigure = nodeType.prototype.onConfigure;
    nodeType.prototype.onConfigure = function () {
      const result = onConfigure?.apply(this, arguments);
      this._llmhubApplyBackendToggle?.();
      return result;
    };

    const onRemoved = nodeType.prototype.onRemoved;
    nodeType.prototype.onRemoved = function () {
      delete this[PANEL_KEY];
      return onRemoved?.apply(this, arguments);
    };
  },
});

// --------------------------------------------------------------------------
// 스타일 (라이트/다크 모두에서 읽히도록 ComfyUI 변수를 쓴다)
// --------------------------------------------------------------------------

const style = document.createElement("style");
style.textContent = `
/* --- 시스템 프롬프트 편집창 --------------------------------------------- */
/* z-index 를 높게 잡는다. ComfyUI 의 메뉴/사이드바보다 위에 떠야 한다. */
.llmhub-overlay {
  position: fixed; inset: 0; z-index: 10000;
  display: flex; align-items: center; justify-content: center;
  background: rgba(0, 0, 0, 0.55);
}
.llmhub-dialog {
  display: flex; flex-direction: column; gap: 8px;
  width: min(860px, 92vw); height: min(70vh, 720px);
  padding: 12px;
  box-sizing: border-box;
  border: 1px solid var(--border-color, #444);
  border-radius: 8px;
  background: var(--comfy-menu-bg, #202020);
  color: var(--input-text, #ddd);
  box-shadow: 0 12px 40px rgba(0, 0, 0, 0.5);
}
.llmhub-dialog-head { display: flex; align-items: center; justify-content: space-between; }
.llmhub-dialog-bar { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
.llmhub-dialog-bar select {
  flex: 1 1 200px; min-width: 0;
  padding: 3px 6px;
  background: var(--comfy-input-bg, #222);
  color: var(--input-text, #ddd);
  border: 1px solid var(--border-color, #444);
  border-radius: 4px;
}
.llmhub-dialog button, .llmhub-dialog-foot button {
  flex: 0 0 auto; white-space: nowrap;
  padding: 3px 10px; font-size: 12px; cursor: pointer;
  border: 1px solid var(--border-color, #444); border-radius: 4px;
  background: var(--comfy-input-bg, #222); color: var(--input-text, #ddd);
}
.llmhub-dialog button:hover { border-color: #8ab4f8; color: #8ab4f8; }
.llmhub-apply { border-color: #4a7a4a !important; }
.llmhub-apply:hover { border-color: #6ab86a !important; color: #9be89b !important; }
.llmhub-del:hover { border-color: #c04040 !important; color: #ff8080 !important; }
.llmhub-x { padding: 0 6px !important; }
/* 편집창이 화면의 주인공이다. 남는 세로 공간을 전부 가져간다. */
.llmhub-editor {
  flex: 1 1 auto; min-height: 0; resize: none;
  padding: 8px; box-sizing: border-box;
  border: 1px solid var(--border-color, #444); border-radius: 4px;
  background: var(--comfy-input-bg, #222); color: var(--input-text, #ddd);
  font-family: ui-monospace, SFMono-Regular, Consolas, "Liberation Mono", monospace;
  font-size: 13px; line-height: 1.5;
  white-space: pre-wrap;
}
.llmhub-dialog-foot { display: flex; align-items: center; gap: 6px; }
.llmhub-hint { margin-right: auto; font-size: 11px; color: var(--descrip-text, #888); }
.llmhub-dialog-msg { font-size: 11px; color: var(--descrip-text, #888); }
.llmhub-dialog-msg.llmhub-bad { color: #ff8080; }

/* 복사와 Stop 을 오른쪽 끝에 한 덩어리로 붙인다. Stop 은 생성 중에만 보이므로
   margin-left:auto 를 Stop 에 걸면 버튼 줄이 상태에 따라 좌우로 튄다. */
.llmhub-copy { margin-left: auto; }
.llmhub-copy, .llmhub-stop {
  /* flex 기본값은 줄어들 수 있음(shrink:1) 이라, 상태 문구가 길어지면
     버튼 폭이 글자 하나 너비까지 눌려서 "복/사" 처럼 세로로 접힌다.
     도구 이름이 긴 실행에서 실제로 그렇게 됐다. */
  flex: 0 0 auto;
  white-space: nowrap;
  padding: 1px 8px;
  font-size: 11px;
  line-height: 16px;
  cursor: pointer;
  border: 1px solid var(--border-color, #444);
  border-radius: 4px;
  background: var(--comfy-input-bg, #222);
  color: var(--input-text, #ddd);
}
.llmhub-stop:hover:not(:disabled) { border-color: #c04040; color: #ff8080; }
.llmhub-stop:disabled { opacity: 0.5; cursor: default; }
.llmhub-copy:hover { border-color: #4a90d9; color: #8ab4f8; }

/* 사고 과정은 답이 아니다 — 흐리고 기울여서 최종 결과와 한눈에 구분되게 한다. */
.llmhub-body.llmhub-thinking {
  opacity: 0.55;
  font-style: italic;
  white-space: pre-wrap;
}

/* 실패 사유도 답이 아니다 — 색으로 갈라놓지 않으면 모델이 낸 답으로 읽힌다. */
.llmhub-body.llmhub-notice {
  color: #ff9a9a;
  white-space: pre-wrap;
}

.llmhub-monitor {
  display: flex; flex-direction: column;
  width: 100%; height: 100%;
  box-sizing: border-box;
  border: 1px solid var(--border-color, #444);
  border-radius: 6px;
  background: var(--comfy-input-bg, #222);
  overflow: hidden;
}
.llmhub-head {
  display: flex; justify-content: space-between; align-items: center;
  gap: 8px; padding: 4px 8px;
  border-bottom: 1px solid var(--border-color, #444);
  font-size: 11px;
  color: var(--descrip-text, #999);
  flex: 0 0 auto;
}
/* 상태 문구만 줄어든다. 길면 말줄임하고 전체 내용은 마우스를 올리면 보인다.
   여기서 줄바꿈을 허용하면 생성 중에 헤더 높이가 들쭉날쭉해서 눈에 거슬린다.
   min-width:0 이 없으면 flex 항목이 내용보다 작아지지 않아 말줄임이 안 걸린다. */
.llmhub-status {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.llmhub-meta { flex: 0 0 auto; }
.llmhub-status.llmhub-running::before {
  content: "●"; margin-right: 5px; color: #4caf50;
  animation: llmhub-blink 1s steps(2, start) infinite;
}
@keyframes llmhub-blink { to { visibility: hidden; } }
.llmhub-body {
  flex: 1 1 auto; overflow-y: auto; overflow-x: hidden;
  padding: 6px 8px; margin: 0;
  font-size: 12px; line-height: 1.5;
  color: var(--input-text, #ddd);
  white-space: pre-wrap; word-break: break-word;
  font-family: ui-monospace, SFMono-Regular, Consolas, "Liberation Mono", monospace;
}
/* 마크다운 모드에서는 pre-wrap 을 풀고 일반 글꼴을 쓴다 */
.llmhub-body:has(p), .llmhub-body:has(h3), .llmhub-body:has(ul), .llmhub-body:has(ol) {
  white-space: normal;
  font-family: inherit;
}
.llmhub-body h3, .llmhub-body h4, .llmhub-body h5, .llmhub-body h6 {
  margin: 8px 0 4px; font-weight: 600;
}
.llmhub-body p { margin: 0 0 6px; }
.llmhub-body ul, .llmhub-body ol { margin: 0 0 6px; padding-left: 20px; }
.llmhub-body li { margin: 2px 0; }
.llmhub-body code {
  background: rgba(127, 127, 127, 0.22);
  padding: 1px 4px; border-radius: 3px;
  font-family: ui-monospace, Consolas, monospace; font-size: 11px;
}
.llmhub-body pre.llmhub-code {
  background: rgba(127, 127, 127, 0.16);
  padding: 6px 8px; border-radius: 4px; margin: 0 0 6px;
  overflow-x: auto; white-space: pre;
}
.llmhub-body pre.llmhub-code code { background: none; padding: 0; }
.llmhub-body blockquote {
  margin: 0 0 6px; padding-left: 8px;
  border-left: 3px solid var(--border-color, #555);
  color: var(--descrip-text, #999);
}
.llmhub-body hr { border: none; border-top: 1px solid var(--border-color, #444); margin: 8px 0; }
.llmhub-body a { color: #6ab7ff; }
`;
document.head.appendChild(style);
