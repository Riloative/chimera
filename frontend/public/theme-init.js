// Applies the saved theme before first paint (no flash). External file so the
// strict Content-Security-Policy (script-src 'self') can stay free of inline scripts.
(function () {
  try {
    var prefs = JSON.parse(localStorage.getItem("chimera.appearance") || "{}");
    var theme = prefs.theme || "system";
    var dark = theme === "dark" || (theme === "system" && window.matchMedia("(prefers-color-scheme: dark)").matches);
    var root = document.documentElement;
    root.classList.toggle("dark", dark);
    root.dataset.fontSize = prefs.font_size || "md";
    root.dataset.chatWidth = prefs.chat_width || "default";
    if (prefs.reduce_motion) root.dataset.reduceMotion = "true";
  } catch (e) {}
})();
