const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("fridayOverlay", {
  onVisibility(callback) {
    ipcRenderer.on("companion-visibility", (_event, visible) => {
      callback(Boolean(visible));
    });
  },
  notifyReady() {
    ipcRenderer.send("overlay-ready");
  },
  openMainApp() {
    ipcRenderer.send("open-main-app");
  },
  shutdownFriday() {
    ipcRenderer.send("shutdown-friday");
  },
  setOverlayHeight(height) {
    ipcRenderer.send("overlay-resize", { height: Math.round(height) });
  },
  setOverlayBounds(width, height) {
    ipcRenderer.send("overlay-resize", {
      width: Math.round(width),
      height: Math.round(height),
    });
  },
});