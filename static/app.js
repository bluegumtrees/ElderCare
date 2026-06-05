// ============ 状态 ============
const state = {
  sessionId: loadOrCreateSession(),
  busy: false,
  autoSpeak: false,
  recognition: null,
  recording: false,
};

// ============ DOM ============
const chatArea = document.getElementById("chatArea");
const input = document.getElementById("input");
const sendBtn = document.getElementById("sendBtn");
const micBtn = document.getElementById("micBtn");
const resetBtn = document.getElementById("resetBtn");
const autoSpeakCheckbox = document.getElementById("autoSpeak");
const sessionLabel = document.getElementById("sessionLabel");
const statusText = document.getElementById("statusText");

sessionLabel.textContent = state.sessionId;

// ============ 会话管理 ============
function loadOrCreateSession() {
  let id = localStorage.getItem("eldercare_session");
  if (!id) {
    id = "s_" + Math.random().toString(36).slice(2, 10);
    localStorage.setItem("eldercare_session", id);
  }
  return id;
}

resetBtn.addEventListener("click", () => {
  const newId = "s_" + Math.random().toString(36).slice(2, 10);
  localStorage.setItem("eldercare_session", newId);
  state.sessionId = newId;
  sessionLabel.textContent = newId;
  chatArea.innerHTML = "";
  setStatus("已开启新会话");
});

// ============ 工具 ============
function setStatus(text) {
  statusText.textContent = text;
}

function escapeHTML(s) {
  return s.replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function scrollToBottom() {
  chatArea.scrollTop = chatArea.scrollHeight;
}

function clearWelcome() {
  const welcome = chatArea.querySelector(".welcome");
  if (welcome) welcome.remove();
}

// ============ 渲染消息 ============
function renderUserMessage(text) {
  clearWelcome();
  const wrap = document.createElement("div");
  wrap.className = "msg user";
  wrap.innerHTML = `
    <div class="avatar">👵</div>
    <div class="bubble">${escapeHTML(text)}</div>
  `;
  chatArea.appendChild(wrap);
  scrollToBottom();
}

function renderBotShell() {
  const wrap = document.createElement("div");
  wrap.className = "msg bot";
  wrap.innerHTML = `
    <div class="avatar">🤖</div>
    <div class="bubble">
      <div class="intent-row" data-role="intent-row" hidden></div>
      <div data-role="alert" hidden></div>
      <div data-role="text"><span class="cursor"></span></div>
      <details class="retrieved" data-role="retrieved" hidden>
        <summary>查看检索到的参考资料</summary>
        <div data-role="retrieved-list"></div>
      </details>
    </div>
  `;
  chatArea.appendChild(wrap);
  scrollToBottom();
  return wrap;
}

function setIntentBadge(botEl, intent, risk, logLevel) {
  const row = botEl.querySelector('[data-role="intent-row"]');
  const badgeClass = intent === "PSYCH" ? `badge PSYCH ${risk}` : `badge ${intent}`;
  row.innerHTML = `
    <span class="${badgeClass}">${intent}${intent === "PSYCH" ? " · " + risk : ""}</span>
    <span class="log-tag ${logLevel}">${logLevel}</span>
  `;
  row.hidden = false;
}

function setAlertBanner(botEl, reason, action) {
  const a = botEl.querySelector('[data-role="alert"]');
  const icon = reason === "EMERGENCY" ? "🚨" : "⚠️";
  a.innerHTML = `<div class="alert-banner">${icon} ${escapeHTML(action || "高风险预警已派发")}</div>`;
  a.hidden = false;
}

function setRetrievedChunks(botEl, hits) {
  if (!hits || hits.length === 0) return;
  const wrap = botEl.querySelector('[data-role="retrieved"]');
  const list = botEl.querySelector('[data-role="retrieved-list"]');
  const SCORE_LABELS = {
    distance: "cos",
    bm25_score: "bm25",
    rrf_score: "rrf",
    rerank_score: "rerank",
  };
  list.innerHTML = hits
    .map((h, i) => {
      const parts = [];
      for (const [key, label] of Object.entries(SCORE_LABELS)) {
        if (h[key] !== undefined && h[key] !== null) {
          parts.push(`${label}=${h[key]}`);
        }
      }
      return `
        <div class="retrieved-item" data-citation-idx="${i + 1}">
          <strong>[${i + 1}]</strong> ${escapeHTML(h.text)}
          <span class="dist">${parts.join(" · ")}</span>
        </div>`;
    })
    .join("");
  wrap.hidden = false;
}

function appendText(botEl, delta) {
  const target = botEl.querySelector('[data-role="text"]');
  const cursor = target.querySelector(".cursor");
  cursor.insertAdjacentText("beforebegin", delta);
}

function finalizeText(botEl) {
  // 幂等：被调多次时只第一次真正处理，避免对已渲染的徽章再次正则替换破坏 HTML
  if (botEl.dataset.finalized === "1") return;
  botEl.dataset.finalized = "1";

  const cursor = botEl.querySelector(".cursor");
  if (cursor) cursor.remove();
  // 流式结束后把 [N] 转成可点击的引用徽章
  // 注意：title 里不要再包含 [N]，否则即使有幂等也容易踩到二次替换的坑
  const textEl = botEl.querySelector('[data-role="text"]');
  if (textEl) {
    textEl.innerHTML = textEl.innerHTML.replace(
      /\[(\d+)\]/g,
      (_, n) => `<span class="citation" data-idx="${n}" title="点击查看引用资料">${n}</span>`
    );
  }
}

// ============ SSE over fetch ============
async function streamAgent(message) {
  const botEl = renderBotShell();
  let fullText = "";
  let intent = null;

  const resp = await fetch("/agent", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session_id: state.sessionId, message }),
  });

  if (!resp.ok || !resp.body) {
    appendText(botEl, `（请求出错：${resp.status}）`);
    finalizeText(botEl);
    return;
  }

  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    // SSE 一条记录以空行结尾，sse-starlette 用 CRLF，浏览器原生 EventSource 接受 LF/CRLF/CR
    // 用正则同时兼容 \r\n\r\n / \n\n
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

      if (evt === "intent") {
        intent = data;
        setIntentBadge(botEl, data.intent, data.risk_level, data.log_level);
        setStatus(`路由到 ${data.intent} (${data.log_level})`);
      } else if (evt === "alert") {
        setAlertBanner(botEl, data.reason, data.action);
      } else if (evt === "retrieved") {
        setRetrievedChunks(botEl, data.hits);
      } else if (evt === "message") {
        if (data.delta) {
          fullText += data.delta;
          appendText(botEl, data.delta);
          scrollToBottom();
        }
      } else if (evt === "done") {
        finalizeText(botEl);
        setStatus("就绪");
        if (state.autoSpeak && fullText) speak(fullText);
      }
    }
  }
  finalizeText(botEl);
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
  setStatus("分类中…");

  try {
    await streamAgent(message);
  } catch (e) {
    setStatus("出错：" + e.message);
  } finally {
    state.busy = false;
    sendBtn.disabled = false;
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
  input.style.height = Math.min(input.scrollHeight, 120) + "px";
}

