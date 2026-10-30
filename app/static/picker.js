// Person picker: typing a name in a .picker input with data-find lists matches under it; picking one puts that
// person's ID in the box. Plain typing of an ID also works. A plain list, not a <datalist>: Android Chrome matches a
// datalist only against option values (the IDs), so typed names showed nothing there. Not the header search, which
// has data-find too but no list.
document.querySelectorAll(".picker input[data-find]").forEach((input) => {
  const list = document.getElementById(input.id + "-list");
  const label = document.getElementById(input.id + "-name");
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
        const b = Object.assign(document.createElement("button"), { type: "button", textContent: p.label });
        b.addEventListener("click", () => {
          input.value = p.id;
          if (label) label.textContent = p.label;
          close();
          input.focus();
        });
        const li = document.createElement("li");
        li.append(b);
        return li;
      }));
      list.hidden = !people.length;
      input.setAttribute("aria-expanded", String(!list.hidden));
      const hit = people.find((p) => p.id === q.toUpperCase());
      if (label && hit) label.textContent = hit.label;
    }, 250);
  });
  input.addEventListener("keydown", (e) => {
    if (e.key === "ArrowDown" && !list.hidden) { e.preventDefault(); list.querySelector("button").focus(); }
    if (e.key === "Escape") close();
  });
  list.addEventListener("keydown", (e) => {
    const items = [...list.querySelectorAll("button")], i = items.indexOf(document.activeElement);
    if (e.key === "ArrowDown") { e.preventDefault(); items[i + 1]?.focus(); }
    if (e.key === "ArrowUp") { e.preventDefault(); (items[i - 1] || input).focus(); }
    if (e.key === "Escape") { close(); input.focus(); }
  });
  document.addEventListener("click", (e) => { if (!input.parentElement.contains(e.target)) close(); });
});
