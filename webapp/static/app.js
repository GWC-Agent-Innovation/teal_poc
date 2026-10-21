const $ = (s) => document.querySelector(s);
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const chip = (s) => s ? `<span class="chip ${esc(s)}">${esc(String(s).replace("_", " "))}</span>` : `<span class="chip">—</span>`;
const fmt = (v) => v === null || v === undefined ? "—" : typeof v === "number" ? +v.toFixed(3) : typeof v === "object" ? JSON.stringify(v) : v;

let META = null, CURRENT = null, DETAIL = null, POLL = null, VIEWER = null, loadedStl = null;

async function api(path, opts = {}) {
  const r = await fetch(path, { headers: { "Content-Type": "application/json" }, ...opts });
  const body = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(body.detail || r.statusText);
  return body;
}
function toast(msg, ms = 4000) {
  const t = $("#toast"); t.textContent = msg; t.hidden = false;
  clearTimeout(toast._t); toast._t = setTimeout(() => (t.hidden = true), ms);
}

// ---- parts list --------------------------------------------------------------------------------
async function loadParts() {
  const parts = await api("/api/parts");
  $("#parts").innerHTML = parts.map((p) => `
    <li data-part="${esc(p.part)}" class="${p.part === CURRENT ? "active" : ""}">
      <span>${esc(p.part)}</span>${p.running ? chip("RUNNING") : chip(p.last_status)}
    </li>`).join("") || `<li class="muted">No parts yet</li>`;
  document.querySelectorAll("#parts li[data-part]").forEach((li) => li.onclick = () => selectPart(li.dataset.part));
}

async function selectPart(part) {
  CURRENT = part; loadedStl = null;
  history.replaceState(null, "", `#${part}`);
  $("#empty").hidden = true; $("#part").hidden = false;
  await refresh(true);
  loadParts();
}

// ---- detail ------------------------------------------------------------------------------------
async function refresh(first = false) {
  if (!CURRENT) return;
  DETAIL = await api(`/api/parts/${CURRENT}`);
  renderHead(); renderPipeline(); renderBanner(); renderCompare(); renderValidation(); renderPlan(); renderVerification(); renderFiles();
  if (first) { await loadEditor(); await loadQuestions(); }
  renderReviewSide();
  clearInterval(POLL);
  if (DETAIL.running) POLL = setInterval(async () => {
    await refresh();
    if (!DETAIL.running) { clearInterval(POLL); loadParts(); loadEditor(); loadQuestions(); toast(`Run finished for ${CURRENT}`); }
  }, 2000);
}

function renderHead() {
  $("#part-name").textContent = DETAIL.part;
  $("#part-badges").innerHTML = (DETAIL.reviewed ? chip("REVIEWED").replace("chip", "chip PASS") : "") +
    (DETAIL.running ? " " + chip("RUNNING") : "");
  $("#run-stage1").disabled = DETAIL.running || !DETAIL.has_drawing;
  $("#run-stage2").disabled = DETAIL.running;
}

