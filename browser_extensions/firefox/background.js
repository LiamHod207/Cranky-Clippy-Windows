/* Native-messaging bridge for Jev's task-tab memory and restore command. */
const api = globalThis.browser || globalThis.chrome;
const HOST = "com.crankyclippy.bridge";
const RECONNECT_ALARM = "cranky-clippy-native-reconnect";
const PROBE = "cc-ext-v2";   // reported back with every active-tab answer
const MAX_RETRY_MS = 5000;   // a missing host must not stall the poll for long
let nativePort = null;
let retryDelay = 1000;

// Firefox (MV2) answers a callback; Chrome (MV3) answers a promise and *throws*
// if a callback argument is passed at all. So try the promise-only call first
// and fall back to the callback form, which settles whichever arrives first.
function invoke(object, method, ...args) {
  return new Promise((resolve, reject) => {
    let settled = false;
    const done = value => {
      if (!settled) { settled = true; resolve(value); }
    };
    const fail = error => {
      if (!settled) {
        settled = true;
        reject(error instanceof Error ? error : new Error(String(error)));
      }
    };
    let result;
    try {
      result = object[method](...args);
    } catch (_promiseStyleRejected) {
      // Chrome MV3 rejects a trailing callback; retry the callback style that
      // Firefox (and older Chrome) still require.
      try {
        object[method](...args, value => {
          const error = api.runtime.lastError;
          if (error) fail(new Error(error.message));
          else done(value);
        });
      } catch (error) {
        fail(error);
      }
      return;
    }
    if (result && typeof result.then === "function") {
      result.then(done, fail);
    } else {
      done(result);
    }
  });
}

function attachPort(port) {
  port.onMessage.addListener(message => {
    if (message.action === "remember") {
      rememberTab(message.target);
    } else if (message.action === "restore") {
      restoreTab(message.target);
    } else if (message.action === "query_active_tab") {
      queryActiveTab(message.request_id);
    } else if (message.action === "shutdown") {
      const closing = nativePort;
      nativePort = null;
      try { closing.disconnect(); } catch (_error) {}
      scheduleReconnect();
    }
  });
  port.onDisconnect.addListener(() => {
    nativePort = null;
    scheduleReconnect();
  });
  retryDelay = 1000;
}

function connectNative() {
  if (nativePort) return;
  let port;
  try {
    port = api.runtime.connectNative(HOST);
  } catch (_error) {
    // Chrome throws synchronously when the host manifest is missing.
    scheduleReconnect();
    return;
  }
  if (port && typeof port.then === "function") {
    // Firefox answers with a promise, and rejects it when the host is missing
    // or refuses to start. Handling only the synchronous case would leave a
    // rejected promise behind and kill this whole script on the next access.
    port.then(connected => {
      if (!nativePort) {
        nativePort = connected;
        attachPort(connected);
      }
    }, () => {
      nativePort = null;
      scheduleReconnect();
    });
    return;
  }
  nativePort = port;
  if (port) attachPort(port);
  else scheduleReconnect();
}

function scheduleReconnect() {
  setTimeout(connectNative, retryDelay);
  retryDelay = Math.min(retryDelay * 2, MAX_RETRY_MS);
}

async function rememberTab(target) {
  try {
    const tabs = await invoke(api.tabs, "query", {});
    const expectedUrl = normalizedUrl(target.site_url || "");
    const expectedTitle = (target.tab_title || "").trim().toLocaleLowerCase();
    const titleMatches = candidate => {
      const actual = (candidate.title || "").trim().toLocaleLowerCase();
      return Boolean(expectedTitle && actual && (actual === expectedTitle ||
        actual.includes(expectedTitle) || expectedTitle.includes(actual)));
    };
    const urlCandidates = expectedUrl
      ? tabs.filter(candidate => normalizedUrl(candidate.url) === expectedUrl)
      : [];
    // Search all windows, not just lastFocusedWindow: the browser's active
    // tab may already be the distraction while Jev is processing its verdict.
    const tab = (urlCandidates.find(titleMatches) || urlCandidates[0]) ||
      tabs.find(titleMatches);
    if (!tab) {
      nativePort?.postMessage({type: "remember_mismatch", expected: target.tab_title || target.site_url});
      return;
    }
    const record = {
      tabId: tab.id,
      windowId: tab.windowId,
      url: tab.url || target.site_url || "",
      title: tab.title || target.tab_title || target.window_title || "",
      target: target,
      savedAt: Date.now(),
    };
    await invoke(api.storage.local, "set", {lastOnTaskTab: record});
    nativePort?.postMessage({type: "remembered", title: record.title});
  } catch (error) {
    nativePort?.postMessage({type: "error", action: "remember", message: String(error)});
  }
}

