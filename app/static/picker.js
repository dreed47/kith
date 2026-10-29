// Person picker: typing a name in an input with data-find fills its <datalist> with matches.
// Option values are person IDs, so picking one puts the ID in the box; plain typing of an ID also works.
document.querySelectorAll("input[data-find]").forEach((input) => {
  const list = document.getElementById(input.getAttribute("list"));
  const label = document.getElementById(input.id + "-name");
  let timer;
  input.addEventListener("input", () => {
    clearTimeout(timer);
    const q = input.value.trim();
    if (q.length < 2) return;
    timer = setTimeout(async () => {
      const res = await fetch(input.dataset.find + "?q=" + encodeURIComponent(q));
      if (!res.ok) return;
      const people = await res.json();
      list.replaceChildren(...people.map((p) => {
        const o = document.createElement("option");
        o.value = p.id;
        o.label = p.label;
        o.textContent = p.label;
        return o;
      }));
      const hit = people.find((p) => p.id === q.toUpperCase());
      if (label && hit) label.textContent = hit.label;
    }, 250);
  });
});
