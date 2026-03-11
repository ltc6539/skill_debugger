/* ================================================
   Skill Debugger — app.js
   Three-panel IDE layout
   ================================================ */

const state = {
  workspaces: [],
  activeWorkspaceId: null,
  current: null,
  pendingTurn: null,
  runtime: null,
  busy: false,
  pendingAssistantBubble: null,
};

const els = {
  workspaceSelect: document.getElementById("workspaceSelect"),
  createWorkspaceButton: document.getElementById("createWorkspaceButton"),
  deleteWorkspaceButton: document.getElementById("deleteWorkspaceButton"),
  uploadForm: document.getElementById("uploadForm"),
  skillFiles: document.getElementById("skillFiles"),
  skillFolderFiles: document.getElementById("skillFolderFiles"),
  clearContextButton: document.getElementById("clearContextButton"),
  skillsList: document.getElementById("skillsList"),
  runtimeStatus: document.getElementById("runtimeStatus"),
  skillCount: document.getElementById("skillCount"),
  turnCount: document.getElementById("turnCount"),
  chatLog: document.getElementById("chatLog"),
  chatForm: document.getElementById("chatForm"),
  chatInput: document.getElementById("chatInput"),
  traceLog: document.getElementById("traceLog"),
  forcedSkillSelect: document.getElementById("forcedSkillSelect"),
  toolForm: document.getElementById("toolForm"),
  toolNameInput: document.getElementById("toolNameInput"),
  toolsList: document.getElementById("toolsList"),
  toolHints: document.getElementById("toolHints"),
  addToolButton: document.getElementById("addToolButton"),
};

/* ---- helpers ---- */

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function formatJson(value) {
  if (value === null || value === undefined) return "";
  if (typeof value === "string") return value;
  return JSON.stringify(value, null, 2);
}

function truncate(str, len) {
  if (!str) return "";
  return str.length > len ? str.slice(0, len) + "..." : str;
}

function getTraceTimeline() {
  const sessionTurns = state.current?.session?.turns || [];
  const groups = [];

  sessionTurns.forEach((turn, index) => {
    const entries = Array.isArray(turn.trace) ? turn.trace : [];
    if (!entries.length) return;
    groups.push({
      turnIndex: index + 1,
      userMessage: turn.user_message || "",
      pending: false,
      entries,
    });
  });

  if (state.pendingTurn?.trace?.length) {
    groups.push({
      turnIndex: sessionTurns.length + 1,
      userMessage: state.pendingTurn.user_message || "",
      pending: true,
      entries: state.pendingTurn.trace,
    });
  }

  return groups;
}

function parseSse(buffer, onEvent) {
  const chunks = buffer.split("\n\n");
  const remainder = chunks.pop() || "";
  for (const chunk of chunks) {
    const lines = chunk.split("\n");
    let event = "message";
    let data = "";
    for (const line of lines) {
      if (line.startsWith("event:")) event = line.slice(6).trim();
      if (line.startsWith("data:")) data += line.slice(5).trimStart();
    }
    if (data) onEvent(event, JSON.parse(data));
  }
  return remainder;
}

function currentMode() {
  return document.querySelector('input[name="mode"]:checked')?.value || "agent";
}

function resetStreamingDomRefs() {
  state.pendingAssistantBubble = null;
}

/* ---- auto-resize textarea ---- */

els.chatInput.addEventListener("input", () => {
  els.chatInput.style.height = "auto";
  els.chatInput.style.height = Math.min(els.chatInput.scrollHeight, 180) + "px";
});

/* ---- busy state ---- */

function setBusy(nextBusy) {
  state.busy = nextBusy;
  els.createWorkspaceButton.disabled = nextBusy;
  els.deleteWorkspaceButton.disabled = nextBusy;
  els.workspaceSelect.disabled = nextBusy;
  els.clearContextButton.disabled = nextBusy;
  els.chatInput.disabled = nextBusy;
  els.skillFiles.disabled = nextBusy;
  els.skillFolderFiles.disabled = nextBusy;
  els.forcedSkillSelect.disabled = nextBusy || currentMode() !== "forced";
  if (els.toolNameInput) els.toolNameInput.disabled = nextBusy;
  if (els.addToolButton) els.addToolButton.disabled = nextBusy;
  document.getElementById("sendButton").disabled = nextBusy;
}

