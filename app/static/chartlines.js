// Lines between chart boxes: each box names the boxes above it it comes from (data-from), and gets an elbow line
// from each of their bottoms to its top. Redrawn whenever the chart's size changes (photos loading, resizing).
(() => {
  for (const chart of document.querySelectorAll(".vped")) {
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.classList.add("chart-lines");
    svg.setAttribute("aria-hidden", "true");
    chart.prepend(svg);
    const draw = () => {
      const base = chart.getBoundingClientRect();
      const at = (el) => {
        const r = el.getBoundingClientRect();
        return { x: r.left + r.width / 2 - base.left + chart.scrollLeft, top: r.top - base.top, bottom: r.bottom - base.top };
      };
      const keyed = new Map([...chart.querySelectorAll("[data-key]")].map((el) => [el.dataset.key, el]));
      const paths = [];
      for (const el of keyed.values()) {
        const to = at(el);
        for (const key of (el.dataset.from || "").split(" ")) {
          const up = keyed.get(key);
          if (!up) continue;
          const from = at(up);
          const mid = Math.round((from.bottom + to.top) / 2);
          paths.push(`M${from.x},${from.bottom}V${mid}H${to.x}V${to.top}`);
        }
      }
      svg.setAttribute("width", chart.scrollWidth);
      svg.setAttribute("height", chart.scrollHeight);
      svg.innerHTML = paths.map((d) => `<path d="${d}"/>`).join("");
    };
    new ResizeObserver(draw).observe(chart);
    for (const row of chart.querySelectorAll(".vped-row")) new ResizeObserver(draw).observe(row);
  }
})();
