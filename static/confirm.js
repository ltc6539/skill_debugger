(function () {
  let refs = null;
  let pendingResolve = null;
  let previousFocus = null;

  function ensureDialog() {
    if (refs) return refs;

    const root = document.createElement("div");
    root.className = "confirm-modal hidden";
    root.setAttribute("aria-hidden", "true");
    root.innerHTML = `
      <div class="confirm-backdrop" data-confirm-close="backdrop"></div>
      <section class="confirm-panel" role="dialog" aria-modal="true" aria-labelledby="confirmDialogTitle">
        <div class="confirm-header">
          <div class="confirm-header-copy">
            <div id="confirmDialogTitle" class="confirm-title">确认删除</div>
            <div class="confirm-message"></div>
          </div>
          <button class="icon-btn confirm-close-btn" type="button" title="关闭确认弹窗" data-confirm-close="button">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round">
              <line x1="18" y1="6" x2="6" y2="18"></line>
              <line x1="6" y1="6" x2="18" y2="18"></line>
            </svg>
          </button>
        </div>
        <div class="confirm-actions">
          <button class="sidebar-btn confirm-cancel-btn" type="button">取消</button>
          <button class="sidebar-btn danger-btn confirm-submit-btn" type="button">确认删除</button>
        </div>
      </section>
    `;
    document.body.appendChild(root);

    refs = {
      root,
      title: root.querySelector(".confirm-title"),
      message: root.querySelector(".confirm-message"),
      cancelButton: root.querySelector(".confirm-cancel-btn"),
      submitButton: root.querySelector(".confirm-submit-btn"),
    };

    root.addEventListener("click", (event) => {
      const target = event.target;
      if (!(target instanceof HTMLElement)) return;
      if (target.dataset.confirmClose) {
        closeDialog(false);
      }
    });

    refs.cancelButton.addEventListener("click", () => {
      closeDialog(false);
    });

    refs.submitButton.addEventListener("click", () => {
      closeDialog(true);
    });

    return refs;
  }

  function onKeyDown(event) {
    if (!refs || refs.root.classList.contains("hidden")) return;
    if (event.key === "Escape") {
      event.preventDefault();
      closeDialog(false);
      return;
    }
    if (event.key === "Enter") {
      const active = document.activeElement;
      if (active instanceof HTMLButtonElement) {
        event.preventDefault();
        closeDialog(true);
      }
    }
  }

  function closeDialog(result) {
    if (!refs || refs.root.classList.contains("hidden")) return;
    refs.root.classList.add("hidden");
    refs.root.setAttribute("aria-hidden", "true");
    document.removeEventListener("keydown", onKeyDown);
    const resolve = pendingResolve;
    pendingResolve = null;
    const focusTarget = previousFocus;
    previousFocus = null;
    if (focusTarget instanceof HTMLElement) {
      focusTarget.focus();
    }
    if (typeof resolve === "function") {
      resolve(result);
    }
  }

  async function confirmDestructiveAction(options) {
    const dialog = ensureDialog();
    if (typeof pendingResolve === "function") {
      pendingResolve(false);
      pendingResolve = null;
    }

    const title = String(options?.title || "确认删除");
    const message = String(options?.message || "");
    const confirmLabel = String(options?.confirmLabel || "确认删除");

    previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;

    dialog.title.textContent = title;
    dialog.message.textContent = message;
    dialog.submitButton.textContent = confirmLabel;

    dialog.root.classList.remove("hidden");
    dialog.root.setAttribute("aria-hidden", "false");
    document.addEventListener("keydown", onKeyDown);

    window.setTimeout(() => {
      dialog.submitButton.focus();
    }, 0);

    return new Promise((resolve) => {
      pendingResolve = resolve;
    });
  }

  window.SkillDebuggerConfirm = {
    confirmDestructiveAction,
  };
})();