/* ---- render: workspaces ---- */

function renderWorkspaces() {
  const currentId = state.activeWorkspaceId;
  els.workspaceSelect.innerHTML = "";
  state.workspaces.forEach((ws) => {
    const option = document.createElement("option");
    option.value = ws.workspace_id;
    option.textContent = ws.name;
    els.workspaceSelect.appendChild(option);
  });
  if (currentId && state.workspaces.some((ws) => ws.workspace_id === currentId)) {
    els.workspaceSelect.value = currentId;
  }
  els.workspaceSelect.disabled = state.busy || state.workspaces.length <= 1;
}

/* ---- render: skills ---- */

function renderSkills() {
  const skills = state.current?.skills || [];
  els.skillsList.innerHTML = "";
  els.skillCount.textContent = `${skills.length}`;

  if (!skills.length) {
    els.skillsList.innerHTML =
      '<div class="skill-tree-empty">上传 skill 目录、zip 或 SKILL.md<br/>开始调试</div>';
    updateForcedSkillOptions([]);
    return;
  }

  skills.forEach((skill) => {
    const declaredTools = skill.declared_tools || skill.allowed_tools || [];
    const lint = skill.lint || {};
    const lintErrors = lint.errors || [];
    const lintWarnings = lint.warnings || [];
    const description = skill.description ? escapeHtml(skill.description) : "无描述";
    const declaredLabel = declaredTools.length
      ? `${declaredTools.length} declared tools · ${escapeHtml(declaredTools.join(", "))}`
      : "0 declared tools";
    const lintClass = lintErrors.length ? "error" : lintWarnings.length ? "warning" : "ok";
    const lintSummary = lintErrors.length
      ? `Lint errors · ${escapeHtml(lintErrors.map((item) => item.message).join(" | "))}`
      : lintWarnings.length
        ? `Lint warnings · ${escapeHtml(lintWarnings.map((item) => item.message).join(" | "))}`
        : "Lint ok";

    const row = document.createElement("div");
    row.className = "tool-row";
    row.innerHTML = `
      <div class="tool-row-main">
        <div class="tool-row-name">${escapeHtml(skill.skill_id)}</div>
        <div class="tool-row-meta">${description}</div>
        <div class="tool-row-meta skill-lint-meta ${lintClass}">${lintSummary}</div>
        <div class="tool-row-meta">${declaredLabel}</div>
      </div>
      <button class="tool-delete-btn" type="button" title="删除 skill" aria-label="删除 ${escapeHtml(skill.skill_id)}">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <polyline points="3 6 5 6 21 6"></polyline>
          <path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path>
          <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6"></path>
        </svg>
      </button>
    `;

    row.querySelector(".tool-delete-btn").addEventListener("click", (event) => {
      event.preventDefault();
      event.stopPropagation();
      deleteSkill(skill.skill_id).catch((error) => {
        window.alert(error.message);
      });
    });

    els.skillsList.appendChild(row);
  });

  updateForcedSkillOptions(skills);
}

function updateForcedSkillOptions(skills) {
  const previous = els.forcedSkillSelect.value;
  els.forcedSkillSelect.innerHTML = '<option value="">选择 skill...</option>';
  skills.forEach((skill) => {
    const opt = document.createElement("option");
    opt.value = skill.skill_id;
    opt.textContent = skill.skill_id;
    els.forcedSkillSelect.appendChild(opt);
  });
  if (skills.some((skill) => skill.skill_id === previous)) {
    els.forcedSkillSelect.value = previous;
  }
}

/* ---- render: tools ---- */

