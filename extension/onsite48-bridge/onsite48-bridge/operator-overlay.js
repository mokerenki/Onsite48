(() => {
  if (globalThis.__onsite48Overlay) {
    globalThis.__onsite48Overlay.ping?.();
    return;
  }

  let host = null;
  let root = null;
  let state = { phase: "idle" };

  globalThis.__onsite48Overlay = {
    ping() {
      // Already loaded; optionally re-show the host if needed
      if (host && !host.isConnected) {
        document.documentElement.append(host);
      }
    },
    render(next) {
      // exposed for external calls if needed
      if (typeof render === "function") render(next);
    },
  };

  function messageFor(action) {
    if (action === "stop") return "operatorStop";
    if (action === "pause") return "operatorPause";
    if (action === "resume") return "operatorResume";
    return "operatorTakeover";
  }

  function ensureHost() {
    if (host?.isConnected) return host;
    host = document.createElement("div");
    host.id = "onsite48-bridge-operator-root";
    host.style.cssText = "position:fixed;inset:0;pointer-events:none;z-index:2147483646;";
    root = host.attachShadow({ mode: "closed" });
    root.innerHTML = `
      <style>
        :host{all:initial}.shell{position:fixed;right:18px;bottom:18px;display:flex;align-items:center;gap:10px;max-width:470px;padding:11px 12px;border:1px solid rgba(136,151,225,.48);border-radius:14px;background:rgba(20,28,53,.97);box-shadow:0 18px 50px rgba(7,12,31,.28);color:#fff;font-family:Inter,ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;pointer-events:auto}.pulse{width:10px;height:10px;border-radius:999px;background:#7c91ff;box-shadow:0 0 0 5px rgba(124,145,255,.16);animation:pulse 1.4s infinite}.copy{min-width:0;flex:1}.eyebrow{margin:0 0 2px;color:#aeb9ff;font-size:10px;font-weight:800;letter-spacing:.08em;text-transform:uppercase}.title{overflow:hidden;margin:0;color:#fff;font-size:12px;font-weight:750;text-overflow:ellipsis;white-space:nowrap}.detail{overflow:hidden;margin:3px 0 0;color:#d4dafc;font-size:10px;line-height:1.35;text-overflow:ellipsis;white-space:nowrap}.meta{margin:4px 0 0;color:#9aa7dc;font-size:9px;line-height:1.2}.goal{margin:4px 0 0;color:#f0f1ff;font-size:10px;line-height:1.35;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.verification-status{margin:4px 0 0;font-size:10px;font-weight:700;line-height:1.2}.verification-status.verified{color:#4ade80}.verification-status.failed{color:#ff8a8a}.actions{display:flex;gap:6px}.button{border:1px solid rgba(255,255,255,.2);border-radius:8px;padding:6px 8px;background:rgba(255,255,255,.1);color:#fff;font:700 10px Inter,ui-sans-serif,system-ui;cursor:pointer}.button:hover{background:rgba(255,255,255,.2)}.button:disabled{opacity:.55;cursor:default}.button.danger{border-color:rgba(255,130,130,.7);color:#ffd0d0}@keyframes pulse{0%,100%{transform:scale(1);opacity:1}50%{transform:scale(.72);opacity:.55}}
      </style>
      <aside class="shell" role="status" aria-live="polite">
        <i class="pulse"></i>
        <div class="copy"><p class="eyebrow">Onsite48 Onsite48 Browser</p><p class="title"></p><p class="detail"></p><p class="meta"></p><p class="goal"></p><p class="verification-status"></p></div>
        <div class="actions"><button class="button" data-action="pause">Pause</button><button class="button" data-action="takeover">Take over</button><button class="button danger" data-action="stop">Stop</button></div>
      </aside>`;
    root.querySelectorAll("button").forEach((button) => {
      button.addEventListener("click", () => chrome.runtime.sendMessage({ type: messageFor(button.dataset.action) }));
    });
    document.documentElement.append(host);
    return host;
  }

  function render(next) {
    state = { ...state, ...next };
    if (state.phase === "idle") {
      if (host?.isConnected) host.remove();
      return;
    }
    ensureHost();
    if (!root) return;
    const paused = state.phase === "paused";
    const done = state.phase === "done";
    const title = root.querySelector(".title");
    const detail = root.querySelector(".detail");
    const meta = root.querySelector(".meta");
    const goal = root.querySelector(".goal");
    const verificationStatus = root.querySelector(".verification-status");
    const pause = root.querySelector("[data-action='pause']");
    const takeover = root.querySelector("[data-action='takeover']");
    title.textContent = state.title || (done ? "Agent completed this browser action" : paused ? "Agent is paused in this tab" : "Agent working");
    detail.textContent = state.detail || (done ? "The result was returned to Onsite48." : paused ? "You control this tab. Resume only when you want the agent to process the next approved action." : "You can pause, take over, or stop this action at any time.");
    const commandId = state.command_id || state.lifecycle?.command_id || null;
    meta.textContent = commandId ? `Command ${String(commandId).slice(0, 12)} · ${state.transport || state.lifecycle?.transport || "local"}` : "Local browser execution boundary";
    
    if (goal) {
      const goalText = state.context?.goal || state.goal || "";
      goal.textContent = goalText ? `Goal: ${String(goalText).slice(0, 100)}` : "";
      goal.style.display = goalText ? "" : "none";
    }
    
    if (verificationStatus) {
      verificationStatus.className = "verification-status";
      if (state.verification?.status === "verified") {
        verificationStatus.textContent = "✅ Verified";
        verificationStatus.classList.add("verified");
      } else if (state.verification?.status === "failed") {
        verificationStatus.textContent = "⚠️ Verification Failed";
        verificationStatus.classList.add("failed");
      } else {
        verificationStatus.textContent = "";
      }
    }
    
    pause.textContent = paused ? "Paused" : "Pause";
    pause.disabled = paused || done;
    takeover.dataset.action = paused ? "resume" : "takeover";
    takeover.textContent = paused ? "Resume" : "Take over";
    takeover.disabled = done;
  }

  chrome.runtime.onMessage.addListener((message) => {
    if (message?.type === "operatorState") render(message.state || {});
  });
})();