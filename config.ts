/**
 * NBA Smart Alarm – App Configuration
 * ------------------------------------
 * Change SERVER_IP to the local IP address of the machine running the
 * Python FastAPI server (run `ipconfig` on Windows / `ifconfig` on Mac/Linux).
 *
 * Both your phone and the server must be on the same Wi-Fi network.
 */
export const SERVER_IP   = "192.168.1.119";   // <-- update this
export const SERVER_PORT = 8001;
export const SERVER_URL  = `http://${SERVER_IP}:${SERVER_PORT}`;