function normalizedUrl(url) {
  try {
    const parsed = new URL(url);
    parsed.hash = "";
    return parsed.href;
  } catch (_error) {
    return url || "";
  }
}

function domainOf(url) {
  try {
    return new URL(url).hostname.toLowerCase().replace(/^(www|open)\./, "");
  } catch (_error) {
    return "";
  }
}

// Report the tab the browser is actually showing. Windows cannot read a
// Chromium tab from disk, so this is the only fresh source.
//
// The windows.* API is deliberately avoided: Chrome's extension API does not
// expose window titles, and lastFocusedWindow does the same job through tabs.
async function queryActiveTab(requestId) {
  const reply = payload => {
    payload.request_id = requestId;
    nativePort?.postMessage(payload);
    return payload;
  };
  const empty = {type: "active_tab", url: "", title: "", window_title: "", probe: PROBE};
  try {
    let tabs = await invoke(api.tabs, "query", {active: true, lastFocusedWindow: true});
    const focusedCount = tabs ? tabs.length : -1;
    if (!tabs || !tabs.length) {
      tabs = await invoke(api.tabs, "query", {active: true});
    }
    const active = (tabs || []).find(candidate => candidate.active) || (tabs || [])[0];
    if (!active) return reply({...empty, probe: PROBE + "/no-tabs/" + focusedCount});

    const url = active.url || "";
    if (!/^(https?|file|moz-extension|chrome-extension):/i.test(url)) {
      // A new-tab page or an internal page: say so honestly rather than
      // inventing a URL for the window.
      return reply({...empty, tab_title: active.title || "", window_id: active.windowId,
                    probe: PROBE + "/internal-page"});
    }

    const windowTabs = await invoke(api.tabs, "query", {windowId: active.windowId});
    const others = [];
    for (const candidate of windowTabs || []) {
      if (candidate.id === active.id) continue;
      const domain = domainOf(candidate.url || "");
      if (domain && !/^(newtab|edge|about|chrome|moz)/.test(domain) && !others.includes(domain)) {
        others.push(domain);
      }
    }
    return reply({
      type: "active_tab",
      url,
      title: active.title || "",
      // Chromium captions its window "<tab title> - <browser>"; the tab title
      // is what the detector can compare the window against.
      window_title: active.title || "",
      window_id: active.windowId,
      other_domains: others.slice(0, 8),
      probe: PROBE + "/ok",
    });
  } catch (error) {
    return reply({...empty, error: String(error), probe: PROBE + "/error"});
  }
}

async function restoreTab(target) {
  try {
    const stored = await invoke(api.storage.local, "get", "lastOnTaskTab");
    const saved = stored && stored.lastOnTaskTab;
    const tabs = await invoke(api.tabs, "query", {});
    const desiredUrl = (saved && saved.url) || target.site_url || "";
    const desiredNormalized = normalizedUrl(desiredUrl);
    let tab = saved && tabs.find(candidate =>
      candidate.id === saved.tabId &&
      desiredNormalized && normalizedUrl(candidate.url) === desiredNormalized
    );
    if (!tab && desiredNormalized) {
      tab = tabs.find(candidate => normalizedUrl(candidate.url) === desiredNormalized);
    }
    if (!tab && target.tab_title) {
      const title = target.tab_title.trim().toLocaleLowerCase();
      tab = tabs.find(candidate => (candidate.title || "").trim().toLocaleLowerCase() === title);
    }

    if (!tab) {
      if (!desiredUrl || !/^https?:\/\//i.test(desiredUrl)) {
        throw new Error("The saved tab is gone and has no restorable web URL.");
      }
      tab = await invoke(api.tabs, "create", {url: desiredUrl, active: true});
    } else {
      await invoke(api.tabs, "update", tab.id, {active: true});
    }
    if (tab.windowId !== undefined) {
      await invoke(api.windows, "update", tab.windowId, {focused: true});
    }
    nativePort?.postMessage({
      type: "restored",
      reusedExistingTab: Boolean(saved && tab.id === saved.tabId),
      title: tab.title || target.tab_title || "",
      url: tab.url || desiredUrl,
    });
  } catch (error) {
    nativePort?.postMessage({type: "error", action: "restore", message: String(error)});
  }
}

api.runtime.onInstalled.addListener(() => {
  api.alarms.create(RECONNECT_ALARM, {periodInMinutes: 1});
  connectNative();
});
api.runtime.onStartup.addListener(() => {
  api.alarms.create(RECONNECT_ALARM, {periodInMinutes: 1});
  connectNative();
});
api.alarms.onAlarm.addListener(alarm => {
  if (alarm.name === RECONNECT_ALARM && !nativePort) connectNative();
});
connectNative();