const PLAIN = {
  INSUFFICIENT_CONSTRAINTS: "a feature's position is not dimensioned",
  AMBIGUOUS_INSTANCE: "a dimension does not say which copy of a feature it locates",
  UNKNOWN_REFERENCE: "a dimension refers to something that could not be identified",
  MISSING_REQUIRED_DIMENSION: "a size needed to build a feature is not given",
  EDGE_SELECTION_UNKNOWN: "a fillet/chamfer does not say which edges it applies to",
  UNSUPPORTED_FEATURE: "a feature type the CAD step cannot build yet",
  STAGE1_AMBIGUITY: "the drawing reader was unsure about something",
  STAGE1_MISSING_INFORMATION: "the drawing reader found missing information",
  MULTIVIEW_CONFLICT: "two views give different values",
  ZERO_WALL_THICKNESS: "two features touch (zero wall thickness)",
  FEATURE_OUTSIDE_BASE: "a feature falls outside the part",
  DIMENSION_CHAIN_MISMATCH: "a chain of dimensions does not add up to the overall size",
  PATTERN_NOT_A_GRID: "a group of features cannot be placed from its dimensions",
  GUSSET_ATTACHMENT_UNKNOWN: "a rib does not say which feature it is attached to",
  GUSSET_DIRECTIONS_UNKNOWN: "ribs on a boss do not say which way they run",
  GUSSET_POSITION_UNKNOWN: "a rib's position is not stated", SLOT_ORIENTATION_UNKNOWN: "a slot's direction is not stated",
  HOLE_DEPTH_UNKNOWN: "a hole is neither THRU nor given a depth", LUG_ORIENTATION_UNKNOWN: "an upright ear's direction is not stated",
};
function renderBanner() {
  const el = $("#banner"), st = DETAIL.status.stages || {}, v = DETAIL.validated;
  if (DETAIL.running || !v) { el.hidden = true; return; }
  const codes = {};
  (v.errors || []).forEach((e) => { codes[e.code] = (codes[e.code] || 0) + 1; });
  const list = Object.entries(codes).map(([c, n]) =>
    `<li>${esc(PLAIN[c] || c)}${n > 1 ? ` (×${n})` : ""} <span class="muted mono">${esc(c)}</span></li>`).join("");
  const s5 = st.stage5?.status;
  if (v.status === "FAIL") {
    el.className = "banner fail";
    el.innerHTML = `<div class="text"><b>The geometry is impossible as extracted, so no CAD model is built.</b>
      Fix the values in the Review tab (or re-read the drawing).<ul>${list}</ul></div>
      <div class="btns"><button class="btn primary" data-go="review">Open review</button></div>`;
  } else if (v.status === "REVIEW_REQUIRED" && !(DETAIL.plan?.development_override && s5)) {
    const blocked = new Set((v.errors || []).map((e) => e.feature_id).filter(Boolean));
    const skipped = v.features.filter((f) => f.type === "unsupported" || blocked.has(f.id) ||
      (f.instances || []).some((p) => p.x == null || p.y == null)).length;
    el.className = "banner review";
    el.innerHTML = `<div class="text"><b>The drawing does not define everything needed to build this part, so the pipeline stopped instead of guessing.</b>
      Answer the open questions, or build only what the drawing fully defines now.<ul>${list}</ul></div>
      <div class="btns"><button class="btn primary" data-go="review">Answer questions</button>
      <button class="btn" data-build-partial>Build what's defined${skipped ? ` (skips ${skipped} feature${skipped > 1 ? "s" : ""})` : ""}</button></div>`;
  } else if (DETAIL.plan?.development_override && s5) {
    el.className = "banner review";
    el.innerHTML = `<div class="text"><b>Partial model: only the features the drawing fully defines were built.</b>
      Skipped: ${esc(DETAIL.plan.skipped.map((x) => `${x.feature_id} (${x.reason})`).join("; ") || "none")}.
      Answer the open questions to complete it.</div>
      <div class="btns"><button class="btn primary" data-go="review">Answer questions</button></div>`;
  } else if (s5 === "PASS") {
    el.className = "banner pass";
    el.innerHTML = `<div class="text"><b>Model built and verified against the drawing (${esc(st.stage5.message)}).</b></div>
      <div class="btns"><button class="btn" data-go="verification">See checks</button>
      <button class="btn" data-go="files">Download files</button></div>`;
  } else if (s5 || st.stage4?.status === "FAIL" || st.stage3?.status === "FAIL") {
    const bad = ["stage3", "stage4", "stage5"].map((k) => st[k]).find((x) => x && x.status !== "PASS");
    el.className = "banner fail";
    el.innerHTML = `<div class="text"><b>The CAD step did not pass.</b> ${esc(bad?.message || "")}</div>
      <div class="btns"><button class="btn" data-go="verification">See checks</button></div>`;
  } else { el.hidden = true; return; }
  el.hidden = false;
  el.querySelectorAll("[data-go]").forEach((b) => b.onclick = () => showTab(b.dataset.go));
  const partial = el.querySelector("[data-build-partial]");
  if (partial) partial.onclick = () => { $("#allow-review").checked = true; run("stage2"); };
}

function renderPipeline() {
  const st = DETAIL.status.stages || {};
  $("#pipeline").innerHTML = META.stages.map((s, i) => {
    const x = st[s.id] || {};
    return `<li class="${esc(x.status || "")}"><div class="n">STAGE ${i + 1}</div><div class="t">${esc(s.title)}</div>
      ${chip(x.status || "NOT RUN")}<div class="m">${esc(x.message || "")}</div></li>`;
  }).join("");
}

