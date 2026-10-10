(() => {
  function $(sel) {
    return document.querySelector(sel);
  }

  function fmtPct(n) {
    return `${Number(n).toFixed(2)}%`;
  }

  function calcMdf(betPct) {
    const frac = Math.max(0, Number(betPct) || 0) / 100;
    if (frac === 0) return 100;
    return 100 / (1 + frac);
  }

  function updateMdf() {
    const betPct = Number($("#mdfBetPct").value) || 0;
    const mdf = calcMdf(betPct);
    $("#mdfValue").textContent = fmtPct(mdf);
    const pot = 100;
    const bet = (pot * betPct) / 100;
    $("#mdfExplain").textContent =
      `底池 ${pot}，对手下注 ${bet.toFixed(betPct % 1 ? 1 : 0)}（${betPct}%）→ 需防守 ${mdf.toFixed(1)}% 的范围。`;
  }

  function initTools() {
    const mdfInput = $("#mdfBetPct");
    if (!mdfInput) return;

    mdfInput.addEventListener("input", updateMdf);
    $("#mdfPresets").addEventListener("click", (ev) => {
      const btn = ev.target.closest("[data-pct]");
      if (!btn) return;
      mdfInput.value = btn.dataset.pct;
      updateMdf();
    });
    updateMdf();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initTools);
  } else {
    initTools();
  }
})();
