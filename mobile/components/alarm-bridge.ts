/**
 * Alarm Bridge
 *
 * A module-level singleton that lets the root _layout.tsx invoke functions
 * that live inside HomeScreen without prop-drilling or global state managers.
 *
 * Why this exists:
 *   Notification response listeners must be registered at the ROOT layout level
 *   so they fire even when HomeScreen is not the active tab or is suspended.
 *   But dismissing the alarm requires calling HomeScreen's dismissAlarm()
 *   (which has access to soundRef and the overlay state setters).
 *   This bridge connects the two without coupling the files directly.
 *
 * Usage:
 *   _layout.tsx  → calls AlarmBridge.dismiss() / AlarmBridge.trigger()
 *   index.tsx    → calls AlarmBridge.register() on mount, AlarmBridge.unregister() on unmount
 */

type VoidAsync    = () => Promise<void>;
type TriggerAsync = (title: string, body: string) => Promise<void>;

let _dismiss: VoidAsync    | null = null;
let _trigger: TriggerAsync | null = null;

export const AlarmBridge = {
  /** Called by HomeScreen on mount to hand its local functions to the bridge. */
  register(dismiss: VoidAsync, trigger: TriggerAsync) {
    _dismiss = dismiss;
    _trigger = trigger;
  },

  /** Called by HomeScreen on unmount. */
  unregister() {
    _dismiss = null;
    _trigger = null;
  },

  /**
   * Stop sound + hide overlay.
   * Safe to call even if HomeScreen is not mounted — silently no-ops.
   */
  async dismiss() {
    await _dismiss?.();
  },

  /**
   * Show overlay + start sound.
   * Safe to call even if HomeScreen is not mounted — alarm will appear
   * via AsyncStorage when HomeScreen mounts.
   */
  async trigger(title: string, body: string) {
    await _trigger?.(title, body);
  },
};