function renderCompare() {
  $("#drawing-img").src = DETAIL.has_drawing ? `/api/parts/${CURRENT}/drawing?t=${Date.now() % 1e6}` : "";
  const stl = DETAIL.files.find((f) => f.toLowerCase().endsWith(".stl"));
  const ver = DETAIL.verification;
  $("#viewer-note").textContent = ver ? `· verification ${ver.status}` : "";
  if (stl && stl + DETAIL.status.finished !== loadedStl) { loadedStl = stl + DETAIL.status.finished; showStl(`/api/parts/${CURRENT}/file/${stl}?t=${Date.now()}`); }
  if (!stl) { disposeViewer(); $("#viewer").innerHTML = `<div class="placeholder">No model yet</div>`; }
  const renders = ver?.renders || {};
  $("#renders").innerHTML = ["top", "front", "right", "isometric"].filter((k) => renders[k]).map((k) =>
    `<figure><img src="/api/parts/${CURRENT}/file/cad/${renders[k]}?t=${Date.now() % 1e6}" alt="${k} view"><figcaption>${k} (orthographic render of the exported model)</figcaption></figure>`).join("");
}

function issueRows(list) {
  return list.map((e) => `<tr><td>${chip(e.status)}</td><td class="num">${esc(e.stage)}</td><td class="num">${esc(e.code)}</td>
    <td class="num">${esc(e.feature_id || "")}</td><td>${esc(e.message)}</td></tr>`).join("");
}

function renderValidation() {
  const v = DETAIL.validated, el = $("#tab-validation");
  if (!v) { el.innerHTML = `<p class="muted">Stage 2 has not run yet.</p>`; return; }
  const stages = Object.entries(v.validation).map(([k, s]) => `<div>${esc(k)} ${chip(s)}</div>`).join("");
  const feats = v.features.map((f) => {
    const size = ["diameter", "length", "width", "height", "depth", "radius", "distance"].filter((k) => f[k] != null).map((k) => `${k} ${fmt(f[k])}`).join(", ");
    const pos = (f.instances || []).map((p) => `(${fmt(p.x)}, ${fmt(p.y)})`).join(" ");
    return `<tr><td class="num">${esc(f.id)}</td><td>${esc(f.type)}${f.type === "unsupported" ? ` (${esc(f.stage1_type)})` : ""}</td>
      <td class="num">${esc(size)}</td><td class="num">${esc(pos || "—")}</td></tr>`;
  }).join("");
  const b = v.base || {};
  el.innerHTML = `
    <div class="stage-grid"><div>overall ${chip(v.status)}</div>${stages}</div>
    <h3>Base</h3><p class="mono">${fmt(b.length)} × ${fmt(b.width)} × ${fmt(b.thickness)} mm · plan view: ${esc(v.coordinate_system.plan_view)}</p>
    <h3>Features (solved, mm; origin = lower-left of base, bottom face)</h3>
    <div class="table-wrap"><table><tr><th>ID</th><th>Type</th><th>Size</th><th>Positions (x, y)</th></tr>${feats}</table></div>
    <h3>Errors (${v.errors.length})</h3>
    <div class="table-wrap"><table><tr><th>Status</th><th>Stage</th><th>Code</th><th>Feature</th><th>Message</th></tr>${issueRows(v.errors) || `<tr><td colspan=5 class="muted">none</td></tr>`}</table></div>
    <details><summary>Warnings (${v.warnings.length})</summary>
    <div class="table-wrap"><table><tr><th>Status</th><th>Stage</th><th>Code</th><th>Feature</th><th>Message</th></tr>${issueRows(v.warnings)}</table></div></details>`;
}

function renderPlan() {
  const p = DETAIL.plan, el = $("#tab-plan");
  if (!p) { el.innerHTML = `<p class="muted">No CAD plan yet (Stage 3 runs only when validation passes, or with the review override).</p>`; return; }
  const rows = p.operations.map((o) => {
    const { id, operation, feature_id, ...rest } = o;
    const args = Object.entries(rest).filter(([, v]) => v !== null && v !== undefined).map(([k, v]) => `${k}=${fmt(v)}`).join("  ");
    return `<tr><td class="num">${esc(id)}</td><td class="num">${esc(operation)}</td><td class="num">${esc(feature_id || "")}</td><td class="num">${esc(args)}</td></tr>`;
  }).join("");
  const skipped = p.skipped.map((s) => `<li><b class="mono">${esc(s.feature_id)}</b> (${esc(s.type)}): ${esc(s.reason)}</li>`).join("");
  el.innerHTML = `${p.development_override ? `<p>${chip("REVIEW_REQUIRED")} Development override: this plan is incomplete.</p>` : ""}
    ${skipped ? `<h3>Skipped features</h3><ul>${skipped}</ul>` : ""}
    <h3>Operations (${p.operations.length})</h3>
    <div class="table-wrap"><table><tr><th>ID</th><th>Operation</th><th>Feature</th><th>Parameters (mm, deg)</th></tr>${rows}</table></div>
    <p class="muted mono">expected volume ${fmt(p.expected.volume_mm3)} mm³ · bbox ${fmt(p.expected.bbox)}</p>`;
}

