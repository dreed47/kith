// Your account: show a theme, or light or dark, as soon as it's picked, before Save.
document.querySelectorAll("input[data-css]").forEach(r => r.addEventListener("change", () => {
  document.getElementById("theme-css").href = r.dataset.css;
}));
document.querySelectorAll('input[name="mode"]').forEach(r => r.addEventListener("change", () => {
  document.documentElement.dataset.mode = r.value;
  document.documentElement.applyScheme();
}));
