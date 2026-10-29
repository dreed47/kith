// Place boxes (input[data-places]) suggest existing places as you type, so the same place is spelled the same way.
document.querySelectorAll("input[data-places]").forEach((input) => {
  const list = document.createElement("datalist");
  list.id = input.name + "-places";
  input.after(list);
  input.setAttribute("list", list.id);
  input.setAttribute("autocomplete", "off");
  let timer;
  input.addEventListener("input", () => {
    clearTimeout(timer);
    const q = input.value.trim();
    if (q.length < 2) return;
    timer = setTimeout(async () => {
      const res = await fetch(input.dataset.places + "?q=" + encodeURIComponent(q));
      if (!res.ok) return;
      const places = await res.json();
      list.replaceChildren(...places.map((p) => Object.assign(document.createElement("option"), { value: p })));
    }, 250);
  });
});
