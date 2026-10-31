// The person page's map: one circle per place in the life events (data-pins, from views.map_pins). The family map
// (migration.html) also names who each event is about and draws a line per person (data-lines: [[from, to]]); on
// the person page the lines join the places in the order of the events (views.life_route).
(() => {
  const el = document.getElementById("person-map");
  if (!el || !window.L) return;
  const pins = JSON.parse(el.dataset.pins);
  const accent = getComputedStyle(document.documentElement).getPropertyValue("--accent").trim() || "#3366cc";
  const map = L.map(el, { scrollWheelZoom: false });
  L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19,
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
  }).addTo(map);
  for (const [from, to, approx] of JSON.parse(el.dataset.lines || "[]")) {  // approx: dashed, like its circle
    L.polyline([from, to], { color: accent, weight: 1.5, opacity: 0.45, dashArray: approx ? "4 6" : null }).addTo(map);
  }
  const spots = pins.map((p) => {
    const box = document.createElement("div");  // built from text nodes: place names are typed by people
    for (const e of p.events) {
      const line = box.appendChild(document.createElement("div"));
      if (e.url) {
        const a = line.appendChild(document.createElement("a"));
        a.href = e.url;
        a.textContent = e.name;
        line.append(" · ");
      }
      line.appendChild(document.createElement("strong")).textContent = e.label;
      line.append(`${e.date ? " · " + e.date : ""}${e.place ? " · " + e.place : ""}`);
    }
    if (p.approx) box.appendChild(document.createElement("div")).textContent = `Approximate: near ${p.approx}`;
    L.circleMarker([p.lat, p.lon], {
      radius: 9, color: accent, weight: 2, fillColor: accent,
      fillOpacity: p.approx ? 0.15 : 0.7, dashArray: p.approx ? "4 4" : null,
    }).bindPopup(box).addTo(map);
    return [p.lat, p.lon];
  });
  if (spots.length === 1) map.setView(spots[0], 10);
  else map.fitBounds(spots, { padding: [30, 30], maxZoom: 10 });
})();
