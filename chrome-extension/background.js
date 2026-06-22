const API_BASE = "http://127.0.0.1:8000";
const ICONS = {
  off: "icons/favicon.png",
  on: "icons/favicon-timer-active.png",
};

async function setTimerIcon(isRunning) {
  await chrome.action.setIcon({ path: isRunning ? ICONS.on : ICONS.off });
}

async function syncTimerIcon() {
  try {
    const response = await fetch(`${API_BASE}/api/state`);
    const state = await response.json();
    await setTimerIcon(Boolean(state.running));
  } catch {
    await setTimerIcon(false);
  }
}

chrome.runtime.onInstalled.addListener(() => {
  chrome.alarms.create("syncTimerIcon", { periodInMinutes: 1 });
  syncTimerIcon();
});

chrome.runtime.onStartup.addListener(syncTimerIcon);

chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === "syncTimerIcon") syncTimerIcon();
});

chrome.runtime.onMessage.addListener((message) => {
  if (message?.type === "timer-state") setTimerIcon(Boolean(message.running));
});