function renderTools() {
  if (!els.toolsList || !els.toolHints) return;
  const tools = state.current?.tools || [];
  const missing = state.current?.unregistered_declared_tools || [];
  els.toolsList.innerHTML = "";
  els.toolHints.innerHTML = "";

  if (!tools.length) {
    els.toolsList.innerHTML = '<div class="tool-list-empty">当前没有注册运行时 tools</div>';
  } else {
    tools.forEach((tool) => {
      const row = document.createElement("div");
      row.className = "tool-row";
      const linkedSkills = (tool.declared_by_skills || []).join(", ");
      row.innerHTML = `
        <div class="tool-row-main">
          <div class="tool-row-name">${escapeHtml(tool.name)}</div>
          <div class="tool-row-meta">${escapeHtml(tool.execution_mode || "stub")}${linkedSkills ? ` · used by ${escapeHtml(linkedSkills)}` : ""}</div>
        </div>
        <button class="tool-delete-btn" type="button" title="删除 tool" aria-label="删除 ${escapeHtml(tool.name)}">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <polyline points="3 6 5 6 21 6"></polyline>
            <path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path>
            <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6"></path>
          </svg>
        </button>
      `;
      row.querySelector(".tool-delete-btn").addEventListener("click", () => {
        deleteTool(tool.name).catch((error) => {
          window.alert(error.message);
        });
      });
      els.toolsList.appendChild(row);
    });
  }

  if (missing.length) {
    const title = document.createElement("div");
    title.className = "tool-hints-head";
    title.innerHTML = `
      <span class="tool-hints-title">Skill 声明了但当前未注册的工具</span>
      <span class="badge">${missing.length}</span>
    `;
    els.toolHints.appendChild(title);

    const note = document.createElement("div");
    note.className = "tool-hints-note";
    note.textContent = "这些工具写在上传的 skill 里，但当前 workspace 还没有注册，所以 Claude 现在用不到它们。";
    els.toolHints.appendChild(note);

    missing.forEach((tool) => {
      const hint = document.createElement("div");
      hint.className = "tool-hint";
      hint.innerHTML = `
        <span class="tool-hint-name">${escapeHtml(tool.name)}</span>
        <span class="tool-hint-meta">${escapeHtml((tool.declared_by_skills || []).join(", "))}</span>
      `;
      els.toolHints.appendChild(hint);
    });
  }
}

/* ---- render: header metadata ---- */

function renderHeader() {
  const traceCount = getTraceTimeline().reduce(
    (total, group) => total + group.entries.length,
    0
  );
  els.turnCount.textContent = `${traceCount}`;

  const runtime = state.current?.runtime || state.runtime || {};
  if (runtime.default_model) {
    els.runtimeStatus.textContent = runtime.default_model;
  } else if (runtime.claude_cli_path) {
    els.runtimeStatus.textContent = "Claude CLI";
  } else {
    els.runtimeStatus.textContent = "未配置";
  }
}

/* ---- render: chat ---- */

function createMessageNode(role, text, options = {}) {
  const wrapper = document.createElement("div");
  wrapper.className = `msg ${role === "user" ? "user-msg" : "assistant-msg"}`;

  const roleLabel = document.createElement("div");
  roleLabel.className = "msg-role";
  roleLabel.textContent = role === "user" ? "You" : "Assistant";

  const bubble = document.createElement("div");
  bubble.className = `msg-bubble${options.streaming ? " streaming" : ""}`;
  bubble.textContent = text || "";
  if (options.pendingAssistant) {
    bubble.dataset.pendingAssistant = "true";
  }

  wrapper.appendChild(roleLabel);
  wrapper.appendChild(bubble);
  return { wrapper, bubble };
}

