// ComfyUI-LLM-Hub — 노드 위 실시간 모니터링 창
//
// 백엔드(utils/stream.py)가 "llmhub.stream" 이벤트로 누적 전문을 보내면
// 여기서 노드 안의 패널에 그려준다. 외부 CDN 을 쓰지 않는다(오프라인 동작).

import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";

const NODE_NAME = "LLMHubGenerate";
const EVENT_NAME = "llmhub.stream";

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
// 패널 생성
// --------------------------------------------------------------------------

function createPanel(node) {
  const root = document.createElement("div");
  root.className = "llmhub-monitor";
  root.innerHTML = `
    <div class="llmhub-head">
      <span class="llmhub-status">대기 중</span>
      <span class="llmhub-meta"></span>
    </div>
    <div class="llmhub-body"></div>
  `;

  const statusEl = root.querySelector(".llmhub-status");
  const metaEl = root.querySelector(".llmhub-meta");
  const bodyEl = root.querySelector(".llmhub-body");

  let stick = true; // 사용자가 위로 스크롤하면 자동 스크롤을 멈춘다
  bodyEl.addEventListener("scroll", () => {
    stick = bodyEl.scrollHeight - bodyEl.scrollTop - bodyEl.clientHeight < 24;
  });

  const control = {
    root,
    lastText: "",
    render(text, mode) {
      if (mode === "markdown") {
        bodyEl.innerHTML = renderMarkdown(text || "");
      } else {
        bodyEl.textContent = text || "";
      }
      if (stick) bodyEl.scrollTop = bodyEl.scrollHeight;
    },
    setStatus(status, elapsed, done) {
      statusEl.textContent = status || (done ? "완료" : "생성 중...");
      statusEl.classList.toggle("llmhub-running", !done);
      metaEl.textContent = elapsed != null ? `${elapsed}s` : "";
    },
    clear() {
      this.lastText = "";
      bodyEl.textContent = "";
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
  const PANEL_HEIGHT = 240;
  widget.computeSize = (width) => [width, PANEL_HEIGHT];
  widget.computeLayoutSize = () => ({ minHeight: PANEL_HEIGHT, minWidth: 200 });

  // 이 위젯은 반드시 widgets 배열의 맨 끝에 있어야 한다.
  //   serialize():  widgets_values[전체배열 인덱스] = 값   (건너뛴 자리에 구멍)
  //   configure():  구멍을 무시하고 순서대로 읽는다        (압축해서 읽음)
  // 둘이 어긋나 있어서, 직렬화되지 않는 위젯이 중간에 끼면 그 뒤 위젯 값이 전부
  // 한 칸씩 밀린다. 맨 끝일 때만 구멍이 배열 끝이라 무해하다.
  // → 모니터를 위로 올리고 싶으면 옮기지 말고 사이 위젯을 숨겨라.
  control.widget = widget;

  return control;
}

function viewMode(node) {
  const widget = node.widgets?.find((w) => w.name === "stream_view");
  return widget ? widget.value : "plain";
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
const BACKEND_ONLY = {
  claude_model: ["claude"],
  lmstudio_model: ["lmstudio"],
  lmstudio_ttl_sec: ["lmstudio"],
  lmstudio_unload_after: ["lmstudio"],
  temperature: ["lmstudio"],
  max_tokens: ["lmstudio"],
  mcp_config: ["claude"],
  video_max_frames: ["lmstudio", "claude", "codex"],
};

// 접었을 때 숨는 위젯. 여기 없는 것 = 항상 보이는 것이다:
//   backend / prompt / system_prompt / lmstudio_model
// 모니터 창을 system_prompt 바로 밑으로 끌어올리는 방법이 이것뿐이다 —
// DOM 위젯 자체는 위로 옮길 수 없다(createPanel 의 주석 참고).
const ADVANCED = [
  "model", "file_access", "workspace_dir", "temperature", "max_tokens",
  "timeout_sec", "seed", "video_max_frames", "stream_view", "video_path",
  "mcp_config", "extra_args", "lmstudio_ttl_sec", "lmstudio_unload_after",
  // INPUT_TYPES 에 없는 이름이다. seed 에 control_after_generate:True 를 주면
  // 프론트엔드가 짝꿍 위젯을 하나 더 만들어 붙인다. seed 만 숨기면 이게 홀로 남아
  // "고급을 접었는데 웬 randomize 줄이 남아 있는" 모양이 된다.
  "control_after_generate",
];

const SHOW_ADVANCED_PROP = "showAdvanced";

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
      const visible = usedByBackend && (showAdvanced || !ADVANCED.includes(w.name));

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

// --------------------------------------------------------------------------
// 등록
// --------------------------------------------------------------------------

app.registerExtension({
  name: "ComfyUI-LLM-Hub.Monitor",

  async setup() {
    api.addEventListener(EVENT_NAME, (event) => {
      const data = event.detail || {};
      const [node, control] = panelFor(data.node);
      if (!node || !control) return;

      const mode = viewMode(node);
      if (mode === "off") return;

      control.lastText = data.text || "";
      control.render(control.lastText, mode);
      control.setStatus(data.status, data.elapsed, data.done);
    });

    // 새 실행이 시작되면 지난 결과를 지운다.
    // (이걸 안 하면 이번 실행이 아무것도 못 냈을 때 이전 결과가 현재 결과처럼 보인다.)
    api.addEventListener("execution_start", () => {
      for (const node of app.graph?._nodes || []) {
        const control = node[PANEL_KEY];
        if (control) {
          control.clear();
          control.setStatus("대기 중", null, true);
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
        widget.callback = function (value) {
          control.render(control.lastText, value);
          return previous?.apply(this, arguments);
        };
      }

      // backend 가 lmstudio 일 때만 lmstudio_* 위젯을 보인다(잡음 감소).
      setupBackendToggle(this);

      this.size[1] = Math.max(this.size[1], 460);
      return result;
    };

    // 위젯을 하나 더 만들지 않고 우클릭 메뉴로 접고 편다.
    // 위젯을 추가하면 widgets_values 자리를 차지해 예전 워크플로우와 어긋난다.
    const getExtraMenuOptions = nodeType.prototype.getExtraMenuOptions;
    nodeType.prototype.getExtraMenuOptions = function (canvas, options) {
      const result = getExtraMenuOptions?.apply(this, arguments);
      const shown = !!this.properties?.[SHOW_ADVANCED_PROP];
      options.push({
        content: shown ? "고급 옵션 접기" : "고급 옵션 펼치기",
        callback: () => {
          this.properties[SHOW_ADVANCED_PROP] = !shown;
          this._llmhubApplyBackendToggle?.();
        },
      });
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
