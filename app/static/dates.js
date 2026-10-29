// Date boxes stay free text (genealogy dates are often "abt 1895" or just a year). This adds:
//  - a calendar button that fills in an exact day in TNG's style, e.g. "3 DEC 1920"
//  - a live hint underneath showing how the server understands the text (or a warning if it can't)
const MONTHS = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"];

document.querySelectorAll("input[data-date]").forEach((input) => {
  const hint = document.getElementById(input.id + "-hint");
  const picker = document.getElementById(input.id + "-picker");
  const button = document.getElementById(input.id + "-pick");
  let timer;

  async function explain() {
    const res = await fetch(input.dataset.date + "?d=" + encodeURIComponent(input.value));
    if (!res.ok) return;
    const { message, ok } = await res.json();
    hint.textContent = message;
    hint.classList.toggle("warning", !ok);
  }

  input.addEventListener("input", () => {
    clearTimeout(timer);
    timer = setTimeout(explain, 300);
  });

  button.hidden = false;
  button.addEventListener("click", () => {
    if (picker.showPicker) picker.showPicker(); else picker.click();
  });
  picker.addEventListener("change", () => {
    if (!picker.value) return;
    const [y, m, d] = picker.value.split("-").map(Number);
    input.value = `${d} ${MONTHS[m - 1]} ${y}`;
    explain();
  });

  if (input.value) explain();
});
