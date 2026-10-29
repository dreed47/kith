// Your account: show a theme as soon as it's picked, before Save.
document.querySelectorAll("input[data-css]").forEach(r => r.addEventListener("change", () => {
  document.getElementById("theme-css").href = r.dataset.css;
}));
