// ============================================================
// ElderCare 前端逻辑 v2
// - SSE over fetch 流式渲染 + 轻量 markdown + 引用徽章
// - Agent 执行轨迹面板（演示模式）
// - 对比模式：完整 Agent vs 裸 LLM 双栏
// - 登录 / 注册 / demo 账号 / 历史会话
// ============================================================

// ============ 状态 ============
const state = {
  sessionId: loadOrCreateSession(),
  busy: false,
  autoSpeak: localStorage.getItem("eldercare_speak") === "1",
  recognition: null,
  recording: false,
  user: null,
  demoMode: localStorage.getItem("eldercare_demo") === "1",
  traceOpen: true,
  compareMode: false,
  activeConv: null, // 正在回看的历史会话 id
};

// ============ DOM ============
const $ = (id) => document.getElementById(id);
const chatArea = $("chatArea");
const input = $("input");
const sendBtn = $("sendBtn");
const micBtn = $("micBtn");
const resetBtn = $("resetBtn");
const autoSpeakBtn = $("autoSpeakBtn");
const sessionLabel = $("sessionLabel");
const statusText = $("statusText");
const demoBtn = $("demoBtn");
const demoBar = $("demoBar");
const traceToggle = $("traceToggle");
const compareToggle = $("compareToggle");
const tracePanel = $("tracePanel");
const traceSteps = $("traceSteps");
const traceQuery = $("traceQuery");
const sidebar = $("sidebar");
const sidebarClose = $("sidebarClose");
const convList = $("convList");
const historyBtn = $("historyBtn");
const loginBtn = $("loginBtn");
const loginModal = $("loginModal");
const modalClose = $("modalClose");
const authForm = $("authForm");
const authError = $("authError");
const authSubmit = $("authSubmit");
const displayNameRow = $("displayNameRow");
const demoLoginBtn = $("demoLoginBtn");
const userChip = $("userChip");
const userName = $("userName");
const logoutBtn = $("logoutBtn");

// ============ 意图元数据 ============
const INTENT_META = {
  CHAT: { label: "闲聊陪伴", icon: "i-message" },
  HEALTH: { label: "健康咨询", icon: "i-pulse" },
  PSYCH: { label: "心理关怀", icon: "i-heart" },
  EMERGENCY: { label: "紧急情况", icon: "i-alert" },
  FRAUD: { label: "防诈提醒", icon: "i-shield" },
};
const RISK_LABEL = { low: "低风险", mid: "中风险", high: "高风险" };

const EXAMPLES = [
  { icon: "i-message", color: "var(--c-chat)", msg: "今天天气真好" },
  { icon: "i-pulse", color: "var(--c-health)", msg: "我血压有点高怎么办" },
  { icon: "i-heart", color: "var(--c-psych)", msg: "老伴走了我一个人好孤单" },
  { icon: "i-alert", color: "var(--c-emergency)", msg: "我胸口好痛喘不上气" },
  { icon: "i-shield", color: "var(--c-fraud)", msg: "有人说我中奖了让我转钱" },
];

// ============ 工具 ============
function loadOrCreateSession() {
  let id = localStorage.getItem("eldercare_session");
  if (!id) {
    id = newSessionId();
    localStorage.setItem("eldercare_session", id);
  }
  return id;
}

function newSessionId() {
  return "s_" + Math.random().toString(36).slice(2, 10);
}

function setStatus(text) {
  statusText.textContent = text || "";
}