function renderChat() {
  const turns = [...(state.current?.session?.turns || [])];
  if (state.pendingTurn) turns.push(state.pendingTurn);
  els.chatLog.innerHTML = "";
  resetStreamingDomRefs();

  if (!turns.length) {
    els.chatLog.innerHTML = `
      <div class="chat-log-empty">
        <div class="chat-log-empty-icon">
          <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>
        </div>
        <p>左侧上传 skills，注册 workspace tools，然后输入测试问题开始调试</p>
      </div>`;
    return;
  }

  turns.forEach((turn, index) => {
    const isStreaming = Boolean(state.pendingTurn) && index === turns.length - 1;
    const userNode = createMessageNode("user", turn.user_message || "");
    els.chatLog.appendChild(userNode.wrapper);

    const assistantNode = createMessageNode("assistant", turn.assistant_message || "", {
      streaming: isStreaming,
      pendingAssistant: isStreaming,
    });
    els.chatLog.appendChild(assistantNode.wrapper);

    if (isStreaming) {
      state.pendingAssistantBubble = assistantNode.bubble;
    }
  });

  els.chatLog.scrollTop = els.chatLog.scrollHeight;
}

function updatePendingAssistantMessage() {
  if (!state.pendingTurn) return;
  if (!state.pendingAssistantBubble || !document.body.contains(state.pendingAssistantBubble)) {
    state.pendingAssistantBubble = els.chatLog.querySelector('[data-pending-assistant="true"]');
  }
  if (!state.pendingAssistantBubble) {
    renderChat();
    return;
  }
  state.pendingAssistantBubble.textContent = state.pendingTurn.assistant_message || "";
  els.chatLog.scrollTop = els.chatLog.scrollHeight;
}

/* ---- render: trace ---- */

function renderTrace() {
  const traceTimeline = getTraceTimeline();
  els.traceLog.innerHTML = "";

  if (!traceTimeline.length) {
    els.traceLog.innerHTML =
      '<div class="trace-empty">发送测试问题后<br/>工具调用轨迹将出现在这里</div>';
    return;
  }

  traceTimeline.forEach((group) => {
    const section = document.createElement("section");
    section.className = "trace-turn-group";

    const header = document.createElement("div");
    header.className = "trace-turn-header";
    header.innerHTML = `
      <span class="trace-turn-label">Turn ${group.turnIndex}${group.pending ? " · streaming" : ""}</span>
      <span class="trace-turn-message">${escapeHtml(truncate(group.userMessage, 56) || "无用户输入")}</span>
    `;
    section.appendChild(header);

    group.entries.forEach((entry) => {
      const card = document.createElement("article");
      const isError = entry.status === "error";
      card.className = `trace-card${isError ? " error" : ""}`;

      const inputJson = formatJson(entry.input);
      const outputJson = formatJson(entry.output);
      const statusClass = isError ? "error" : entry.status === "running" ? "running" : "ok";
      const metaLabel =
        entry.category === "skill_activation"
          ? `<div class="trace-meta">Skill Activation${entry.skills?.length ? ` · ${escapeHtml(entry.skills.join(", "))}` : ""}</div>`
          : "";

      card.innerHTML = `
        <div class="trace-card-head">
          <span class="trace-tool-name">${escapeHtml(entry.tool || entry.type || "trace")}</span>
          <span class="trace-status ${statusClass}">${escapeHtml(entry.status || "")}</span>
        </div>
        <div class="trace-card-body">
          ${metaLabel}
          ${inputJson ? `<div class="trace-section-label">Input</div><pre class="trace-pre">${escapeHtml(inputJson)}</pre>` : ""}
          ${outputJson ? `<div class="trace-section-label">Output</div><pre class="trace-pre">${escapeHtml(outputJson)}</pre>` : ""}
        </div>
      `;

      card.querySelector(".trace-card-head").addEventListener("click", () => {
        card.classList.toggle("open");
      });

      section.appendChild(card);
    });

    els.traceLog.appendChild(section);
  });
}

/* ---- render all ---- */

function renderAll() {
  renderWorkspaces();
  renderSkills();
  renderTools();
  renderHeader();
  renderChat();
  renderTrace();
}

/* ---- API helpers ---- */

async function fetchJson(url, options) {
  const response = await fetch(url, options);
  if (!response.ok) {
    const message = await response.text();
    throw new Error(message || `Request failed: ${response.status}`);
  }
  return response.json();
}

/* ---- actions ---- */