function renderVerification() {
  const v = DETAIL.verification, el = $("#tab-verification");
  if (!v) { el.innerHTML = `<p class="muted">Stage 5 has not run yet.</p>`; return; }
  const rows = v.checks.map((c) => `<tr><td>${chip(c.ok ? "PASS" : (c.severity_if_failed || "FAIL"))}</td><td class="num">${esc(c.category)}</td>
    <td>${esc(c.check)}</td><td class="num">${esc(fmt(c.expected))}</td><td class="num">${esc(fmt(c.measured))}</td>
    <td class="num">${esc(c.drawing_evidence || "")}</td></tr>`).join("");
  const summary = Object.entries(v.summary).map(([k, s]) => `<div>${esc(k)}: ${s.passed}✓ ${s.failed ? `<b style="color:var(--fail)">${s.failed}✗</b>` : ""}</div>`).join("");
  el.innerHTML = `<div class="stage-grid"><div>overall ${chip(v.status)}</div>${summary}</div>
    <div class="table-wrap"><table><tr><th>Result</th><th>Category</th><th>Check</th><th>Expected (drawing)</th><th>Measured (CAD)</th><th>Drawing evidence</th></tr>${rows}</table></div>`;
}

function renderFiles() {
  const groups = { "CAD": (f) => f.startsWith("cad/") && !f.includes("render_"), "Reports": (f) => f.endsWith(".json"), "Logs": (f) => f.startsWith("logs/") };
  $("#tab-files").innerHTML = Object.entries(groups).map(([g, fn]) => {
    const items = DETAIL.files.filter(fn).map((f) => `<li><a href="/api/parts/${CURRENT}/file/${f}" target="_blank">${esc(f)}</a></li>`).join("");
    return `<h3>${g}</h3><ul class="mono">${items || `<li class="muted">none</li>`}</ul>`;
  }).join("");
}

