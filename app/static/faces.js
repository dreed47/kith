// Face tags on a photo: hovering a name in the list lights up its box, and editors drag a box to tag a face.
// Boxes are stored as fractions of the picture, so they fit whatever size it's shown at.
const photo = document.querySelector(".face-photo");
document.querySelectorAll(".faces-list li[data-tag]").forEach((li) => {
  const box = photo.querySelector(`.face[data-tag="${li.dataset.tag}"]`);
  li.addEventListener("mouseenter", () => box.classList.add("lit"));
  li.addEventListener("mouseleave", () => box.classList.remove("lit"));
});

const start = document.getElementById("tag-face");
const form = document.querySelector(".face-form");
const hint = document.querySelector(".face-hint");
if (start && form) {
  const img = photo.querySelector("img");
  let origin, box;
  const at = (e) => {
    const r = img.getBoundingClientRect();
    return { x: Math.min(Math.max((e.clientX - r.left) / r.width, 0), 1),
             y: Math.min(Math.max((e.clientY - r.top) / r.height, 0), 1) };
  };
  const cancel = () => {
    photo.classList.remove("drawing");
    box?.remove();
    box = origin = null;
    form.hidden = hint.hidden = true;
    start.hidden = false;
  };
  start.addEventListener("click", () => {
    photo.classList.add("drawing");
    hint.hidden = false;
    start.hidden = true;
  });
  form.querySelector("[data-cancel]").addEventListener("click", cancel);
  photo.addEventListener("pointerdown", (e) => {
    if (!photo.classList.contains("drawing")) return;
    e.preventDefault();
    photo.setPointerCapture(e.pointerId);
    box?.remove();
    for (const k of "xywh") form.elements[k].value = "";
    origin = at(e);
    box = document.createElement("div");
    box.className = "face new";
    photo.append(box);
  });
  photo.addEventListener("pointermove", (e) => {
    if (!origin) return;
    const p = at(e);
    const b = { x: Math.min(origin.x, p.x), y: Math.min(origin.y, p.y), w: Math.abs(p.x - origin.x), h: Math.abs(p.y - origin.y) };
    Object.assign(box.style, { left: `${b.x * 100}%`, top: `${b.y * 100}%`, width: `${b.w * 100}%`, height: `${b.h * 100}%` });
    for (const k in b) form.elements[k].value = b[k].toFixed(4);
  });
  photo.addEventListener("pointerup", () => {
    if (!origin) return;
    origin = null;
    if (!(form.elements.w.value >= 0.01 && form.elements.h.value >= 0.01)) return box.remove();  // a click, not a drag
    form.hidden = false;
    hint.hidden = true;
    form.elements.personID.focus();
  });
}
