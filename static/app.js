"use strict";

const form = document.getElementById("plan-form");
const btn = document.getElementById("submit");
const err = document.getElementById("form-error");
const statusEl = document.getElementById("status");
const results = document.getElementById("results");

const keyBox = document.getElementById("ai-key-box");
const keyInput = document.getElementById("gemini_key");
let mode = "unknown"; // "server" (FastAPI backend) or "static" (GitHub Pages, browser engine)

const store = {
  get() { try { return sessionStorage.getItem("sza-gemini-key") || ""; } catch { return ""; } },
  set(v) { try { v ? sessionStorage.setItem("sza-gemini-key", v) : sessionStorage.removeItem("sza-gemini-key"); } catch { /* storage blocked */ } },
};

async function detectMode() {
  if (mode !== "unknown") return mode;
  try {
    const res = await fetch("health", { headers: { Accept: "application/json" } });
    const data = res.ok ? await res.json() : null;
    mode = data && data.status === "ok" ? "server" : "static";
  } catch {
    mode = "static";
  }
  if (mode === "static") {
    keyBox.hidden = false;
    keyInput.value = store.get();
  }
  return mode;
}
detectMode();

const KIND_LABEL = { routine: "Routine", indoor: "Indoor air", alternative: "Low-emission alternative", health: "Health" };

function el(tag, props = {}, children = []) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) {
    if (k === "class") node.className = v; else if (k === "text") node.textContent = v; else node.setAttribute(k, v);
  }
  for (const c of children) node.append(c);
  return node;
}

const catClass = (c) => "cat-" + String(c || "Unknown").replace(/\s+/g, "-");
const fmtTime = (iso) => new Date(iso).toLocaleString([], { weekday: "short", hour: "2-digit", minute: "2-digit" });

function readProfile() {
  const fd = new FormData(form);
  const conditions = fd.getAll("conditions");
  return {
    profile: {
      city: String(fd.get("city") || "").trim(),
      commute_mode: fd.get("commute_mode"),
      commute_km: Number(fd.get("commute_km") || 0),
      workdays_per_week: Number(fd.get("workdays_per_week") || 0),
      cooks_with: fd.get("cooks_with"),
      conditions,
      outdoor_exercise: fd.get("outdoor_exercise") === "on",
      has_purifier: fd.get("has_purifier") === "on",
    },
    question: String(fd.get("question") || "").trim() || null,
  };
}

function showError(msg, field) {
  err.textContent = msg;
  err.hidden = false;
  if (field) field.focus();
}

function render(data) {
  const cur = data.current;
  const now = document.getElementById("now");
  now.replaceChildren(
    el("div", { class: "badge " + catClass(cur.category) }, [
      el("strong", { text: String(cur.naqi ?? "–") }), el("span", { text: cur.category }),
    ]),
    el("div", {}, [
      el("h3", { text: data.location.name + (data.location.country ? ", " + data.location.country : "") }),
      el("p", { class: "muted", text: `PM2.5 ${cur.pm2_5 ?? "–"} µg/m³ · PM10 ${cur.pm10 ?? "–"} µg/m³ · NO₂ ${cur.no2 ?? "–"} µg/m³` }),
      el("p", { class: "small muted", text: data.sensitive ? "Sensitive-group thresholds applied to your plan." : "General-population thresholds applied." }),
    ]),
  );

  document.getElementById("ai-text").textContent = data.ai.text;
  document.getElementById("ai-source").textContent =
    data.ai.source === "rules" ? (mode === "static" ? "Generated from rules. Add a Gemini key above for an AI-written answer." : "Generated from rules (AI key not configured).") : `Personalised by ${data.ai.source === "gemini" ? "Google Gemini" : "Anthropic Claude"} from the facts below.`;

  const list = document.getElementById("actions");
  list.replaceChildren(...data.actions.map((a) => el("li", {}, [
    el("div", { class: "kind", text: KIND_LABEL[a.kind] || a.kind }),
    el("strong", { text: a.title }),
    el("p", { text: a.detail }),
    el("p", { class: "impact", text: "Impact: " + a.impact }),
  ])));

  const chart = document.getElementById("chart");
  const rows = data.hourly.filter((h) => h.naqi !== null);
  const max = Math.max(100, ...rows.map((h) => h.naqi));
  chart.replaceChildren(...rows.map((h) => {
    const b = el("div", { class: "bar " + catClass(h.category), title: `${fmtTime(h.time)}: NAQI ${h.naqi} (${h.category})` });
    b.style.height = Math.round((h.naqi / max) * 100) + "%";
    return b;
  }));
  const best = data.windows[0];
  chart.setAttribute("aria-label", `Hourly NAQI bar chart for the next ${rows.length} hours, peaking at ${max}.` +
    (best ? ` Cleanest daytime window starts ${fmtTime(best.start)} at about ${best.avg_naqi}.` : ""));

  document.querySelector("#hourly-table tbody").replaceChildren(...rows.map((h) => el("tr", {}, [
    el("td", { text: fmtTime(h.time) }), el("td", { text: String(h.naqi) }),
    el("td", { text: h.category }), el("td", { text: String(h.pm2_5 ?? "–") }),
  ])));

  results.hidden = false;
  statusEl.textContent = `Plan ready for ${data.location.name}. Air quality is ${cur.category}.`;
  document.getElementById("res-h").focus?.();
  results.scrollIntoView({ behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" });
}

form.addEventListener("submit", async (e) => {
  e.preventDefault();
  err.hidden = true;
  const body = readProfile();
  if (body.profile.city.length < 2) return showError("Please enter your city.", document.getElementById("city"));

  btn.disabled = true;
  btn.textContent = "Building your plan…";
  statusEl.textContent = "Fetching the air-quality forecast and building your plan.";
  try {
    if ((await detectMode()) === "static") {
      const key = keyInput.value.trim();
      store.set(key);
      render(await window.SZAEngine.plan(body, key));
    } else {
      const res = await fetch("api/plan", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(typeof data.detail === "string" ? data.detail : "Please check your inputs.");
      render(data);
    }
  } catch (ex) {
    showError(ex.message || "Something went wrong. Please try again.");
  } finally {
    btn.disabled = false;
    btn.textContent = "Get my plan";
  }
});
