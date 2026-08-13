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

function createPanel(node) {
  const root = document.createElement("div");
  root.className = "llmhub-monitor";
  root.innerHTML = `
    <div class="llmhub-head">
      <span class="llmhub-status">대기 중</span>
      <span class="llmhub-meta"></span>
      <button class="llmhub-copy" type="button" title="생성된 텍스트를 클립보드에 복사합니다">복사</button>
      <button class="llmhub-stop" type="button" title="이 노드의 생성을 중지합니다">■ Stop</button>
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
    if (!text) label = "내용 없음";
    else label = (await copyToClipboard(text)) ? "복사됨" : "복사 실패";

    copyEl.textContent = label;
    clearTimeout(copyTimer);
    copyTimer = setTimeout(() => {
      copyEl.textContent = "복사";
    }, 1200);
  });
  stopEl.addEventListener("click", async () => {
    stopEl.disabled = true;
    stopEl.textContent = "중지 중...";
    try {
      await api.fetchApi("/llmhub/stop", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ node: String(node.id) }),
      });
    } catch (error) {
      // 중지 요청이 실패해도 패널이 멈춘 것처럼 보이면 안 된다.
      statusEl.textContent = "중지 요청 실패";
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
    render(text, mode) {
      bodyEl.classList.remove("llmhub-thinking");
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
      bodyEl.textContent = text || "";
      if (stick) bodyEl.scrollTop = bodyEl.scrollHeight;
    },
    setStatus(status, elapsed, done) {
      statusEl.textContent = status || (done ? "완료" : "생성 중...");
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
      bodyEl.textContent = "";
      bodyEl.classList.remove("llmhub-thinking");
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
const BACKEND_ONLY = {
  openai_base_url: ["openai_compat"],
  claude_model: ["claude"],
  lmstudio_model: ["lmstudio"],
  lmstudio_ttl_sec: ["lmstudio"],
  lmstudio_unload_after: ["lmstudio"],
  temperature: ["lmstudio", "openai_compat"],
  max_tokens: ["lmstudio", "openai_compat"],
  mcp_config: ["claude"],
  video_max_frames: ["lmstudio", "claude", "codex", "openai_compat"],
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
const BUTTON = { width: 58, height: 18, margin: 8 };
const HOVER_KEY = "_llmhubBtnHover";

function buttonRect(node) {
  const titleHeight = window.LiteGraph?.NODE_TITLE_HEIGHT ?? 30;
  return {
    x: (node.size?.[0] ?? 0) - BUTTON.width - BUTTON.margin,
    // 타이틀 바는 노드 본문 기준 음수 y 영역이다(본문 위쪽).
    y: -titleHeight + (titleHeight - BUTTON.height) / 2,
    w: BUTTON.width,
    h: BUTTON.height,
  };
}

function insideButton(node, pos) {
  if (node.flags?.collapsed) return false;
  const r = buttonRect(node);
  return (
    pos[0] >= r.x && pos[0] <= r.x + r.w && pos[1] >= r.y && pos[1] <= r.y + r.h
  );
}

function drawAdvancedButton(node, ctx) {
  if (node.flags?.collapsed) return;
  const shown = !!node.properties?.[SHOW_ADVANCED_PROP];
  const hover = !!node[HOVER_KEY];
  const r = buttonRect(node);

  ctx.save();
  ctx.beginPath();
  // roundRect 는 비교적 최근 API 라 없을 수 있다. 없으면 각진 사각형으로 떨어진다.
  if (ctx.roundRect) ctx.roundRect(r.x, r.y, r.w, r.h, 4);
  else ctx.rect(r.x, r.y, r.w, r.h);
  ctx.fillStyle = hover ? "#4b5563" : shown ? "#3b4351" : "#2c3038";
  ctx.fill();
  ctx.strokeStyle = hover ? "#8ab4f8" : "#5a6270";
  ctx.lineWidth = 1;
  ctx.stroke();

  ctx.fillStyle = "#e8e8e8";
  ctx.font = "11px sans-serif";
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  ctx.fillText(shown ? "▲ 고급" : "▼ 고급", r.x + r.w / 2, r.y + r.h / 2);
  ctx.restore();
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
      `[LLM Hub] v${VERSION} 모니터 확장 로드됨. 진단: /llmhub/health`
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
        control.lastText = body;
        control.render(body, mode);
        control.setStatus(data.status, data.elapsed, data.done);
      } else if (thinking && !data.done) {
        // 이게 없으면 생성 시간의 대부분을 빈 창으로 앉아 있게 된다
        // (실측: 델타 298개가 thinking, 3개가 본문).
        control.renderThinking(thinking);
        control.setStatus("생각 중...", data.elapsed, false);
      } else {
        control.lastText = body;
        control.render(body, mode);
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

      this.size[1] = Math.max(this.size[1], 460);
      return result;
    };

    // 타이틀 바에 버튼을 그린다.
    const onDrawForeground = nodeType.prototype.onDrawForeground;
    nodeType.prototype.onDrawForeground = function (ctx) {
      const result = onDrawForeground?.apply(this, arguments);
      drawAdvancedButton(this, ctx);
      return result;
    };

    // true 를 돌려주면 LiteGraph 가 노드 끌기를 시작하지 않는다.
    // 이게 없으면 버튼을 누를 때마다 노드가 딸려 움직인다.
    const onMouseDown = nodeType.prototype.onMouseDown;
    nodeType.prototype.onMouseDown = function (event, pos) {
      if (insideButton(this, pos)) {
        toggleAdvanced(this);
        return true;
      }
      return onMouseDown?.apply(this, arguments);
    };

    // 마우스를 올리면 색이 바뀐다 — 이게 있어야 "눌리는 것" 으로 보인다.
    const onMouseMove = nodeType.prototype.onMouseMove;
    nodeType.prototype.onMouseMove = function (event, pos) {
      const hover = insideButton(this, pos);
      if (hover !== !!this[HOVER_KEY]) {
        this[HOVER_KEY] = hover;
        this.setDirtyCanvas?.(true, false);
      }
      return onMouseMove?.apply(this, arguments);
    };

    // 노드 밖으로 나가면 onMouseMove 가 더 안 불린다. 여기서 안 꺼주면
    // 강조된 채로 굳는다.
    const onMouseLeave = nodeType.prototype.onMouseLeave;
    nodeType.prototype.onMouseLeave = function () {
      if (this[HOVER_KEY]) {
        this[HOVER_KEY] = false;
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
        content: shown ? "고급 옵션 접기" : "고급 옵션 펼치기",
        callback: () => toggleAdvanced(this),
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
