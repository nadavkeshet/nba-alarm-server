// ─── NBA Smart Alarm – Server Configuration ───────────────────────────────────
//
// STEP 1: Find your PC's local IP
//   Windows → open Command Prompt → run: ipconfig
//            look for "IPv4 Address" under your Wi-Fi adapter (e.g. 192.168.1.XX)
//   Mac/Linux → open Terminal → run: ifconfig
//            look for "inet" under en0 (Wi-Fi)
//
// STEP 2: Paste it in LOCAL_IP below, then rebuild the APK.
//         Your phone and PC must be on the same Wi-Fi network.
//
// STEP 3: To switch to production, change SERVER_URL to PROD_URL.
// ──────────────────────────────────────────────────────────────────────────────

const LOCAL_IP  = "192.168.1.119";           // ← local Wi-Fi (for development only)
const LOCAL_URL = `http://${LOCAL_IP}:8001`;
const PROD_URL  = "https://nba-alarm-server-production.up.railway.app";
const DEV_URL   = "https://nba-alarm-server-dev.up.railway.app";

// ↓ Switch to LOCAL_URL for local development, DEV_URL for dev server testing
export const SERVER_URL = PROD_URL;
