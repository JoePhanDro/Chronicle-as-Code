// Turns ```mermaid code blocks into diagrams. Mermaid (about 5 MB) is only downloaded when a block is on the page.
(() => {
  const MERMAID_SRC = "https://cdn.jsdelivr.net/npm/mermaid@12.1.0/dist/mermaid.min.js";
  const MERMAID_SRI = "sha384-EbBpjO7rlR6eqZEcG7GaPpyk9H9WrMyPWX4d3KvPYltgt8Z8l0z6R56B1qP40pR4";
  let loading = null;
  let counter = 0;

  // The diagram follows the interface theme: the chosen one, otherwise the system setting (dark is the default).
  function isLight() {
    const chosen = document.documentElement.dataset.theme;
    return chosen ? chosen === "light" : window.matchMedia("(prefers-color-scheme: light)").matches;
  }

  function loadMermaid() {
    if (window.mermaid) return Promise.resolve(window.mermaid);
    if (!loading) {
      loading = new Promise((resolve, reject) => {
        const script = document.createElement("script");
        script.src = MERMAID_SRC;
        script.integrity = MERMAID_SRI;
        script.crossOrigin = "anonymous";
        script.onload = () => {
          // strict: labels are sanitized by mermaid, and no click handlers or HTML labels are allowed.
          window.mermaid.initialize({ startOnLoad: false, securityLevel: "strict", theme: isLight() ? "default" : "dark" });
          resolve(window.mermaid);
        };
        script.onerror = () => reject(new Error("Mermaid konnte nicht geladen werden"));
        document.head.append(script);
      });
    }
    return loading;
  }

  // Replaces each mermaid block below `root` with its SVG. A block that does not parse stays as source.
  window.renderDiagrams = async (root) => {
    const blocks = root.querySelectorAll("pre > code.language-mermaid");
    if (!blocks.length) return;
    const mermaid = await loadMermaid();
    for (const code of blocks) {
      const pre = code.parentElement;
      try {
        const { svg } = await mermaid.render(`diagram-${++counter}`, code.textContent);
        const figure = document.createElement("div");
        figure.className = "mermaid-diagram";
        figure.style.overflowX = "auto";
        figure.style.margin = "1rem 0";
        figure.innerHTML = svg;
        pre.replaceWith(figure);
      } catch (error) {
        pre.title = "Diagramm konnte nicht gerendert werden";
      }
    }
  };

  document.addEventListener("DOMContentLoaded", () => window.renderDiagrams(document));
})();