// ---- review ------------------------------------------------------------------------------------
async function loadEditor() {
  try {
    const g = await api(`/api/parts/${CURRENT}/geometry`);
    $("#geom-editor").value = g.geometry ? JSON.stringify(g.geometry, null, 2) : "";
    $("#geom-source").textContent = g.geometry ? `editing ${g.source}` : "no geometry yet";
  } catch { $("#geom-editor").value = ""; }
}
async function loadQuestions() {
  const form = $("#questions");
  try {
    const { questions } = await api(`/api/parts/${CURRENT}/questions`);
    form.innerHTML = questions.map(renderQuestion).join("") || `<p class="muted">No open questions.</p>`;
    $("#apply-answers").disabled = !questions.length;
  } catch (e) { form.innerHTML = `<p class="muted">${esc(e.message)}</p>`; }
}
function renderQuestion(q) {
  const head = `<div class="qt">${esc(q.question)}</div><div class="why">${esc(q.why || "")}</div>`;
  const name = esc(q.id);
  let body = "";
  if (q.kind === "value") {
    body = `<input type="number" step="any" name="${name}" placeholder="mm"> mm`;
  } else if (q.kind === "position") {
    body = `<table><tr><th>#</th><th>x (mm from left)</th><th>y (mm from front)</th></tr>${q.instances.map((p, i) =>
      `<tr><td>${i + 1}</td><td><input type="number" step="any" data-q="${name}" data-i="${i}" data-axis="x" value="${p.x ?? ""}"></td>
       <td><input type="number" step="any" data-q="${name}" data-i="${i}" data-axis="y" value="${p.y ?? ""}"></td></tr>`).join("")}</table>
      <span class="muted">base is ${esc(q.base[0])} × ${esc(q.base[1])} mm</span>`;
  } else if (q.kind === "choice") {
    body = `<select name="${name}"><option value="">— not decided —</option>${q.options.map((o) => `<option>${esc(o)}</option>`).join("")}</select>`;
  } else if (q.kind === "depth") {
    body = `<select name="${name}"><option value="">— not decided —</option><option value="through">through</option></select>
      or blind depth <input type="number" step="any" data-depth="${name}" placeholder="mm">`;
  } else if (q.kind === "acknowledge") {
    body = (q.options.length ? `<ul class="muted">${q.options.map((o) => `<li>${esc(o)}</li>`).join("")}</ul>` : "") +
      `<label class="opt"><input type="checkbox" name="${name}" value="resolved"> resolved by my answers / not relevant</label>`;
  } else if (q.kind === "conflict") {
    body = q.options.map((o) => `<label class="opt"><input type="radio" name="${name}" value="${esc(o)}"> ${esc(o)}</label>`).join("");
  }
  return `<div class="q">${head}${body}</div>`;
}
function collectAnswers() {
  const form = $("#questions"), answers = {};
  form.querySelectorAll("input[type=number][name], select[name]").forEach((el) => { if (el.value !== "") answers[el.name] = el.value; });
  form.querySelectorAll("input[data-depth]").forEach((el) => { if (el.value !== "") answers[el.dataset.depth] = el.value; });
  form.querySelectorAll("input[type=checkbox][name]:checked, input[type=radio][name]:checked").forEach((el) => { answers[el.name] = el.value; });
  const pos = {};
  form.querySelectorAll("input[data-q]").forEach((el) => {
    const rows = (pos[el.dataset.q] ||= []);
    const r = (rows[+el.dataset.i] ||= {});
    r[el.dataset.axis] = el.value === "" ? null : +el.value;
  });
  Object.entries(pos).forEach(([k, rows]) => { if (rows.some((r) => r.x != null && r.y != null)) answers[k] = rows; });
  return answers;
}
$("#apply-answers").onclick = async () => {
  const answers = collectAnswers();
  if (!Object.keys(answers).length) { $("#answers-msg").textContent = "Answer at least one question."; return; }
  try {
    const r = await api(`/api/parts/${CURRENT}/answers`, { method: "POST", body: JSON.stringify({
      answers, note: $("#answers-note").value, allow_review: $("#allow-review").checked, dry_run: $("#dry-run").checked }) });
    $("#answers-msg").textContent = `Applied: ${r.applied.join("; ")}. Re-validating…`;
    $("#answers-note").value = "";
    await refresh(); loadParts();
  } catch (e) { $("#answers-msg").textContent = e.message; }
};

function renderReviewSide() {
  $("#review-log").innerHTML = DETAIL.review_log.slice().reverse().map((r) => `<li><b>${esc(r.time)}</b> · ${esc(r.note)}
    <details><summary>${r.changes.length} change(s)</summary><pre class="mono">${esc(r.changes.join("\n"))}</pre></details></li>`).join("")
    || `<li class="muted">No human review yet.</li>`;
}

// ---- 3D viewer ---------------------------------------------------------------------------------
function disposeViewer() { if (VIEWER) { VIEWER.stop(); VIEWER = null; } }
async function showStl(url) {
  disposeViewer();
  const el = $("#viewer"); el.innerHTML = `<div class="placeholder">Loading model…</div>`;
  try {
    const THREE = await import("three");
    const { STLLoader } = await import("three/addons/loaders/STLLoader.js");
    const { OrbitControls } = await import("three/addons/controls/OrbitControls.js");
    const geom = await new STLLoader().loadAsync(url);
    geom.computeVertexNormals(); geom.computeBoundingBox();
    const box = geom.boundingBox, size = new THREE.Vector3(), center = new THREE.Vector3();
    box.getSize(size); box.getCenter(center);
    el.innerHTML = "";
    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    renderer.setPixelRatio(window.devicePixelRatio); renderer.setSize(el.clientWidth, el.clientHeight);
    el.appendChild(renderer.domElement);
    const scene = new THREE.Scene();
    const mesh = new THREE.Mesh(geom, new THREE.MeshStandardMaterial({ color: 0x9fb3c8, metalness: 0.2, roughness: 0.55 }));
    mesh.position.sub(center); scene.add(mesh);
    const edges = new THREE.LineSegments(new THREE.EdgesGeometry(geom, 25), new THREE.LineBasicMaterial({ color: 0x2d3b48 }));
    edges.position.copy(mesh.position); scene.add(edges);
    scene.add(new THREE.HemisphereLight(0xffffff, 0x445566, 1.2));
    const dl = new THREE.DirectionalLight(0xffffff, 1.6); dl.position.set(1, 2, 1.5); scene.add(dl);
    const r = size.length();
    const camera = new THREE.PerspectiveCamera(35, el.clientWidth / el.clientHeight, r / 100, r * 20);
    camera.position.set(r * 0.9, r * 0.8, r * 1.1);   // STL is Y-up (SolidWorks model frame)
    const controls = new OrbitControls(camera, renderer.domElement); controls.enableDamping = true;
    let raf = 0; const loop = () => { controls.update(); renderer.render(scene, camera); raf = requestAnimationFrame(loop); }; loop();
    const onResize = () => { renderer.setSize(el.clientWidth, el.clientHeight); camera.aspect = el.clientWidth / el.clientHeight; camera.updateProjectionMatrix(); };
    window.addEventListener("resize", onResize);
    VIEWER = { stop() { cancelAnimationFrame(raf); window.removeEventListener("resize", onResize); renderer.dispose(); } };
  } catch (e) {
    el.innerHTML = `<div class="placeholder">3D viewer unavailable (${esc(e.message)}). See the renders below.</div>`;
  }
}

