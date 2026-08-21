// FRRR shell tab switching (plain show/hide). The chat panel stays in the DOM
// while hidden, so SSE rendering and window.__stpp keep working on any tab.
export function initTabs(defaultTab = "leadership") {
  const tabs = [...document.querySelectorAll("#tabbar .tab")];
  const show = (name) => {
    for (const t of tabs) {
      const active = t.dataset.tab === name;
      t.classList.toggle("active", active);
      t.setAttribute("aria-selected", String(active));
      document.getElementById(`panel-${t.dataset.tab}`).hidden = !active;
    }
    if (name === "ai") {
      // scrollHeight is 0 while the panel is hidden, so render.js's
      // scrollChat() can't pin the transcript — re-pin on reveal.
      const chat = document.getElementById("chat");
      chat.scrollTop = chat.scrollHeight;
    }
  };
  tabs.forEach((t) => t.addEventListener("click", () => show(t.dataset.tab)));
  show(defaultTab);
}