// 示例 chip + 引用徽章
chatArea.addEventListener("click", (e) => {
  const chip = e.target.closest(".example-chip");
  if (chip) {
    send(chip.dataset.msg);
    return;
  }
  const cite = e.target.closest(".citation");
  if (cite) {
    const idx = cite.dataset.idx;
    const botEl = cite.closest(".msg.bot");
    if (!botEl) return;
    const details = botEl.querySelector('[data-role="retrieved"]');
    if (details) details.open = true;
    const target = botEl.querySelector(`.retrieved-item[data-citation-idx="${idx}"]`);
    if (target) {
      target.scrollIntoView({ behavior: "smooth", block: "center" });
      target.classList.add("citation-flash");
      setTimeout(() => target.classList.remove("citation-flash"), 1500);
    }
  }
});

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
    setStatus("听您说…");
  }
});

// ============ 语音朗读 ============
function speak(text) {
  if (!window.speechSynthesis) return;
  speechSynthesis.cancel();
  const u = new SpeechSynthesisUtterance(text);
  u.lang = "zh-CN";
  u.rate = 0.95;
  u.pitch = 1;
  speechSynthesis.speak(u);
}

autoSpeakCheckbox.addEventListener("change", (e) => {
  state.autoSpeak = e.target.checked;
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

// 初始化：从 localStorage 恢复
const savedSize = parseInt(localStorage.getItem("eldercare_fontsize") || "17", 10);
applyReadingSize(savedSize);