function escapeHTML(s) {
  return s.replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function svgIcon(name) {
  return `<svg class="icon" aria-hidden="true"><use href="#${name}"/></svg>`;
}

function isNearBottom() {
  return chatArea.scrollHeight - chatArea.scrollTop - chatArea.clientHeight < 140;
}

function scrollToBottom(force = false) {
  if (force || isNearBottom()) chatArea.scrollTop = chatArea.scrollHeight;
}

// ============ 轻量 Markdown（先转义再渲染，防注入；从原文全量重渲染，幂等） ============
function renderInline(escaped, { staticCite = false } = {}) {
  let s = escaped;
  s = s.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  s = s.replace(/`([^`]+)`/g, "<code>$1</code>");
  const cls = staticCite ? "citation static" : "citation";
  const title = staticCite ? "历史消息不含检索快照" : "点击查看引用资料";
  s = s.replace(/\[(\d{1,2})\]/g, (_, n) =>
    `<span class="${cls}" data-idx="${n}" title="${title}">${n}</span>`);
  return s;
}

function renderMarkdown(raw, opts = {}) {
  const lines = escapeHTML(raw).split("\n");
  let html = "";
  let list = null; // "ul" | "ol" | null
  let para = [];

  const flushPara = () => {
    if (para.length) {
      html += `<p>${para.map((l) => renderInline(l, opts)).join("<br>")}</p>`;
      para = [];
    }
  };
  const closeList = () => {
    if (list) { html += `</${list}>`; list = null; }
  };

  for (const line of lines) {
    const ul = line.match(/^\s*[-•]\s+(.+)/);
    const ol = line.match(/^\s*\d+[.、)]\s+(.+)/);
    if (ul || ol) {
      flushPara();
      const want = ul ? "ul" : "ol";
      if (list !== want) { closeList(); html += `<${want}>`; list = want; }
      html += `<li>${renderInline((ul || ol)[1], opts)}</li>`;
    } else if (!line.trim()) {
      flushPara();
      closeList();
    } else {
      closeList();
      para.push(line);
    }
  }
  flushPara();
  closeList();
  return html;
}

// ============ 欢迎页 ============
function renderWelcome() {
  const chips = EXAMPLES.map(
    (e) => `
    <button class="example-chip" data-msg="${escapeHTML(e.msg)}">
      <span class="chip-dot" style="background:${e.color}">${svgIcon(e.icon)}</span>
      ${escapeHTML(e.msg)}
    </button>`
  ).join("");
  chatArea.innerHTML = `
    <div class="welcome">
      <div class="welcome-mark">${svgIcon("i-blossom")}</div>
      <h1>您好呀，我在呢</h1>
      <p>可以跟我聊聊天、问问健康上的事，或者说说心里话。</p>
      <div class="examples">${chips}</div>
    </div>`;
}

function clearWelcome() {
  const welcome = chatArea.querySelector(".welcome");
  if (welcome) welcome.remove();
}

// ============ 消息渲染 ============
function renderUserMessage(text) {
  clearWelcome();
  const wrap = document.createElement("div");
  wrap.className = "msg user";
  wrap.innerHTML = `
    <div class="avatar">${svgIcon("i-user")}</div>
    <div class="bubble">${escapeHTML(text)}</div>`;
  chatArea.appendChild(wrap);
  scrollToBottom(true);
  return wrap;
}

function botShellHTML() {
  return `
    <div class="avatar">${svgIcon("i-blossom")}</div>
    <div class="bubble">
      <div class="intent-row" data-role="intent-row" hidden></div>
      <div data-role="alert" hidden></div>
      <div class="msg-text" data-role="text"><span class="cursor"></span></div>
      <details class="retrieved" data-role="retrieved" hidden>
        <summary>${svgIcon("i-book")}<span data-role="ref-count">参考资料</span><span class="chev">${svgIcon("i-chevron")}</span></summary>
        <div data-role="retrieved-list"></div>
      </details>
    </div>`;
}

function renderBotShell(container = null) {
  const wrap = document.createElement("div");
  wrap.className = "msg bot";
  wrap.innerHTML = botShellHTML();
  (container || chatArea).appendChild(wrap);
  scrollToBottom(true);
  return wrap;
}

function setIntentBadge(botEl, intent, risk, logLevel) {
  const row = botEl.querySelector('[data-role="intent-row"]');
  const meta = INTENT_META[intent] || { label: intent, icon: "i-message" };
  const badgeClass = intent === "PSYCH" ? `badge PSYCH ${risk}` : `badge ${intent}`;
  const riskText = intent === "PSYCH" ? ` · ${RISK_LABEL[risk] || risk}` : "";
  row.innerHTML = `
    <span class="${badgeClass}">${svgIcon(meta.icon)}${meta.label}${riskText}</span>
    <span class="log-tag ${logLevel}">${logLevel}</span>`;
  row.hidden = false;
}

function setAlertBanner(botEl, reason, action) {
  const a = botEl.querySelector('[data-role="alert"]');
  a.innerHTML = `<div class="alert-banner">${svgIcon("i-alert")}${escapeHTML(action || "高风险预警已派发")}</div>`;
  a.hidden = false;
}

function setRetrievedChunks(botEl, hits) {
  if (!hits || hits.length === 0) return;
  const wrap = botEl.querySelector('[data-role="retrieved"]');
  const list = botEl.querySelector('[data-role="retrieved-list"]');
  const countEl = botEl.querySelector('[data-role="ref-count"]');
  const SCORE_LABELS = {
    distance: "cos", bm25_score: "bm25", rrf_score: "rrf", rerank_score: "rerank",
  };
  list.innerHTML = hits
    .map((h, i) => {
      const chips = [];
      const meta = h.metadata || {};
      if (meta.source) chips.push(`<span class="ref-chip">来源 ${escapeHTML(String(meta.source))}</span>`);
      if (meta.topic) chips.push(`<span class="ref-chip">${escapeHTML(String(meta.topic))}</span>`);
      for (const [key, label] of Object.entries(SCORE_LABELS)) {
        if (h[key] !== undefined && h[key] !== null) {
          chips.push(`<span class="ref-chip score">${label} ${h[key]}</span>`);
        }
      }
      return `
        <div class="retrieved-item" data-citation-idx="${i + 1}">
          <strong>[${i + 1}]</strong>${escapeHTML(h.text)}
          <div class="ref-meta">${chips.join("")}</div>
        </div>`;
    })
    .join("");
  if (countEl) countEl.textContent = `参考资料（${hits.length} 条）`;
  wrap.hidden = false;
}

function paintStreaming(botEl, fullText) {
  const target = botEl.querySelector('[data-role="text"]');
  target.innerHTML = renderMarkdown(fullText) + '<span class="cursor"></span>';
}

function finalizeText(botEl, fullText) {
  const target = botEl.querySelector('[data-role="text"]');
  target.innerHTML = renderMarkdown(fullText);
}

// ============ 执行轨迹面板 ============
const TRACE_STEPS = [
  { key: "classify", name: "意图分诊", sub: "intent × risk × 检索改写" },
  { key: "dense", name: "向量召回", sub: "BGE-small-zh · HNSW" },
  { key: "sparse", name: "BM25 召回", sub: "jieba 分词 · Okapi" },
  { key: "rrf", name: "RRF 融合", sub: "排名倒数融合 k=60" },
  { key: "rerank", name: "CrossEncoder 精排", sub: "bge-reranker-base" },
  { key: "generate", name: "LLM 生成", sub: "流式输出 · 引用标注" },
];

function resetTrace() {
  traceQuery.hidden = true;
  traceQuery.textContent = "";
  traceSteps.innerHTML = TRACE_STEPS.map(
    (s) => `
    <div class="trace-step" data-step="${s.key}">
      <div class="trace-rail"><div class="trace-dot"></div><div class="trace-line"></div></div>
      <div class="trace-body">
        <div class="trace-name">${escapeHTML(s.name)}<span class="trace-ms" hidden></span></div>
        <div class="trace-detail">${escapeHTML(s.sub)}</div>
      </div>
    </div>`
  ).join("");
  markTraceActive("classify");
}

function traceStepEl(key) {
  return traceSteps.querySelector(`[data-step="${key}"]`);
}

function markTraceActive(key) {
  const el = traceStepEl(key);
  if (el && !el.classList.contains("done")) el.classList.add("active");
}

function markTraceDone(key, detail, ms) {
  const el = traceStepEl(key);
  if (!el) return;
  el.classList.remove("active");
  el.classList.add("done");
  if (detail) el.querySelector(".trace-detail").textContent = detail;
  if (ms !== undefined && ms !== null) {
    const msEl = el.querySelector(".trace-ms");
    msEl.textContent = `${ms}ms`;
    msEl.hidden = false;
  }
}

function finishTrace() {
  traceSteps.querySelectorAll(".trace-step").forEach((el) => {
    el.classList.remove("active");
    if (!el.classList.contains("done")) {
      el.classList.add("skip");
      el.querySelector(".trace-detail").textContent = "本次路由跳过";
    }
  });
}

const TRACE_NEXT = { classify: "dense", dense: "sparse", sparse: "rrf", rrf: "rerank", rerank: "generate" };

function handleStageEvent(data) {
  const { stage, ms } = data;
  if (stage === "classify") {
    const meta = INTENT_META[data.intent] || { label: data.intent };
    markTraceDone("classify", `${meta.label} (${data.intent}) · ${RISK_LABEL[data.risk_level] || data.risk_level} · ${data.log_level}`, ms);
    if (data.query && ["HEALTH", "PSYCH"].includes(data.intent)) {
      traceQuery.textContent = `检索改写：「${data.query}」`;
      traceQuery.hidden = false;
      markTraceActive("dense");
    } else {
      markTraceActive("generate");
    }
  } else if (stage === "dense") {
    markTraceDone("dense", `召回 Top ${data.count}`, ms);
    markTraceActive("sparse");
  } else if (stage === "sparse") {
    markTraceDone("sparse", `召回 Top ${data.count}`, ms);
    markTraceActive("rrf");
  } else if (stage === "rrf") {
    markTraceDone("rrf", `${data.in ?? "-"} 条候选 → 去重融合 ${data.count} 条`, ms);
    markTraceActive("rerank");
  } else if (stage === "rerank") {
    markTraceDone("rerank", `精选 Top ${data.count} 送入 LLM`, ms);
    markTraceActive("generate");
  } else if (stage === "generate") {
    const parts = [];
    if (data.first_token_ms !== undefined) parts.push(`首字 ${data.first_token_ms}ms`);
    if (ms !== undefined) parts.push(`共 ${ms}ms`);
    markTraceDone("generate", parts.join(" · ") || "完成", null);
  }
}

// ============ SSE over fetch ============
async function ssePost(url, body, onEvent) {
  const resp = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!resp.ok || !resp.body) throw new Error(`HTTP ${resp.status}`);

  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    // SSE 记录以空行分隔；sse-starlette 用 CRLF，这里同时兼容 \r\n\r\n / \n\n
    const records = buffer.split(/\r?\n\r?\n/);
    buffer = records.pop();

    for (const rec of records) {
      if (!rec.trim()) continue;
      const lines = rec.split(/\r?\n/);
      let evt = "message";
      let dataStr = "";
      for (const line of lines) {
        if (line.startsWith("event:")) evt = line.slice(6).trim();
        else if (line.startsWith("data:")) dataStr += line.slice(5).trim();
      }
      if (!dataStr) continue;
      let data;
      try { data = JSON.parse(dataStr); } catch { continue; }
      onEvent(evt, data);
    }
  }
}

// ============ 两条流式管线 ============
async function streamAgent(botEl, message, { withTrace = true } = {}) {
  let fullText = "";
  await ssePost("/agent", { session_id: state.sessionId, message }, (evt, data) => {
    if (evt === "stage") {
      if (withTrace) handleStageEvent(data);
    } else if (evt === "intent") {
      setIntentBadge(botEl, data.intent, data.risk_level, data.log_level);
      setStatus(`已路由到「${(INTENT_META[data.intent] || {}).label || data.intent}」`);
    } else if (evt === "alert") {
      setAlertBanner(botEl, data.reason, data.action);
    } else if (evt === "retrieved") {
      setRetrievedChunks(botEl, data.hits);
    } else if (evt === "message") {
      if (data.delta) {
        fullText += data.delta;
        paintStreaming(botEl, fullText);
        scrollToBottom();
      }
    } else if (evt === "error") {
      setStatus(data.message || "出了点小问题");
    } else if (evt === "done") {
      finalizeText(botEl, fullText);
      if (withTrace) finishTrace();
      setStatus("");
      if (state.autoSpeak && fullText) speak(fullText);
    }
  });
  finalizeText(botEl, fullText);
  return fullText;
}

async function streamPlain(botEl, message) {
  let fullText = "";
  await ssePost(
    "/chat",
    { session_id: state.sessionId, message, save_history: false },
    (evt, data) => {
      if (evt === "message" && data.delta) {
        fullText += data.delta;
        paintStreaming(botEl, fullText);
        scrollToBottom();
      } else if (evt === "done") {
        finalizeText(botEl, fullText);
      }
    }
  );
  finalizeText(botEl, fullText);
  return fullText;
}

// ============ 发送 ============
async function send(message) {
  if (state.busy) return;
  message = (message ?? input.value).trim();
  if (!message) return;

  state.busy = true;
  sendBtn.disabled = true;
  input.value = "";
  resizeTextarea();

  renderUserMessage(message);
  if (state.demoMode) resetTrace();
  setStatus("正在想怎么回您…");

  try {
    if (state.compareMode) {
      clearWelcome();
      const wrap = document.createElement("div");
      wrap.className = "compare-wrap";
      wrap.innerHTML = `
        <div class="compare-col">
          <span class="compare-label agent">${svgIcon("i-route")}完整 Agent · 路由 + 三阶段检索 + 引用</span>
        </div>
        <div class="compare-col">
          <span class="compare-label plain">${svgIcon("i-message")}裸 LLM · 无检索</span>
        </div>`;
      chatArea.appendChild(wrap);
      const [colA, colB] = wrap.querySelectorAll(".compare-col");
      const botA = renderBotShell(colA);
      const botB = renderBotShell(colB);
      scrollToBottom(true);
      await Promise.allSettled([
        streamAgent(botA, message),
        streamPlain(botB, message),
      ]);
    } else {
      const botEl = renderBotShell();
      await streamAgent(botEl, message);
    }
    if (state.user) loadConversations(); // 刷新侧栏排序/标题
  } catch (e) {
    setStatus("出错了：" + e.message);
  } finally {
    state.busy = false;
    sendBtn.disabled = false;
    setStatus("");
  }
}

sendBtn.addEventListener("click", () => send());
input.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    send();
  }
});
input.addEventListener("input", resizeTextarea);

function resizeTextarea() {
  input.style.height = "auto";
  input.style.height = Math.min(input.scrollHeight, 132) + "px";
}

// 示例 chip + 引用徽章（事件委托）
chatArea.addEventListener("click", (e) => {
  const chip = e.target.closest(".example-chip");
  if (chip) {
    send(chip.dataset.msg);
    return;
  }
  const cite = e.target.closest(".citation");
  if (cite && !cite.classList.contains("static")) {
    const idx = cite.dataset.idx;
    const botEl = cite.closest(".msg.bot");
    if (!botEl) return;
    const details = botEl.querySelector('[data-role="retrieved"]');
    if (details) details.open = true;
    const target = botEl.querySelector(`.retrieved-item[data-citation-idx="${idx}"]`);
    if (target) {
      target.scrollIntoView({ behavior: "smooth", block: "center" });
      target.classList.add("citation-flash");
      setTimeout(() => target.classList.remove("citation-flash"), 1600);
    }
  }
});

// ============ 新对话 ============
resetBtn.addEventListener("click", () => {
  state.sessionId = newSessionId();
  localStorage.setItem("eldercare_session", state.sessionId);
  sessionLabel.textContent = state.sessionId;
  state.activeConv = null;
  renderWelcome();
  markActiveConv();
  setStatus("已开启新对话");
  setTimeout(() => setStatus(""), 1500);
});

// ============ 演示模式 ============
function applyDemoMode() {
  document.body.classList.toggle("demo", state.demoMode);
  demoBtn.setAttribute("aria-pressed", String(state.demoMode));
  demoBar.hidden = !state.demoMode;
  tracePanel.hidden = !(state.demoMode && state.traceOpen);
  localStorage.setItem("eldercare_demo", state.demoMode ? "1" : "0");
}

demoBtn.addEventListener("click", () => {
  state.demoMode = !state.demoMode;
  applyDemoMode();
});

traceToggle.addEventListener("click", () => {
  state.traceOpen = !state.traceOpen;
  traceToggle.setAttribute("aria-pressed", String(state.traceOpen));
  tracePanel.hidden = !(state.demoMode && state.traceOpen);
});

compareToggle.addEventListener("click", () => {
  state.compareMode = !state.compareMode;
  compareToggle.setAttribute("aria-pressed", String(state.compareMode));
  setStatus(state.compareMode ? "对比模式已开启：每条消息同时问「完整 Agent」和「裸 LLM」" : "对比模式已关闭");
  setTimeout(() => setStatus(""), 2500);
});

// ============ 登录 / 注册 ============
let authTab = "login";

function openModal() {
  loginModal.hidden = false;
  authError.hidden = true;
  $("authUsername").focus();
}
function closeModal() {
  loginModal.hidden = true;
}

loginBtn.addEventListener("click", openModal);
modalClose.addEventListener("click", closeModal);
loginModal.addEventListener("click", (e) => {
  if (e.target === loginModal) closeModal();
});

document.querySelectorAll(".modal-tabs button").forEach((btn) => {
  btn.addEventListener("click", () => {
    authTab = btn.dataset.tab;
    document.querySelectorAll(".modal-tabs button").forEach((b) =>
      b.classList.toggle("active", b === btn));
    displayNameRow.hidden = authTab !== "register";
    authSubmit.textContent = authTab === "register" ? "注册并登录" : "登录";
    $("authPassword").autocomplete = authTab === "register" ? "new-password" : "current-password";
    authError.hidden = true;
  });
});

authForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  authError.hidden = true;
  authSubmit.disabled = true;
  try {
    const body = {
      username: $("authUsername").value.trim(),
      password: $("authPassword").value,
    };
    if (authTab === "register") {
      body.display_name = $("authDisplayName").value.trim() || null;
    }
    const resp = await fetch(`/auth/${authTab}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.detail || "登录失败");
    await onLoggedIn();
    closeModal();
  } catch (err) {
    authError.textContent = err.message;
    authError.hidden = false;
  } finally {
    authSubmit.disabled = false;
  }
});

