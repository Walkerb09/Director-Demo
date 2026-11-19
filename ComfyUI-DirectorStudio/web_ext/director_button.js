// Adds a "Director" entry to ComfyUI that opens /director. Defensive: works across frontend versions.
import { app } from "../../scripts/app.js";
app.registerExtension({
  name: "DirectorStudio.OpenButton",
  async setup() {
    const open = () => window.open(new URL("director", window.location.href.replace(/[^/]*$/, "")).href, "_blank");
    try {
      if (app.extensionManager?.registerSidebarTab) {
        app.extensionManager.registerSidebarTab({ id: "director-studio", icon: "pi pi-video", title: "Director",
          tooltip: "Open Director Studio", type: "custom",
          render: (el) => { el.innerHTML = ""; const b = document.createElement("button"); b.textContent = "🎬 Open Director Studio";
            b.style.cssText = "margin:12px;padding:10px 14px;border-radius:8px;cursor:pointer"; b.onclick = open; el.appendChild(b); } });
        return;
      }
    } catch (e) { console.warn("[DirectorStudio] sidebar tab unavailable", e); }
    const b = document.createElement("button");
    b.textContent = "🎬 Director"; b.title = "Open Director Studio";
    b.style.cssText = "position:fixed;right:12px;bottom:12px;z-index:9999;padding:8px 12px;border-radius:8px;cursor:pointer";
    b.onclick = open; document.body.appendChild(b);
  },
});