async function bootstrap() {
  const payload = await fetchJson("/api/bootstrap");
  state.workspaces = payload.workspaces || [];
  state.runtime = payload.runtime || {};
  state.activeWorkspaceId = payload.current_workspace_id;
  state.current = payload.current;
  renderAll();
}

async function loadWorkspace(workspaceId) {
  state.activeWorkspaceId = workspaceId;
  state.pendingTurn = null;
  resetStreamingDomRefs();
  state.current = await fetchJson(`/api/workspaces/${workspaceId}`);
  renderAll();
}

async function createWorkspace() {
  const raw = window.prompt("输入新的测试空间名称。留空则自动生成。", "");
  if (raw === null) return;
  const name = raw.trim();
  const payload = await fetchJson("/api/workspaces", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name: name || null }),
  });
  state.activeWorkspaceId = payload.workspace.workspace_id;
  state.current = payload;
  state.workspaces = await fetchJson("/api/workspaces").then((data) => data.workspaces || []);
  renderAll();
}

async function deleteCurrentWorkspace() {
  if (!state.activeWorkspaceId || state.busy) return;
  const workspace = state.current?.workspace;
  const name = workspace?.name || state.activeWorkspaceId;
  const confirmed = window.confirm(
    `确认删除当前测试空间“${name}”吗？\n\n已上传的 skills、tools 和聊天历史都会被删除。`
  );
  if (!confirmed) return;

  const payload = await fetchJson(`/api/workspaces/${state.activeWorkspaceId}`, {
    method: "DELETE",
  });
  state.workspaces = payload.workspaces || [];
  state.runtime = payload.runtime || {};
  state.activeWorkspaceId = payload.current_workspace_id;
  state.current = payload.current;
  state.pendingTurn = null;
  resetStreamingDomRefs();
  renderAll();
}

async function uploadSkills(fileList) {
  if (!state.activeWorkspaceId) return;
  const files = Array.from(fileList || []);
  if (!files.length) return;

  const formData = new FormData();
  files.forEach((file) => {
    formData.append("files", file);
    formData.append("paths", file.webkitRelativePath || file.name);
  });

  setBusy(true);
  try {
    state.current = await fetchJson(
      `/api/workspaces/${state.activeWorkspaceId}/skills/upload`,
      { method: "POST", body: formData }
    );
    state.workspaces = await fetchJson("/api/workspaces").then((data) => data.workspaces || []);
    els.skillFiles.value = "";
    els.skillFolderFiles.value = "";
    renderAll();
  } finally {
    setBusy(false);
  }
}

async function deleteSkill(skillId) {
  if (!state.activeWorkspaceId || state.busy) return;
  const confirmed = window.confirm(`确认删除 skill “${skillId}”吗？`);
  if (!confirmed) return;
  state.pendingTurn = null;
  resetStreamingDomRefs();
  state.current = await fetchJson(
    `/api/workspaces/${state.activeWorkspaceId}/skills/${encodeURIComponent(skillId)}`,
    { method: "DELETE" }
  );
  renderAll();
}

async function addTool(event) {
  event.preventDefault();
  if (!state.activeWorkspaceId || state.busy) return;
  const name = els.toolNameInput.value.trim();
  if (!name) return;

  setBusy(true);
  try {
    state.current = await fetchJson(`/api/workspaces/${state.activeWorkspaceId}/tools`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name }),
    });
    els.toolNameInput.value = "";
    renderAll();
  } finally {
    setBusy(false);
  }
}

async function deleteTool(toolName) {
  if (!state.activeWorkspaceId || state.busy) return;
  const confirmed = window.confirm(`确认删除 tool “${toolName}”吗？`);
  if (!confirmed) return;

  setBusy(true);
  try {
    state.current = await fetchJson(
      `/api/workspaces/${state.activeWorkspaceId}/tools/${encodeURIComponent(toolName)}`,
      { method: "DELETE" }
    );
    renderAll();
  } finally {
    setBusy(false);
  }
}

