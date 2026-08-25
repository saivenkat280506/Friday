const { app, globalShortcut } = require("electron");
const keys = "ABCDEFGHIJKLMNOPQRSTUVWXYZ".split("").concat(["`","Space","F9","F10","F11","Insert","Home","End"]);
const mods = ["Ctrl","Alt","Shift","Win"];
const combos = [];
for (const m of mods) for (const k of keys) combos.push(m+"+"+k);
app.whenReady().then(() => {
  const free=[], taken=[];
  for (const c of combos) {
    try {
      const ok = globalShortcut.register(c, () => {});
      if (ok) { free.push(c); globalShortcut.unregister(c); } else taken.push(c);
    } catch { taken.push(c); }
  }
  console.log("1-MODIFIER FREE ("+free.length+"):");
  free.forEach(c => console.log("  "+c));
  console.log("\n1-MODIFIER TAKEN ("+taken.length+"):");
  taken.forEach(c => console.log("  "+c));
  app.quit();
});
