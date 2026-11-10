const { app, BrowserWindow, session } = require("electron");

app.whenReady().then(() => {
  session.defaultSession.setPermissionRequestHandler((webContents, permission, callback) => {
    console.log("[PERMISSION REQUEST]:", permission);
    callback(true);
  });
  session.defaultSession.setPermissionCheckHandler((webContents, permission) => {
    console.log("[PERMISSION CHECK]:", permission);
    return true;
  });

  const win = new BrowserWindow({ show: false });
  win.webContents.on("console-message", (event, level, message) => {
    console.log("[RENDERER CONSOLE]:", message);
  });
  const html = `
    <!DOCTYPE html>
    <html>
    <body>
    <script>
      const Speech = window.SpeechRecognition || window.webkitSpeechRecognition;
      console.log('Speech API exists:', !!Speech);
      if (Speech) {
        const s = new Speech();
        s.continuous = true;
        s.interimResults = true;
        s.onerror = (e) => console.log('Speech Error:', e.error);
        s.onstart = () => console.log('Speech Started Successfully!');
        s.onaudiostart = () => console.log('Audio capture started!');
        try {
          s.start();
        } catch (err) {
          console.log('Start error:', err.message);
        }
      }
    </script>
    </body>
    </html>
  `;
  win.loadURL(`data:text/html;charset=utf-8,${encodeURIComponent(html)}`);
  setTimeout(() => app.quit(), 4000);
});
