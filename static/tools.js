const state = {
  workspaces: [],
  activeWorkspaceId: null,
  current: null,
  runtime: null,
  busy: false,
};

const els = {
  workspaceSelect: document.getElementById("workspaceSelect"),
  createWorkspaceButton: document.getElementById("createWorkspaceButton"),
  deleteWorkspaceButton: document.getElementById("deleteWorkspaceButton"),
  runtimeStatus: document.getElementById("runtimeStatus"),
  toolCount: document.getElementById("toolCount"),
  missingCount: document.getElementById("missingCount"),
  toolForm: document.getElementById("toolForm"),
  toolNameInput: document.getElementById("toolNameInput"),
  addToolButton: document.getElementById("addToolButton"),
  toolsList: document.getElementById("toolsList"),
  toolHints: document.getElementById("toolHints"),
  syncGoogleMapsButton: document.getElementById("syncGoogleMapsButton"),
  syncYelpButton: document.getElementById("syncYelpButton"),
  syncAllButton: document.getElementById("syncAllButton"),
};

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

async function fetchJson(url, options) {
  const response = await fetch(url, options);
  if (!response.ok) {
    const message = await response.text();
    throw new Error(message || `Request failed: ${response.status}`);
  }
  return response.json();
}

function setBusy(nextBusy) {
  state.busy = nextBusy;
  els.workspaceSelect.disabled = nextBusy;
  els.createWorkspaceButton.disabled = nextBusy;
  els.deleteWorkspaceButton.disabled = nextBusy;
  els.toolNameInput.disabled = nextBusy;
  els.addToolButton.disabled = nextBusy;
  els.syncGoogleMapsButton.disabled = nextBusy;
  els.syncYelpButton.disabled = nextBusy;
  els.syncAllButton.disabled = nextBusy;
}

function renderHeader() {
  const runtime = state.current?.runtime || state.runtime || {};
  if (runtime.default_model) {
    els.runtimeStatus.textContent = runtime.default_model;
  } else if (runtime.claude_cli_path) {
    els.runtimeStatus.textContent = "Claude CLI";
  } else {
    els.runtimeStatus.textContent = "未配置";
  }
}

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

function renderTools() {
  const tools = state.current?.tools || [];
  const missing = state.current?.unregistered_declared_tools || [];
  els.toolsList.innerHTML = "";
  els.toolHints.innerHTML = "";
  els.toolCount.textContent = `${tools.length}`;
  els.missingCount.textContent = `${missing.length}`;

  if (!tools.length) {
    els.toolsList.innerHTML = '<div class="tool-list-empty">当前 workspace 还没有注册运行时工具</div>';
  } else {
    tools.forEach((tool) => {
      const row = document.createElement("div");
      row.className = "tool-row";
      const linkedSkills = (tool.declared_by_skills || []).join(", ");
      row.innerHTML = `
        <div class="tool-row-main">
          <div class="tool-row-name">${escapeHtml(tool.name)}</div>
          <div class="tool-row-meta">${escapeHtml(tool.execution_mode || "stub")}${linkedSkills ? ` · used by ${escapeHtml(linkedSkills)}` : ""}</div>
          <div class="tool-row-meta">${escapeHtml(tool.description || tool.source || "")}</div>
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
        deleteTool(tool.name).catch((error) => window.alert(error.message));
      });
      els.toolsList.appendChild(row);
    });
  }

  if (!missing.length) {
    els.toolHints.innerHTML = '<div class="tool-list-empty">当前所有 skill 声明的工具都已经注册了</div>';
    return;
  }

  missing.forEach((tool) => {
    const row = document.createElement("div");
    row.className = "tool-hint tool-hint-row";
    row.innerHTML = `
      <div class="tool-hint-main">
        <span class="tool-hint-name">${escapeHtml(tool.name)}</span>
        <span class="tool-hint-meta">${escapeHtml((tool.declared_by_skills || []).join(", "))}</span>
      </div>
      <button class="mini-btn tool-register-btn" type="button">注册</button>
    `;
    row.querySelector(".tool-register-btn").addEventListener("click", () => {
      addToolByName(tool.name).catch((error) => window.alert(error.message));
    });
    els.toolHints.appendChild(row);
  });
}

function renderAll() {
  renderWorkspaces();
  renderHeader();
  renderTools();
}

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
  state.current = await fetchJson(`/api/workspaces/${workspaceId}`);
  renderAll();
}

async function refreshWorkspaceList() {
  const payload = await fetchJson("/api/workspaces");
  state.workspaces = payload.workspaces || [];
  state.runtime = payload.runtime || state.runtime;
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
  await refreshWorkspaceList();
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
  renderAll();
}

async function addToolByName(name) {
  if (!state.activeWorkspaceId || state.busy) return;
  const toolName = String(name || "").trim();
  if (!toolName) return;

  setBusy(true);
  try {
    state.current = await fetchJson(`/api/workspaces/${state.activeWorkspaceId}/tools`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: toolName }),
    });
    await refreshWorkspaceList();
    els.toolNameInput.value = "";
    renderAll();
  } finally {
    setBusy(false);
  }
}

async function addTool(event) {
  event.preventDefault();
  await addToolByName(els.toolNameInput.value);
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

async function syncProjectTools(presets) {
  if (!state.activeWorkspaceId || state.busy) return;
  setBusy(true);
  try {
    state.current = await fetchJson(
      `/api/workspaces/${state.activeWorkspaceId}/tools/project-sync`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ presets }),
      }
    );
    renderAll();
  } finally {
    setBusy(false);
  }
}

els.workspaceSelect.addEventListener("change", (event) => {
  const workspaceId = event.target.value;
  if (!workspaceId || workspaceId === state.activeWorkspaceId) return;
  loadWorkspace(workspaceId).catch((error) => window.alert(error.message));
});

els.createWorkspaceButton.addEventListener("click", () => {
  createWorkspace().catch((error) => window.alert(error.message));
});

els.deleteWorkspaceButton.addEventListener("click", () => {
  deleteCurrentWorkspace().catch((error) => window.alert(error.message));
});

els.toolForm.addEventListener("submit", (event) => {
  addTool(event).catch((error) => window.alert(error.message));
});

els.syncGoogleMapsButton.addEventListener("click", () => {
  syncProjectTools(["google_maps"]).catch((error) => window.alert(error.message));
});

els.syncYelpButton.addEventListener("click", () => {
  syncProjectTools(["yelp"]).catch((error) => window.alert(error.message));
});

els.syncAllButton.addEventListener("click", () => {
  syncProjectTools(null).catch((error) => window.alert(error.message));
});

bootstrap().catch((error) => {
  els.toolsList.innerHTML = `<div class="tool-list-empty" style="color:var(--danger)">${escapeHtml(error.message)}</div>`;
});