demoLoginBtn.addEventListener("click", async () => {
  demoLoginBtn.disabled = true;
  try {
    const resp = await fetch("/auth/demo", { method: "POST" });
    if (!resp.ok) throw new Error("演示账号暂不可用");
    await onLoggedIn();
    closeModal();
    sidebar.hidden = false; // 直接展示预置的历史对话
  } catch (err) {
    authError.textContent = err.message;
    authError.hidden = false;
  } finally {
    demoLoginBtn.disabled = false;
  }
});

logoutBtn.addEventListener("click", async () => {
  await fetch("/auth/logout", { method: "POST" });
  state.user = null;
  updateAuthUI();
  sidebar.hidden = true;
});

async function fetchMe() {
  try {
    const resp = await fetch("/auth/me");
    const data = await resp.json();
    state.user = data.user;
  } catch {
    state.user = null;
  }
}

async function onLoggedIn() {
  await fetchMe();
  updateAuthUI();
  if (state.user) await loadConversations();
}

function updateAuthUI() {
  const logged = !!state.user;
  loginBtn.hidden = logged;
  userChip.hidden = !logged;
  historyBtn.hidden = !logged;
  if (logged) userName.textContent = state.user.display_name || state.user.username;
}

// ============ 历史会话 ============
function relTime(sqliteUTC) {
  // SQLite datetime('now') 是 UTC，补 Z 再解析
  const t = new Date(sqliteUTC.replace(" ", "T") + "Z");
  if (isNaN(t)) return "";
  const days = Math.floor((Date.now() - t.getTime()) / 86400000);
  if (days <= 0) return "今天";
  if (days === 1) return "昨天";
  if (days < 7) return `${days} 天前`;
  return `${t.getMonth() + 1}月${t.getDate()}日`;
}

