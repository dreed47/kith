// Header search: lists people while typing; picking one opens their page, Enter does a full search. A plain list,
// not a <datalist>, for the same reason as picker.js: Android Chrome only matched a datalist against the IDs.
document.querySelectorAll("input[data-go]").forEach((input) => {
  const list = document.getElementById(input.getAttribute("aria-controls"));
  let timer;
  const close = () => { list.hidden = true; input.setAttribute("aria-expanded", "false"); };
  input.addEventListener("input", () => {
    clearTimeout(timer);
    const q = input.value.trim();
    if (q.length < 2) return close();
    timer = setTimeout(async () => {
      const res = await fetch(input.dataset.find + "?q=" + encodeURIComponent(q));
      if (!res.ok || input.value.trim() !== q) return;  // typed on since: a newer lookup is coming
      const people = await res.json();
      list.replaceChildren(...people.map((p) => {
        const a = Object.assign(document.createElement("a"), {
          href: input.dataset.go.replace("ID", encodeURIComponent(p.id)), textContent: p.label });
        const li = document.createElement("li");
        li.append(a);
        return li;
      }));
      list.hidden = !people.length;
      input.setAttribute("aria-expanded", String(!list.hidden));
    }, 250);
  });
  input.addEventListener("keydown", (e) => {
    if (e.key === "ArrowDown" && !list.hidden) { e.preventDefault(); list.querySelector("a").focus(); }
    if (e.key === "Escape") close();
  });
  list.addEventListener("keydown", (e) => {
    const items = [...list.querySelectorAll("a")], i = items.indexOf(document.activeElement);
    if (e.key === "ArrowDown") { e.preventDefault(); items[i + 1]?.focus(); }
    if (e.key === "ArrowUp") { e.preventDefault(); (items[i - 1] || input).focus(); }
    if (e.key === "Escape") { close(); input.focus(); }
  });
  document.addEventListener("click", (e) => { if (!input.form.contains(e.target)) close(); });
});
