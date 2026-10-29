// Cemetery maps: every cemetery on the Cemeteries page, or the one on its own page (data-pins, from places.map_pin).
(() => {
  const el = document.getElementById("cemetery-map");
  if (!el || !window.L) return;
  const pins = JSON.parse(el.dataset.pins);
  const accent = getComputedStyle(document.documentElement).getPropertyValue("--accent").trim() || "#3366cc";
  const map = L.map(el, { scrollWheelZoom: false });
  L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19,
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
  }).addTo(map);
  const spots = pins.map((p) => {
    const box = document.createElement("div");  // built from text nodes: names are typed by people
    const link = box.appendChild(document.createElement("strong")).appendChild(document.createElement("a"));
    link.href = p.url;
    link.textContent = p.name;
    if (p.where) box.appendChild(document.createElement("div")).textContent = p.where;
    box.appendChild(document.createElement("div")).textContent =
      `${p.people} ${p.people === 1 ? "person" : "people"} · ${p.stones} ${p.stones === 1 ? "stone" : "stones"}`;
    L.circleMarker([p.lat, p.lon], {
      radius: 8, color: accent, weight: 2, fillColor: accent, fillOpacity: 0.7,
    }).bindPopup(box).addTo(map);
    return [p.lat, p.lon];
  });
  if (spots.length === 1) map.setView(spots[0], 14);
  else map.fitBounds(spots, { padding: [30, 30] });
})();
