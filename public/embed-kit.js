// Copy-to-clipboard for the embed-kit snippets. External file because the site
// CSP is script-src 'self' (no inline script). Progressive: the <pre> snippets
// are selectable by hand if JS is off.
document.querySelectorAll(".copy").forEach((btn) => {
  btn.addEventListener("click", async () => {
    const code = btn.getAttribute("data-copy") || "";
    try {
      await navigator.clipboard.writeText(code);
      const prev = btn.textContent;
      btn.textContent = "Copied";
      btn.classList.add("done");
      setTimeout(() => {
        btn.textContent = prev;
        btn.classList.remove("done");
      }, 1600);
    } catch (e) {
      btn.textContent = "Press Ctrl+C";
    }
  });
});
