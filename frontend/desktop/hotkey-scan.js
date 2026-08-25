const { app, globalShortcut } = require("electron");

const modifierSets = [
  "Ctrl+Shift+Alt",
  "Ctrl+Alt+Win",
  "Ctrl+Shift+Win",
  "Ctrl+Shift+Alt+Win",
  "Alt+Shift+Win",
];

const keys = [
  "F",
  "J",
  "K",
  "Space",
  "D",
  "Q",
  "Z",
  "Y",
  "F1",
  "F2",
  "F3",
  "F4",
  "F5",
  "F6",
  "F7",
  "F8",
  "F9",
  "1",
  "2",
  "3",
  "`",
];

const candidates = [];
modifierSets.forEach((mod) => {
  keys.forEach((key) => {
    candidates.push(`${mod}+${key}`);
  });
});

app.whenReady().then(() => {
  const free = [];
  const taken = [];

  candidates.forEach((combo) => {
    try {
      const ok = globalShortcut.register(combo, () => {});
      if (ok) {
        free.push(combo);
        globalShortcut.unregister(combo);
      } else {
        taken.push(combo);
      }
    } catch {
      // Invalid accelerator strings — skip
    }
  });

  console.log(`\nFREE (${free.length}):`);
  free.forEach((c) => console.log("  " + c));

  console.log(`\nTAKEN (${taken.length}):`);
  taken.forEach((c) => console.log("  " + c));

  app.quit();
});