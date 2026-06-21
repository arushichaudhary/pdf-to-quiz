// ---------- Toast ----------
function showToast(message, type = "info") {
  const colors = { info: "#5B8DBF", success: "#5BAA7A", error: "#D9685C" };
  const el = document.createElement("div");
  el.textContent = message;
  el.style.cssText = `
    position: fixed; bottom: 24px; right: 24px; z-index: 9999;
    background: ${colors[type] || colors.info}; color: white; font-weight: 600; font-size: 0.875rem;
    padding: 0.85rem 1.25rem; border-radius: 0.85rem; box-shadow: 0 12px 32px -8px rgba(0,0,0,0.25);
    opacity: 0; transform: translateY(10px); transition: all .25s ease; max-width: 360px;
  `;
  document.body.appendChild(el);
  requestAnimationFrame(() => { el.style.opacity = "1"; el.style.transform = "translateY(0)"; });
  setTimeout(() => {
    el.style.opacity = "0"; el.style.transform = "translateY(10px)";
    setTimeout(() => el.remove(), 300);
  }, 3400);
}

// ---------- Generic AI-action button wiring ----------
// Buttons with data-ai-action="<url>" trigger a POST, show a spinner, and either
// reload or run a callback named in data-on-success.
document.addEventListener("click", async (e) => {
  const btn = e.target.closest("[data-ai-action]");
  if (!btn) return;
  const url = btn.getAttribute("data-ai-action");
  const method = btn.getAttribute("data-method") || "POST";
  const body = btn.getAttribute("data-body");

  const original = btn.innerHTML;
  btn.disabled = true;
  btn.innerHTML = `<span class="inline-flex items-center gap-2"><svg class="animate-spin w-4 h-4" viewBox="0 0 24 24" fill="none"><circle cx="12" cy="12" r="10" stroke="currentColor" stroke-width="3" opacity="0.25"/><path d="M12 2a10 10 0 0 1 10 10" stroke="currentColor" stroke-width="3" stroke-linecap="round"/></svg> Working…</span>`;

  try {
    const resp = await fetch(url, {
      method,
      headers: body ? { "Content-Type": "application/json" } : {},
      body: body || undefined,
    });
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.error || "Something went wrong.");

    const onSuccess = btn.getAttribute("data-on-success");
    if (onSuccess && typeof window[onSuccess] === "function") {
      window[onSuccess](data, btn);
    } else if (data.redirect) {
      window.location.href = data.redirect;
    } else {
      showToast("Done!", "success");
      setTimeout(() => window.location.reload(), 600);
    }
  } catch (err) {
    showToast(err.message, "error");
    btn.disabled = false;
    btn.innerHTML = original;
  }
});

lucide.createIcons && lucide.createIcons();
