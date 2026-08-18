// Web Bluetooth strap connection with auto-reconnect.
//
// Wraps the chooser + Heart Rate Service notification flow validated by the
// phase-0 test page (index.html) into a reusable module.

import { parseHrMeasurement } from "./hr-parser.js";

const HR_SERVICE = "heart_rate"; // 0x180D
const HR_MEASUREMENT = "heart_rate_measurement"; // 0x2A37
const BATTERY_SERVICE = "battery_service"; // 0x180F
const BATTERY_LEVEL = "battery_level"; // 0x2A19
const RECONNECT_DELAYS_MS = [1000, 2000, 4000, 8000, 8000, 8000];

// "Polar H10 16CD9E3C" -> "16CD9E3C" (mirrors ble_collector.device_id_from_name).
export function deviceIdFromName(name) {
  const parts = (name || "").split(/\s+/).filter(Boolean);
  if (parts.length >= 2 && !["H10", "?"].includes(parts[parts.length - 1])) {
    return parts[parts.length - 1];
  }
  return null;
}

/**
 * Show the chooser and stream heart-rate notifications from the picked strap.
 *
 * Callbacks: onReading({bpm, rrIntervalsMs, sensorContact}), and optional
 * onStatus(text, kind: "ok"|"warn"|"err"), onBattery(percent).
 * Returns {device, name, deviceId, disconnect()}. Reconnects automatically
 * with backoff unless disconnect() was called.
 */
export async function connectStrap({ onReading, onStatus = () => {}, onBattery = () => {} }) {
  const device = await navigator.bluetooth.requestDevice({
    filters: [{ services: [HR_SERVICE] }],
    optionalServices: [BATTERY_SERVICE],
  });

  let intentional = false;

  async function start() {
    const server = await device.gatt.connect();
    const hr = await server.getPrimaryService(HR_SERVICE);
    const chr = await hr.getCharacteristic(HR_MEASUREMENT);
    chr.addEventListener("characteristicvaluechanged", (e) => {
      try {
        onReading(parseHrMeasurement(e.target.value));
      } catch (err) {
        onStatus(`parse error: ${err.message}`, "err");
      }
    });
    await chr.startNotifications();
    onStatus("connected", "ok");
    readBattery(server); // fire-and-forget; battery is a nicety
  }

  async function readBattery(server) {
    try {
      const svc = await server.getPrimaryService(BATTERY_SERVICE);
      const level = await (await svc.getCharacteristic(BATTERY_LEVEL)).readValue();
      onBattery(level.getUint8(0));
    } catch {
      /* battery service is optional */
    }
  }

  device.addEventListener("gattserverdisconnected", async () => {
    if (intentional) {
      onStatus("disconnected", "err");
      return;
    }
    for (const [attempt, delay] of RECONNECT_DELAYS_MS.entries()) {
      onStatus(`reconnecting (attempt ${attempt + 1})…`, "warn");
      try {
        await start();
        return;
      } catch {
        await new Promise((r) => setTimeout(r, delay));
      }
    }
    onStatus("gave up reconnecting", "err");
  });

  await start();
  return {
    device,
    name: device.name || "(unnamed)",
    deviceId: deviceIdFromName(device.name),
    disconnect() {
      intentional = true;
      device.gatt.disconnect();
    },
  };
}