async function loadConversations() {
  try {
    const resp = await fetch("/conversations");
    if (!resp.ok) return;
    const data = await resp.json();
    const items = data.conversations || [];
    if (!items.length) {
      convList.innerHTML = `<div class="conv-empty">还没有历史对话<br>先随便聊两句吧</div>`;
      return;
    }
    convList.innerHTML = items
      .map(
        (c) => `
      <button class="conv-item" data-sid="${escapeHTML(c.session_id)}">
        <div class="conv-title">${escapeHTML(c.title || "未命名对话")}</div>
        <div class="conv-meta"><span>${relTime(c.updated_at)}</span><span>${c.message_count} 条</span></div>
      </button>`
      )
      .join("");
    markActiveConv();
  } catch { /* 匿名或网络异常时静默 */ }
}

function markActiveConv() {
  convList.querySelectorAll(".conv-item").forEach((el) =>
    el.classList.toggle("active", el.dataset.sid === state.activeConv));
}

convList.addEventListener("click", (e) => {
  const item = e.target.closest(".conv-item");
  if (item) loadConversation(item.dataset.sid);
});

async function loadConversation(sid) {
  try {
    const resp = await fetch(`/conversations/${encodeURIComponent(sid)}/messages`);
    if (!resp.ok) throw new Error("会话不存在");
    const data = await resp.json();
    state.sessionId = sid;
    state.activeConv = sid;
    localStorage.setItem("eldercare_session", sid);
    sessionLabel.textContent = sid;
    markActiveConv();

    chatArea.innerHTML = "";
    for (const m of data.messages) {
      if (m.role === "user") {
        const wrap = document.createElement("div");
        wrap.className = "msg user";
        wrap.innerHTML = `
          <div class="avatar">${svgIcon("i-user")}</div>
          <div class="bubble">${escapeHTML(m.content)}</div>`;
        chatArea.appendChild(wrap);
      } else {
        const wrap = document.createElement("div");
        wrap.className = "msg bot";
        wrap.innerHTML = botShellHTML();
        wrap.querySelector(".cursor")?.remove();
        if (m.intent) setIntentBadge(wrap, m.intent, m.risk_level, m.log_level || "INFO");
        wrap.querySelector('[data-role="text"]').innerHTML =
          renderMarkdown(m.content, { staticCite: true });
        chatArea.appendChild(wrap);
      }
    }
    chatArea.scrollTop = 0;
    setStatus(`已打开历史对话「${data.title || sid}」，可以接着聊`);
    setTimeout(() => setStatus(""), 2500);
  } catch (err) {
    setStatus(err.message);
  }
}

