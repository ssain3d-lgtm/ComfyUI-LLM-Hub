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
  widget.computeSize = (width) => [width, 180];

  return control;
}

function viewMode(node) {
  const widget = node.widgets?.find((w) => w.name === "stream_view");
  return widget ? widget.value : "plain";
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

      this.size[1] = Math.max(this.size[1], 460);
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