async function clearContext() {
  if (!state.activeWorkspaceId) return;
  state.pendingTurn = null;
  resetStreamingDomRefs();
  state.current = await fetchJson(
    `/api/workspaces/${state.activeWorkspaceId}/context/clear`,
    { method: "POST" }
  );
  renderAll();
}

async function sendChat(event) {
  event.preventDefault();
  if (!state.activeWorkspaceId || state.busy) return;

  const message = els.chatInput.value.trim();
  if (!message) return;

  const mode = currentMode();
  const forcedSkillId = mode === "forced" ? els.forcedSkillSelect.value : null;

  state.pendingTurn = {
    user_message: message,
    assistant_message: "",
    trace: [],
  };
  resetStreamingDomRefs();
  els.chatInput.value = "";
  els.chatInput.style.height = "auto";
  renderAll();
  setBusy(true);

  const response = await fetch(
    `/api/workspaces/${state.activeWorkspaceId}/chat/stream`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        message,
        mode,
        forced_skill_id: forcedSkillId || null,
      }),
    }
  );

  if (!response.ok || !response.body) {
    setBusy(false);
    throw new Error(`chat stream failed: ${response.status}`);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let finalized = false;
  let hadError = false;

  while (!finalized) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    buffer = parseSse(buffer, (eventName, payload) => {
      if (eventName === "token") {
        state.pendingTurn.assistant_message += payload.delta || "";
        updatePendingAssistantMessage();
      }
      if (eventName === "trace") {
        state.pendingTurn.trace.push(payload);
        renderHeader();
        renderTrace();
      }
      if (eventName === "done") {
        state.current.session = payload.session;
        state.pendingTurn = null;
        finalized = true;
      }
      if (eventName === "error") {
        state.pendingTurn.assistant_message = payload.message || "Runtime error";
        updatePendingAssistantMessage();
        hadError = true;
        finalized = true;
      }
    });
  }

  if (state.pendingTurn && finalized && !hadError) {
    state.pendingTurn = null;
    resetStreamingDomRefs();
  }
  setBusy(false);
  renderAll();
}

/* ---- event bindings ---- */

document.querySelectorAll('input[name="mode"]').forEach((node) => {
  node.addEventListener("change", () => {
    els.forcedSkillSelect.disabled = currentMode() !== "forced" || state.busy;
  });
});

els.createWorkspaceButton.addEventListener("click", createWorkspace);
els.workspaceSelect.addEventListener("change", (event) => {
  const workspaceId = event.target.value;
  if (!workspaceId || workspaceId === state.activeWorkspaceId) return;
  loadWorkspace(workspaceId).catch((error) => {
    window.alert(error.message);
    renderWorkspaces();
  });
});
els.deleteWorkspaceButton.addEventListener("click", () => {
  deleteCurrentWorkspace().catch((error) => {
    window.alert(error.message);
  });
});

els.skillFiles.addEventListener("change", () => {
  uploadSkills(els.skillFiles.files).catch((error) => window.alert(error.message));
});

els.skillFolderFiles.addEventListener("change", () => {
  uploadSkills(els.skillFolderFiles.files).catch((error) => window.alert(error.message));
});

els.clearContextButton.addEventListener("click", clearContext);
if (els.toolForm) {
  els.toolForm.addEventListener("submit", (event) => {
    addTool(event).catch((error) => window.alert(error.message));
  });
}

els.chatForm.addEventListener("submit", (event) => {
  sendChat(event).catch((error) => {
    if (!state.pendingTurn) {
      state.pendingTurn = {
        user_message: "",
        assistant_message: "",
        trace: [],
      };
    }
    state.pendingTurn.assistant_message = error.message;
    updatePendingAssistantMessage();
    setBusy(false);
    renderAll();
  });
});

els.chatInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    els.chatForm.dispatchEvent(new Event("submit", { cancelable: true }));
  }
});

/* ---- init ---- */

bootstrap().catch((error) => {
  els.chatLog.innerHTML = `<div class="chat-log-empty"><p style="color:var(--danger)">${escapeHtml(error.message)}</p></div>`;
});