historyBtn.addEventListener("click", () => {
  sidebar.hidden = !sidebar.hidden;
  if (!sidebar.hidden) loadConversations();
});
sidebarClose.addEventListener("click", () => { sidebar.hidden = true; });

// ============ 语音输入（Web Speech API）============
function setupRecognition() {
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SR) return null;
  const rec = new SR();
  rec.lang = "zh-CN";
  rec.continuous = false;
  rec.interimResults = false;
  rec.onresult = (e) => {
    const text = e.results[0][0].transcript;
    input.value = text;
    resizeTextarea();
    send(text);
  };
  rec.onerror = (e) => setStatus("语音识别失败：" + e.error);
  rec.onend = () => {
    state.recording = false;
    micBtn.classList.remove("recording");
  };
  return rec;
}
state.recognition = setupRecognition();

micBtn.addEventListener("click", () => {
  if (!state.recognition) {
    setStatus("此浏览器不支持语音输入，请用 Chrome 或 Edge");
    return;
  }
  if (state.recording) {
    state.recognition.stop();
  } else {
    state.recognition.start();
    state.recording = true;
    micBtn.classList.add("recording");
    setStatus("我在听，您说…");
  }
});

// ============ 语音朗读 ============
function speak(text) {
  if (!window.speechSynthesis) return;
  speechSynthesis.cancel();
  const clean = text.replace(/\[\d+\]/g, "").replace(/[*`#]/g, "");
  const u = new SpeechSynthesisUtterance(clean);
  u.lang = "zh-CN";
  u.rate = 0.95;
  u.pitch = 1;
  speechSynthesis.speak(u);
}

autoSpeakBtn.addEventListener("click", () => {
  state.autoSpeak = !state.autoSpeak;
  autoSpeakBtn.setAttribute("aria-pressed", String(state.autoSpeak));
  localStorage.setItem("eldercare_speak", state.autoSpeak ? "1" : "0");
  if (!state.autoSpeak) speechSynthesis?.cancel();
});

// ============ 字号切换 ============
const fontButtons = document.querySelectorAll(".font-toggle button");

function applyReadingSize(px) {
  document.documentElement.style.setProperty("--reading-size", `${px}px`);
  fontButtons.forEach((b) => b.classList.toggle("active", b.dataset.size === String(px)));
}

fontButtons.forEach((btn) => {
  btn.addEventListener("click", () => {
    const size = parseInt(btn.dataset.size, 10);
    applyReadingSize(size);
    localStorage.setItem("eldercare_fontsize", String(size));
  });
});

// ============ 初始化 ============
(async function init() {
  sessionLabel.textContent = state.sessionId;
  applyReadingSize(parseInt(localStorage.getItem("eldercare_fontsize") || "19", 10));
  autoSpeakBtn.setAttribute("aria-pressed", String(state.autoSpeak));
  renderWelcome();
  resetTrace();
  applyDemoMode();
  await fetchMe();
  updateAuthUI();
  if (state.user) loadConversations();
})();