// ---- actions -----------------------------------------------------------------------------------
async function run(start) {
  try {
    await api(`/api/parts/${CURRENT}/run`, { method: "POST", body: JSON.stringify({
      start, allow_review: $("#allow-review").checked, dry_run: $("#dry-run").checked }) });
    toast(start === "stage1" ? "Reading the drawing with the VLM (about 1–2 min)…" : "Running stages 2–5…");
    await refresh(); loadParts();
  } catch (e) { toast(e.message); }
}
$("#run-stage1").onclick = () => run("stage1");
$("#run-stage2").onclick = () => run("stage2");

$("#save-review").onclick = async () => {
  let geometry;
  try { geometry = JSON.parse($("#geom-editor").value); } catch (e) { $("#editor-msg").textContent = `Invalid JSON: ${e.message}`; return; }
  const note = $("#review-note").value.trim();
  if (!note) { $("#editor-msg").textContent = "A review note is required."; return; }
  try {
    const r = await api(`/api/parts/${CURRENT}/geometry`, { method: "PUT", body: JSON.stringify({ geometry, note, allow_review: $("#allow-review").checked, dry_run: $("#dry-run").checked }) });
    $("#editor-msg").textContent = `Saved ${r.changes.length} change(s); re-validating…`; $("#review-note").value = "";
    await refresh(); loadParts();
  } catch (e) { $("#editor-msg").textContent = e.message; }
};
$("#discard-review").onclick = async () => {
  if (!confirm("Discard the reviewed geometry and go back to the Stage 1 extraction?")) return;
  await api(`/api/parts/${CURRENT}/review/discard`, { method: "POST" }); await loadEditor(); await refresh(); toast("Review discarded");
};

$("#upload").onchange = async (ev) => {
  const f = ev.target.files[0]; if (!f) return;
  const fd = new FormData(); fd.append("file", f);
  try {
    const r = await fetch("/api/upload", { method: "POST", body: fd }); const b = await r.json();
    if (!r.ok) throw new Error(b.detail); toast(`Uploaded ${f.name}; Stage 1 started`); await loadParts(); selectPart(b.part);
  } catch (e) { toast(e.message); }
  ev.target.value = "";
};

function showTab(name) {
  const b = document.querySelector(`#tabs button[data-tab="${name}"]`) || document.querySelector("#tabs button");
  document.querySelectorAll("#tabs button").forEach((x) => x.classList.toggle("active", x === b));
  document.querySelectorAll(".tab").forEach((t) => (t.hidden = t.id !== `tab-${b.dataset.tab}`));
  if (CURRENT) history.replaceState(null, "", `#${CURRENT}/${b.dataset.tab}`);
}
document.querySelectorAll("#tabs button").forEach((b) => b.onclick = () => showTab(b.dataset.tab));

(async () => {
  META = await api("/api/meta");
  await loadParts();
  const [part, tab] = decodeURIComponent(location.hash.slice(1)).split("/");
  if (part) { await selectPart(part); if (tab) showTab(tab); }
})();
