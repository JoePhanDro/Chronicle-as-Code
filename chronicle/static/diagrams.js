// Turns ```mermaid code blocks into diagrams. Mermaid (about 5 MB) is only downloaded when a block is on the page.
(() => {
  const MERMAID_SRC = "https://cdn.jsdelivr.net/npm/mermaid@12.1.0/dist/mermaid.min.js";
  const MERMAID_SRI = "sha384-EbBpjO7rlR6eqZEcG7GaPpyk9H9WrMyPWX4d3KvPYltgt8Z8l0z6R56B1qP40pR4";
  let loading = null;
  let counter = 0;
  let configured = null; // the theme mermaid was last initialised with

  // Mermaid's own themes bring their own palette (purple fills in light, grey in dark). These values are the
  // interface's slate scale and sky accent (DESIGN.md), so a diagram reads as part of the page in both themes.
  const PALETTES = {
    dark: { surface: "#1e293b", border: "#475569", text: "#e2e8f0", line: "#94a3b8", cluster: "#0f172a", accent: "#38bdf8" },
    light: { surface: "#e2e8f0", border: "#94a3b8", text: "#0f172a", line: "#475569", cluster: "#f1f5f9", accent: "#0284c7" },
  };

  function themeName() { return isLight() ? "light" : "dark"; }

  function configure(mermaid) {
    const name = themeName();
    if (configured === name) return;
    const p = PALETTES[name];
    // strict: labels are sanitized by mermaid, and no click handlers or HTML labels are allowed.
    mermaid.initialize({
      startOnLoad: false, securityLevel: "strict", theme: "base",
      themeVariables: {
        darkMode: name === "dark", fontFamily: "ui-sans-serif, system-ui, sans-serif", fontSize: "14px",
        background: "transparent", primaryColor: p.surface, primaryBorderColor: p.border, primaryTextColor: p.text,
        secondaryColor: p.cluster, tertiaryColor: p.cluster, lineColor: p.line, textColor: p.text, mainBkg: p.surface,
        nodeBorder: p.border, clusterBkg: p.cluster, clusterBorder: p.border, edgeLabelBackground: p.cluster,
        noteBkgColor: p.cluster, noteTextColor: p.text, noteBorderColor: p.accent, titleColor: p.text,
      },
    });
    configured = name;
  }

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
        script.onload = () => resolve(window.mermaid);
        script.onerror = () => reject(new Error("Mermaid konnte nicht geladen werden"));
        document.head.append(script);
      });
    }
    return loading;
  }

  // Draws `source` into `figure`. The SVG keeps a readable width and scrolls sideways in a narrow column, instead of shrinking its text.
  async function draw(mermaid, figure, source) {
    const { svg } = await mermaid.render(`diagram-${++counter}`, source);
    figure.innerHTML = svg;
    const drawn = figure.querySelector("svg");
    const width = drawn && drawn.viewBox.baseVal ? drawn.viewBox.baseVal.width : 0;
    if (drawn && width) drawn.style.minWidth = `${Math.min(width, 640)}px`;
  }

  // Replaces each mermaid block below `root` with its SVG. A block that does not parse stays as source.
  window.renderDiagrams = async (root) => {
    const blocks = root.querySelectorAll("pre > code.language-mermaid");
    if (!blocks.length) return;
    const mermaid = await loadMermaid();
    configure(mermaid);
    for (const code of blocks) {
      const pre = code.parentElement;
      try {
        const figure = document.createElement("div");
        figure.className = "mermaid-diagram";
        figure.dataset.source = code.textContent;
        figure.tabIndex = 0; // a scrollable region has to be reachable by keyboard
        figure.setAttribute("role", "group");
        figure.setAttribute("aria-label", "Diagramm");
        figure.style.overflowX = "auto";
        figure.style.margin = "1rem 0";
        await draw(mermaid, figure, code.textContent);
        pre.replaceWith(figure);
      } catch (error) {
        pre.title = "Diagramm konnte nicht gerendert werden";
      }
    }
  };

  // A theme change redraws the diagrams that are already on the page.
  async function redraw() {
    const figures = document.querySelectorAll(".mermaid-diagram[data-source]");
    if (!figures.length || !window.mermaid) return;
    configure(window.mermaid);
    for (const figure of figures) {
      try { await draw(window.mermaid, figure, figure.dataset.source); } catch (error) { /* the old drawing stays */ }
    }
  }
  new MutationObserver(redraw).observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
  window.matchMedia("(prefers-color-scheme: light)").addEventListener("change", redraw);

  document.addEventListener("DOMContentLoaded", () => window.renderDiagrams(document));
})();
