import { contextBridge, ipcRenderer } from "electron";

contextBridge.exposeInMainWorld("rallyDesktop", {
  version: "2.0.0",
  collapseSidecar: () => ipcRenderer.send("sidecar:collapse"),
  expandSidecar: () => ipcRenderer.send("sidecar:expand"),
  toggleSidecar: () => ipcRenderer.send("sidecar:toggle"),
  openDashboard: () => ipcRenderer.send("dashboard:open"),
  wakeVoice: () => ipcRenderer.send("voice:wake"),
  onWake: (callback: () => void) => {
    const handler = () => callback();
    ipcRenderer.on("voice:wake", handler);
    return () => ipcRenderer.removeListener("voice:wake", handler);
  },
});

contextBridge.exposeInMainWorld("rallySecurity", {
  getTokens: () => ipcRenderer.invoke("security:get-tokens"),
});
