// Header search: suggests people while typing; picking a suggestion opens that person, Enter does a full search.
document.querySelectorAll("input[data-go]").forEach((input) => {
  const list = document.getElementById(input.getAttribute("list"));
  let timer, ids = new Set();
  input.addEventListener("input", () => {
    if (ids.has(input.value)) {  // a suggestion was picked (its value is the person's ID)
      location.href = input.dataset.go.replace("ID", encodeURIComponent(input.value));
      return;
    }
    clearTimeout(timer);
    const q = input.value.trim();
    if (q.length < 2) return;
    timer = setTimeout(async () => {
      const res = await fetch(input.dataset.find + "?q=" + encodeURIComponent(q));
      if (!res.ok) return;
      const people = await res.json();
      ids = new Set(people.map((p) => p.id));
      list.replaceChildren(...people.map((p) => Object.assign(document.createElement("option"), { value: p.id, label: p.label, textContent: p.label })));
    }, 250);
  });
});
