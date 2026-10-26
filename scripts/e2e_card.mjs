// End-to-end check of the development key and the map card, against the dev
// Home Assistant from scripts/develop.
//
//   npm run test:e2e              run, then remove what it created
//   npm run test:e2e -- --keep    leave the entry and dashboard to look at
//
// It logs in with config/.dev_credentials and uses the development key
// (dev-cache: saved responses, no API calls). If an entry with that key exists
// it adds an area, a second area and a commute to it, and removes only those
// afterwards; otherwise it sets one up and removes it afterwards. It then
// puts the map card on a dashboard and checks in headless Chromium that the
// route is drawn, markers appear, map tiles load and a marker opens its
// entity. Screenshots go to config/e2e/.
//
// Chromium comes from Playwright's cache; set PLAYWRIGHT_CHROMIUM to use
// another build.

import { existsSync, mkdirSync, readdirSync, readFileSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

import { chromium } from "playwright-core";

const HA = process.env.HA_URL ?? "http://127.0.0.1:8123";
const KEEP = process.argv.includes("--keep");
const OUT = join("config", "e2e");
const DASHBOARD = "ontario-511-e2e";
const DEV_KEY = "dev-cache";
// Oshawa to Pickering, along Highway 401.
const HOME = { latitude: 43.8971, longitude: -78.8658, radius: 8000 };
const WORK = { latitude: 43.8384, longitude: -79.0868, radius: 8000 };

const results = [];
const check = (name, ok, detail = "") => {
  results.push(ok);
  console.log(`${ok ? "PASS" : "FAIL"}  ${name}${detail ? `: ${detail}` : ""}`);
};

const credentials = () => {
  const values = Object.fromEntries(
    readFileSync(join("config", ".dev_credentials"), "utf8")
      .split("\n")
      .filter((line) => line.includes("="))
      .map((line) => line.split(/=(.*)/s).slice(0, 2).map((s) => s.trim()))
  );
  return { username: values.username, password: values.password };
};

const request = async (path, { token, body, form, method } = {}) => {
  const headers = {};
  if (token) headers.Authorization = `Bearer ${token}`;
  let payload;
  if (form) {
    headers["Content-Type"] = "application/x-www-form-urlencoded";
    payload = new URLSearchParams(form).toString();
  } else if (body !== undefined) {
    headers["Content-Type"] = "application/json";
    payload = JSON.stringify(body);
  }
  const response = await fetch(`${HA}${path}`, {
    method: method ?? (payload === undefined ? "GET" : "POST"),
    headers,
    body: payload,
  });
  const text = await response.text();
  if (!response.ok) {
    throw new Error(`${path}: HTTP ${response.status} ${text.slice(0, 200)}`);
  }
  return text ? JSON.parse(text) : null;
};

const login = async () => {
  const clientId = `${HA}/`;
  const { username, password } = credentials();
  const flow = await request("/auth/login_flow", {
    body: {
      client_id: clientId,
      handler: ["homeassistant", null],
      redirect_uri: `${HA}/?auth_callback=1`,
    },
  });
  const done = await request(`/auth/login_flow/${flow.flow_id}`, {
    body: { username, password, client_id: clientId },
  });
  const tokens = await request("/auth/token", {
    form: { grant_type: "authorization_code", code: done.result, client_id: clientId },
  });
  return { ...tokens, clientId };
};

const connect = (token) =>
  new Promise((resolve, reject) => {
    const socket = new WebSocket(`${HA.replace(/^http/, "ws")}/api/websocket`);
    const pending = new Map();
    let id = 0;
    const call = (message) =>
      new Promise((res, rej) => {
        id += 1;
        pending.set(id, { res, rej });
        socket.send(JSON.stringify({ id, ...message }));
      });
    socket.onmessage = (event) => {
      const message = JSON.parse(event.data);
      if (message.type === "auth_required") {
        socket.send(JSON.stringify({ type: "auth", access_token: token }));
      } else if (message.type === "auth_ok") {
        resolve({ call, close: () => socket.close() });
      } else if (message.type === "auth_invalid") {
        reject(new Error("WebSocket authentication failed"));
      } else if (message.type === "result" && pending.has(message.id)) {
        const { res, rej } = pending.get(message.id);
        pending.delete(message.id);
        message.success ? res(message.result) : rej(new Error(message.error.message));
      }
    };
    socket.onerror = () => reject(new Error(`Can't connect to ${HA}`));
  });

const waitFor = async (what, fn, timeoutMs = 120_000) => {
  const end = Date.now() + timeoutMs;
  while (Date.now() < end) {
    const value = await fn();
    if (value) return value;
    await new Promise((r) => setTimeout(r, 1000));
  }
  throw new Error(`Timed out waiting for ${what}`);
};

const AREA_INPUT = {
  name: "E2E Home",
  location: HOME,
  types: ["event", "alerts", "roadconditions", "cameras"],
};
const WORK_INPUT = { name: "E2E Work", location: WORK, types: ["event"] };
const COMMUTE_INPUT = { name: "E2E commute", corridor_km: 5, types: ["event", "cameras"] };

// Returns the entry that uses the development key, if there is one.
const findDevEntry = async (token) => {
  const entries = await request("/api/config/config_entries/entry?domain=ontario_511", {
    token,
  });
  for (const entry of entries.filter((e) => e.state === "loaded")) {
    const diagnostics = await request(`/api/diagnostics/config_entry/${entry.entry_id}`, {
      token,
    });
    if (diagnostics.data.entry.uses_development_key) return entry.entry_id;
  }
  return null;
};

// Sets up a new entry with the development key: key, area, second area, commute.
const createEntry = async (token) => {
  const step = (flowId, input) =>
    request(`/api/config/config_entries/flow/${flowId}`, { token, body: input });
  let flow = await request("/api/config/config_entries/flow", {
    token,
    body: { handler: "ontario_511" },
  });
  flow = await step(flow.flow_id, { api_key: DEV_KEY });
  check("Development key accepted without an API call", flow.step_id === "area");
  flow = await step(flow.flow_id, AREA_INPUT);
  check("Area saved in one step", flow.step_id === "add_commute");
  flow = await step(flow.flow_id, { next_step_id: "destination" });
  flow = await step(flow.flow_id, WORK_INPUT);
  flow = await step(flow.flow_id, COMMUTE_INPUT);
  check("Entry created with a commute", flow.type === "create_entry");
  return flow.result.entry_id;
};

// Adds an area, and a commute to a new area, to an existing entry; returns
// the IDs of the subentries it added.
const addToEntry = async (ws, token, entryId) => {
  const listed = async () =>
    (await ws.call({ type: "config_entries/subentries/list", entry_id: entryId }));
  const before = new Set((await listed()).map((s) => s.subentry_id));
  const start = (type) =>
    request("/api/config/config_entries/subentries/flow", {
      token,
      body: { handler: [entryId, type] },
    });
  const step = (flowId, input) =>
    request(`/api/config/config_entries/subentries/flow/${flowId}`, {
      token,
      body: input,
    });
  let flow = await start("area");
  flow = await step(flow.flow_id, AREA_INPUT);
  check("Area added to the existing development entry", flow.type === "create_entry");
  const home = (await listed()).find((s) => !before.has(s.subentry_id));
  flow = await start("commute");
  flow = await step(flow.flow_id, { next_step_id: "new_area" });
  flow = await step(flow.flow_id, { from_area: home.unique_id });
  flow = await step(flow.flow_id, WORK_INPUT);
  flow = await step(flow.flow_id, COMMUTE_INPUT);
  check("Commute added to the existing development entry", flow.type === "create_entry");
  return (await listed()).filter((s) => !before.has(s.subentry_id)).map((s) => s.subentry_id);
};

const findCommute = async (ws, token, entryId, subentryIds) => {
  const route = await waitFor("the commute's Route sensor", async () => {
    const entities = await ws.call({ type: "config/entity_registry/list" });
    const ours = entities.filter(
      (e) =>
        e.config_entry_id === entryId &&
        (!subentryIds || subentryIds.includes(e.config_subentry_id))
    );
    const states = await request("/api/states", { token });
    const byId = Object.fromEntries(states.map((s) => [s.entity_id, s]));
    const found = ours.find((e) => byId[e.entity_id]?.attributes.route);
    return found && { found, ours, byId };
  });
  const { found, ours, byId } = route;
  const onSummary = ours.filter(
    (e) =>
      e.device_id === found.device_id &&
      typeof byId[e.entity_id]?.attributes.latitude === "number"
  );
  const camera = ours.find(
    (e) => e.entity_id.startsWith("camera.") && byId[e.entity_id]?.attributes.latitude
  );
  const lines = byId[found.entity_id].attributes.route;
  check(
    "Route sensor has the commute's route",
    lines.length > 0,
    `${byId[found.entity_id].state} km over ${lines.length} stretches`
  );
  return [found.entity_id, ...onSummary.map((e) => e.entity_id), camera?.entity_id].filter(
    Boolean
  );
};

const chromiumPath = () => {
  if (process.env.PLAYWRIGHT_CHROMIUM) return process.env.PLAYWRIGHT_CHROMIUM;
  const cache = join(homedir(), ".cache", "ms-playwright");
  const builds = existsSync(cache)
    ? readdirSync(cache).filter((d) => d.startsWith("chromium_headless_shell-")).sort()
    : [];
  if (!builds.length) {
    throw new Error("No Chromium found; set PLAYWRIGHT_CHROMIUM");
  }
  return join(cache, builds.at(-1), "chrome-headless-shell-linux64", "chrome-headless-shell");
};

// Finds the card through the frontend's nested shadow roots.
const FIND_CARD = `(() => {
  const find = (root) => {
    for (const el of root.querySelectorAll("*")) {
      if (el.localName === "ontario-511-map-card") return el;
      if (el.shadowRoot) {
        const found = find(el.shadowRoot);
        if (found) return found;
      }
    }
    return null;
  };
  return find(document);
})()`;

const checkCard = async (tokens, entities) => {
  const browser = await chromium.launch({
    executablePath: chromiumPath(),
    args: ["--use-angle=swiftshader", "--enable-unsafe-swiftshader"],
  });
  const errors = [];
  try {
    const context = await browser.newContext({ viewport: { width: 1280, height: 900 } });
    await context.addInitScript((stored) => {
      localStorage.setItem("hassTokens", JSON.stringify(stored));
    }, {
      access_token: tokens.access_token,
      refresh_token: tokens.refresh_token,
      token_type: "Bearer",
      expires_in: tokens.expires_in,
      expires: Date.now() + tokens.expires_in * 1000,
      hassUrl: HA,
      clientId: tokens.clientId,
    });
    const page = await context.newPage();
    page.on("pageerror", (err) => errors.push(err.message));
    page.on("console", (msg) => {
      if (msg.type() === "error") {
        errors.push(msg.text());
      }
    });
    await page.goto(`${HA}/${DASHBOARD}/map`);

    mkdirSync(OUT, { recursive: true });
    try {
      await page.waitForFunction(
        `(() => { const c = ${FIND_CARD}; return c && c._map && c._map.loaded() && c._seen; })()`,
        null,
        { timeout: 90_000 }
      );
    } catch (err) {
      await page.screenshot({ path: join(OUT, "failed.png") });
      throw new Error(
        `The card didn't finish loading (see ${OUT}/failed.png). ` +
          `Browser errors: ${errors.join(" | ") || "none"}`
      );
    }
    // Let the remaining tiles arrive before measuring and taking pictures.
    await page.waitForTimeout(3000);
    const report = await page.evaluate(`(() => {
      const card = ${FIND_CARD};
      const map = card._map;
      return {
        style: map.getStyle().name,
        routes: map.queryRenderedFeatures({ layers: ["o511-routes"] }).length,
        local: map.queryRenderedFeatures({ layers: ["o511-local"] }).length,
        roads: map.queryRenderedFeatures().filter((f) => f.sourceLayer).length,
        markers: card.shadowRoot.querySelectorAll(".marker").length,
        canvas: Boolean(card.shadowRoot.querySelector("canvas.maplibregl-canvas")),
        attribution: card.shadowRoot.querySelector(".maplibregl-ctrl-attrib")?.textContent ?? "",
      };
    })()`);
    const expectedMarkers = entities.length - 1;
    check("Map drawn on a WebGL canvas", report.canvas, report.style);
    check("OpenStreetMap tiles loaded", report.roads > 0, `${report.roads} map features`);
    check("OpenStreetMap attribution shown", report.attribution.includes("OpenStreetMap"));
    check("Commute route rendered", report.routes > 0, `${report.routes} route features`);
    check("Local roads rendered dashed", report.local > 0, `${report.local} local features`);
    check(
      "A marker for each located entity",
      report.markers === expectedMarkers,
      `${report.markers} of ${expectedMarkers}`
    );
    await page.screenshot({ path: join(OUT, "map-card.png") });

    await page.evaluate(`${FIND_CARD}.shadowRoot.querySelector(".marker").click()`);
    const opened = await page
      .waitForFunction(
        `(() => {
          const find = (root) => {
            for (const el of root.querySelectorAll("*")) {
              if (el.localName === "ha-more-info-dialog") return el;
              if (el.shadowRoot) { const f = find(el.shadowRoot); if (f) return f; }
            }
            return null;
          };
          const dialog = find(document);
          return dialog && dialog.shadowRoot && dialog.shadowRoot.querySelector("ha-dialog, ha-adaptive-dialog, ha-md-dialog, wa-dialog");
        })()`,
        null,
        { timeout: 15_000 }
      )
      .then(() => true, () => false);
    check("Selecting a marker opens its details", opened);
    await page.screenshot({ path: join(OUT, "more-info.png") });
    check("No errors in the browser", errors.length === 0, errors.join(" | "));
  } finally {
    await browser.close();
  }
};

const main = async () => {
  const tokens = await login();
  const token = tokens.access_token;
  const ws = await connect(token);
  let entryId;
  let created = false;
  let subentryIds;
  let dashboardId;
  try {
    // Use the development key's entry if there is one, adding to it and
    // removing only what this check adds.
    entryId = await findDevEntry(token);
    if (entryId) {
      subentryIds = await addToEntry(ws, token, entryId);
    } else {
      entryId = await createEntry(token);
      created = true;
    }
    const entities = await findCommute(ws, token, entryId, subentryIds);
    const dashboard = await ws.call({
      type: "lovelace/dashboards/create",
      url_path: DASHBOARD,
      title: "Ontario 511 E2E",
      mode: "storage",
      show_in_sidebar: false,
      require_admin: false,
    });
    dashboardId = dashboard.id;
    await ws.call({
      type: "lovelace/config/save",
      url_path: DASHBOARD,
      config: {
        views: [
          {
            title: "Map",
            path: "map",
            type: "panel",
            cards: [
              { type: "custom:ontario-511-map-card", title: "E2E commute", entities },
            ],
          },
        ],
      },
    });
    await checkCard(tokens, entities);
  } finally {
    if (!KEEP) {
      if (dashboardId) {
        await ws.call({ type: "lovelace/dashboards/delete", dashboard_id: dashboardId });
      }
      if (created) {
        await request(`/api/config/config_entries/entry/${entryId}`, {
          token,
          method: "DELETE",
        });
      }
      for (const subentryId of subentryIds ?? []) {
        await ws.call({
          type: "config_entries/subentries/delete",
          entry_id: entryId,
          subentry_id: subentryId,
        });
      }
    }
    ws.close();
  }
  const failed = results.filter((ok) => !ok).length;
  console.log(
    `\n${results.length - failed} passed, ${failed} failed. Screenshots in ${OUT}/` +
      (KEEP ? `\nKept: ${HA}/${DASHBOARD}/map` : "")
  );
  process.exitCode = failed ? 1 : 0;
};

main().catch((err) => {
  console.error(`ERROR  ${err.message}`);
  process.exitCode = 1;
});
