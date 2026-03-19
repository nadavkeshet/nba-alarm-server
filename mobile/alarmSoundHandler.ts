/**
 * alarmSoundHandler.ts
 *
 * Shared module so _layout.tsx can trigger stopAlarmSound()
 * defined inside index.tsx without prop-drilling or context.
 *
 * index.tsx registers the handler once on mount.
 * _layout.tsx calls triggerStop() when DISMISS is pressed.
 */

let _stopHandler: (() => Promise<void>) | null = null;

export const registerStopHandler = (fn: () => Promise<void>): void => {
  _stopHandler = fn;
};

export const triggerStop = (): void => {
  _stopHandler?.();
};
