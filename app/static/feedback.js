// Feedback form: note the screen size, and let a pasted screenshot (Ctrl+V) fill the file box.
document.getElementById("feedback-screen").value = `${window.innerWidth}x${window.innerHeight}`;
const shot = document.getElementById("feedback-shot");
document.addEventListener("paste", (event) => {
  const file = [...(event.clipboardData?.files || [])].find((f) => f.type.startsWith("image/"));
  if (!file) return;
  const files = new DataTransfer();
  files.items.add(file);
  shot.files = files.files;
  document.getElementById("feedback-paste-hint").textContent = "Screenshot pasted.";
});
