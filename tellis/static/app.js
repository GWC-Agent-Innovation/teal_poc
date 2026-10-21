// Tellis frontend: upload, live status of image -> TRELLIS.2 -> 3D model -> SolidWorks, interactive 3D.
const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmt = (v, d = 2) => (typeof v === "number" ? (+v.toFixed(d)).toLocaleString() : v ?? "—");

let CURRENT = null, JOB = null, POLL = 0, VIEWER = null, VIEW = "model", VIEW_KEY = "";

// what each view shows, and the frame its file is in (everything is displayed Y-up)
const VIEWS = {
  trellis: { file: (f) => ["trellis/mesh.glb", ...Object.keys(f).filter((k) => k.startsWith("trellis/uploaded"))].find((k) => f[k]), label: "TRELLIS.2 output" },
  model: { file: (f) => (f["model.stl"] ? "model.stl" : null), zUp: true, label: "prepared mesh, mm" },
  solidworks: { file: (f) => (f["cad/part.STL"] ? "cad/part.STL" : null), label: "exported by SolidWorks, mm" },
};
const DRAWING_VIEWS = {
  trace: { file: (f) => f["overlay.png"] ? "overlay.png" : null, label: "Trace fitted to the drawing" },
  part: { file: (f) => f["cad/part.STL"] ? "cad/part.STL" : null, zUp: true, label: "SolidWorks part" },
};

async function api(path, opts = {}) {
  const r = await fetch(path, opts);
  const body = r.headers.get("content-type")?.includes("json") ? await r.json() : await r.text();
  if (!r.ok) throw new Error(body?.detail || body || `HTTP ${r.status}`);
  return body;
}
function toast(msg) {
  const t = $("#toast"); t.textContent = msg; t.hidden = false;
  clearTimeout(toast.t); toast.t = setTimeout(() => (t.hidden = true), 5000);
}
const fileUrl = (p) => `/api/jobs/${encodeURIComponent(CURRENT)}/files/${p}`;
const running = (j) => ["queued", "running"].includes(j?.status);

// ---- upload --------------------------------------------------------------------------------------
const drop = $("#drop"), imageInput = $("#image");
function previewImage() {
  const f = imageInput.files[0]; if (!f) return;
  const img = $("#drop-preview"); img.src = URL.createObjectURL(f); img.hidden = false;
  $("#drop-text").textContent = f.name;
}
imageInput.onchange = previewImage;
$("#workflow").onchange = () => {
  const mode = $("#workflow").value;
  const drawing = mode === "drawing", multi = mode === "multiview";
  document.querySelectorAll(".image-option").forEach((el) => el.hidden = drawing || multi);
  document.querySelectorAll(".drawing-option").forEach((el) => el.hidden = !drawing);
  document.querySelectorAll(".multiview-option").forEach((el) => el.hidden = !multi);
};
["dragenter", "dragover"].forEach((e) => drop.addEventListener(e, (ev) => { ev.preventDefault(); drop.classList.add("over"); }));
["dragleave", "drop"].forEach((e) => drop.addEventListener(e, (ev) => { ev.preventDefault(); drop.classList.remove("over"); }));
drop.addEventListener("drop", (ev) => { if (ev.dataTransfer.files.length) { imageInput.files = ev.dataTransfer.files; previewImage(); } });

$("#upload").onsubmit = async (ev) => {
  ev.preventDefault();
  const fd = new FormData();
  fd.append("image", imageInput.files[0]);
  const mode = $("#workflow").value;
  const drawing = mode === "drawing", multi = mode === "multiview";
  if (drawing) {
    fd.append("thickness", $("#thickness").value);
    fd.append("relief", $("#relief").value);
    if ($("#height").value) fd.append("height_mm", $("#height").value);
  } else if (!multi) {
    if ($("#mesh").files[0]) fd.append("mesh", $("#mesh").files[0]);
    if ($("#size").value) fd.append("size_mm", $("#size").value);
    if ($("#faces").value) fd.append("max_faces", $("#faces").value);
    fd.append("dry_run", $("#dry-run").checked);
  }
  $("#submit").disabled = true;
  try {
    const job = await api(multi ? "/api/multiview" : drawing ? "/api/drawings" : "/api/jobs", { method: "POST", body: fd });
    toast(multi ? "Engineering sheet queued for multi-view interpretation, validation, CAD, and verification." : drawing ? "Drawing queued for dimension reading, tracing, and SolidWorks." : "Queued. TRELLIS.2 takes about a minute on a GPU; the SolidWorks import can take several minutes.");
    await loadJobs(); select(job.id);
  } catch (e) { toast(e.message); } finally { $("#submit").disabled = false; }
};

// ---- jobs ----------------------------------------------------------------------------------------
async function loadJobs() {
  const jobs = await api("/api/jobs");
  $("#jobs").innerHTML = jobs.map((j) => `<li data-id="${esc(j.id)}" class="${j.id === CURRENT ? "active" : ""}">
      <span title="${esc(j.id)}">${j.workflow === "multiview" ? "Multi-view · " : j.workflow === "drawing" ? "Trace · " : ""}${esc(j.id.replace(/^\d{8}-\d{6}-/, ""))}</span>
      <span class="badge s-${esc(j.status)}">${esc(j.status)}</span></li>`).join("")
    || `<li class="muted">No jobs yet.</li>`;
  document.querySelectorAll("#jobs li[data-id]").forEach((li) => (li.onclick = () => select(li.dataset.id)));
}
async function loadMeta() {
  try {
    const m = await api("/api/meta");
    $("#server-state").textContent = `TRELLIS.2: ${m.trellis_provider}` + (m.running ? ` · running ${m.running}` : "")
      + (m.queued ? ` · ${m.queued} queued` : "");
  } catch { $("#server-state").textContent = "backend unreachable"; }
}
function select(id) {
  CURRENT = id; VIEW_KEY = ""; VIEW = null; location.hash = id;
  document.querySelectorAll("#jobs li").forEach((li) => li.classList.toggle("active", li.dataset.id === id));
  refresh();
}
async function refresh() {
  clearTimeout(POLL);
  if (!CURRENT) return;
  try { JOB = await api(`/api/jobs/${encodeURIComponent(CURRENT)}`); } catch (e) { toast(e.message); return; }
  render(JOB);
  if (running(JOB)) POLL = setTimeout(() => { refresh(); loadMeta(); }, 1500);
  else loadJobs();
}

// ---- rendering -----------------------------------------------------------------------------------
function render(j) {
  if (j.workflow === "multiview") return renderMultiView(j);
  if (j.workflow === "drawing") return renderDrawing(j);
  $("#wire").parentElement.hidden = false;
  $("#views").querySelectorAll('button[data-view="trace"],button[data-view="part"]').forEach((b) => b.hidden = true);
  $("#views").querySelectorAll('button[data-view="trellis"],button[data-view="model"],button[data-view="solidworks"]').forEach((b) => b.hidden = false);
  $("#empty").hidden = true; $("#job").hidden = false;
  const opts = j.options || {}, rep = j.mesh_report, cad = j.cad_result;
  $("#job-title").textContent = j.id.replace(/^\d{8}-\d{6}-/, "");
  $("#input-img").src = fileUrl(j.image);
  $("#job-status").innerHTML = `<span class="badge s-${esc(j.status)}">${esc(j.status)}</span> · ${esc(j.id)}`
    + (opts.dry_run ? " · dry run" : "") + (opts.size_mm ? ` · longest side ${esc(opts.size_mm)} mm` : "");
  const warns = [...(rep?.warnings || []), ...(cad?.warnings || [])];
  $("#warnings").innerHTML = (j.error ? `<div style="color:var(--bad)">✕ ${esc(j.error)}</div>` : "")
    + warns.map((w) => `<div>⚠ ${esc(w)}</div>`).join("");

  const st = j.stages || {};
  const exported = j.files["cad/part.SLDPRT"] || j.files["cad/part.STEP"];
  const state = { input: "done", ...Object.fromEntries(Object.entries(st).map(([k, v]) => [k, v.status])),
    export: exported ? "done" : st.solidworks?.status === "failed" ? "failed" : "pending" };
  document.querySelectorAll("#flow .node").forEach((n) => {
    n.className = `node ${state[n.dataset.stage] || "pending"}`;
    const info = st[n.dataset.stage];
    n.title = info ? `${info.status}${info.seconds != null ? ` (${info.seconds}s)` : ""}: ${info.message || ""}` : "";
  });

  $("#report").innerHTML = rep ? `<table>
      <tr><td>Source</td><td>${esc(rep.source)}</td></tr>
      <tr><td>Faces</td><td class="num">${fmt(rep.faces_in, 0)} → ${fmt(rep.faces_out, 0)}</td></tr>
      <tr><td>Size (X × Y × Z)</td><td class="num">${rep.extents_mm.map((v) => fmt(v)).join(" × ")} mm</td></tr>
      <tr><td>Closed (watertight)</td><td>${rep.watertight ? "yes" : "<span style='color:var(--warn)'>no</span>"}</td></tr>
      <tr><td>Volume</td><td class="num">${rep.volume_mm3 != null ? fmt(rep.volume_mm3, 0) + " mm³" : "—"}</td></tr></table>`
    : `<p class="muted">${esc(st.mesh?.message || st.trellis?.message || "Waiting for TRELLIS.2.")}</p>`;

  if (!running(j) && $("#rerun-size").dataset.job !== j.id) {
    $("#rerun-size").value = opts.size_mm ?? ""; $("#rerun-faces").value = opts.max_faces ?? "";
    $("#rerun-dry").checked = !!opts.dry_run; $("#rerun-size").dataset.job = j.id;
  }
  $("#rerun").disabled = running(j) || !VIEWS.trellis.file(j.files);

  const m = cad?.measured;
  $("#cad").innerHTML = !cad ? `<p class="muted">${esc(st.solidworks?.message || "Not run yet.")}</p>`
    : cad.status !== "PASS" ? cad.errors.map((e) => `<p style="color:var(--bad)">${esc(e.code)}: ${esc(e.message)}</p>`).join("")
    : `<table><tr><td>Body</td><td>${m.solid_bodies ? `${m.solid_bodies} solid` : `${m.surface_bodies} surface`}</td></tr>
        <tr><td>B-rep faces</td><td class="num">${fmt(m.faces, 0)}</td></tr>
        <tr><td>Volume</td><td class="num">${m.volume_mm3 != null ? fmt(m.volume_mm3, 0) + " mm³" : "—"}</td></tr>
        <tr><td>Bounding box</td><td class="num">${m.bbox ? [0, 1, 2].map((i) => fmt(m.bbox[i + 3] - m.bbox[i])).join(" × ") + " mm" : "—"}</td></tr></table>`;
  $("#files").innerHTML = Object.entries(j.files).sort().map(([p, label]) =>
    `<li><span>${esc(label)} <small>${esc(p)}</small></span><a href="${fileUrl(p)}" download>download</a></li>`).join("");

  // default view: the furthest stage that has a model
  if (!VIEW) VIEW = ["solidworks", "model", "trellis"].find((v) => VIEWS[v].file(j.files)) || "model";
  else if (!VIEWS[VIEW].file(j.files)) VIEW = ["solidworks", "model", "trellis"].find((v) => VIEWS[v].file(j.files)) || VIEW;
  document.querySelectorAll("#views button").forEach((b) => {
    b.classList.toggle("active", b.dataset.view === VIEW); b.disabled = !VIEWS[b.dataset.view].file(j.files);
  });
  const file = VIEWS[VIEW].file(j.files);
  const key = `${j.id}|${VIEW}|${file}|${j.stages?.[VIEW === "model" ? "mesh" : VIEW]?.seconds ?? ""}`;
  if (key !== VIEW_KEY) { VIEW_KEY = key; file ? show(fileUrl(file), VIEWS[VIEW]) : placeholder(running(j) ? "Working…" : "No model."); }
}

function renderMultiView(j) {
  $("#empty").hidden = true; $("#job").hidden = false;
  const stages = j.stages || {}, v = j.validated, vr = j.validation_report, ver = j.verification, cad = j.cad_result;
  $("#job-title").textContent = j.id.replace(/^\d{8}-\d{6}-/, "");
  $("#input-img").src = fileUrl(j.image);
  $("#job-status").innerHTML = `<span class="badge s-${esc(j.status)}">${esc(j.status.replaceAll("_", " "))}</span> · multi-view drawing · ${esc(j.id)}`;
  const issues = [...(v?.errors || []), ...(v?.warnings || [])];
  $("#warnings").innerHTML = j.error ? `<div style="color:var(--bad)">✕ ${esc(j.error)}</div>`
    : issues.slice(0, 5).map(x => `<div>${esc(x.code)}: ${esc(x.message)}</div>`).join("");
  const labels = { input: "Engineering sheet", stage1: "Read all views", stage2: "Solve dimensions", stage3: "Plan CAD", stage4: "Build part", stage5: "Verify model" };
  const states = { input: "done", ...Object.fromEntries(Object.entries(labels).filter(([k]) => k !== "input").map(([k]) => [k, stages[k]?.status?.toLowerCase() || "pending"])) };
  $("#flow").innerHTML = Object.entries(labels).map(([key, label], i) => `${i ? '<div class="arrow">→</div>' : ""}<div class="node ${esc(states[key])}" title="${esc(stages[key]?.message || "")}">${label}<small>${esc(stages[key]?.message || "")}</small></div>`).join("");
  $("#views").querySelectorAll('button[data-view="trace"],button[data-view="part"],button[data-view="trellis"],button[data-view="model"]').forEach(b => b.hidden = true);
  const partButton = $("#views button[data-view=\"solidworks\"]");
  partButton.hidden = false; partButton.textContent = "Built CAD part"; partButton.disabled = !VIEWS.solidworks.file(j.files);
  $("#wire").parentElement.hidden = !VIEWS.solidworks.file(j.files);
  if (!VIEW || !["solidworks"].includes(VIEW)) VIEW = "solidworks";
  document.querySelectorAll("#views button").forEach(b => b.classList.toggle("active", b.dataset.view === VIEW));
  const file = VIEWS.solidworks.file(j.files), key = `${j.id}|multi|${file || ""}`;
  if (key !== VIEW_KEY) { VIEW_KEY = key; file ? show(fileUrl(file), VIEWS.solidworks) : placeholder(running(j) ? "Interpreting views and dimensions…" : "No CAD model was produced."); }
  const checks = ver?.checks || [];
  const passed = checks.filter(c => c.ok).length;
  const s2 = v?.validation || {};
  $("#report").innerHTML = `<table>
    <tr><td>Extraction</td><td>${esc(j.extraction?.extraction_status || "running")}</td></tr>
    <tr><td>Geometry validation</td><td>${esc(v?.status || "pending")}</td></tr>
    <tr><td>View interpretation</td><td>${Object.entries(s2).map(([k,x]) => `${esc(k)}: ${esc(x)}`).join(" · ") || "pending"}</td></tr>
    <tr><td>Features identified</td><td>${fmt(v?.features?.length, 0)}</td></tr>
    <tr><td>Model verification</td><td>${ver ? `${esc(ver.status)} · ${passed}/${checks.length} checks passed` : "pending"}</td></tr>
    </table>${checks.length ? `<details><summary>Verification checks</summary><ul>${checks.map(c => `<li>${c.ok ? "✓" : "⚠"} ${esc(c.check)} — expected ${esc(fmt(c.expected))}, measured ${esc(fmt(c.measured))}</li>`).join("")}</ul></details>` : ""}
    ${j.status === "review_required" ? `<p class="muted">The drawing leaves dimensions or feature relationships unresolved. Tellis stopped before building a guessed model. Review the geometry and answer open questions in the engineering review UI: run <code>python -m webapp</code>, then open <a href="http://127.0.0.1:8000/#${encodeURIComponent(j.id)}" target="_blank">this part's review page</a>.</p>` : ""}`;
  const measured = cad?.measured;
  $("#cad").innerHTML = cad ? `<p>${esc(cad.status)} · ${fmt(measured?.bodies, 0)} body/bodies · ${fmt(measured?.rebuild_errors, 0)} rebuild errors</p>
    <p>Measured volume: ${measured?.volume_mm3 != null ? `${fmt(measured.volume_mm3)} mm³` : "—"}</p>` : `<p class="muted">${esc(stages.stage4?.message || "CAD not built: validation may need review first.")}</p>`;
  $("#rerun").disabled = true;
  $("#files").innerHTML = Object.entries(j.files || {}).sort().map(([p, label]) => `<li><span>${esc(label)} <small>${esc(p)}</small></span><a href="${fileUrl(p)}" download>download</a></li>`).join("");
}

function renderDrawing(j) {
  $("#empty").hidden = true; $("#job").hidden = false;
  const opts = j.options || {}, result = j.result || {}, stages = j.stages || {};
  const dims = j.dimensions || {};
  $("#job-title").textContent = j.id.replace(/^\d{8}-\d{6}-/, "");
  $("#input-img").src = fileUrl(j.image);
  $("#job-status").innerHTML = `<span class="badge s-${esc(j.status)}">${esc(j.status)}</span> · dimensioned drawing · ${esc(j.id)}`
    + ` · thickness ${esc(opts.thickness)} mm · relief ${esc(opts.relief)} mm`;
  $("#warnings").innerHTML = j.error ? `<div style="color:var(--bad)">✕ ${esc(j.error)}</div>` : "";
  const steps = { input: "done", dimensions: stages.dimensions?.status, trace: stages.trace?.status, cad: stages.cad?.status, export: j.files["cad/part.SLDPRT"] ? "done" : (stages.cad?.status === "failed" ? "failed" : "pending") };
  const labels = { input: "Drawing", dimensions: "Read dimensions", trace: "Trace outline", cad: "Build SolidWorks", export: "SLDPRT / STEP" };
  $("#flow").innerHTML = Object.entries(labels).map(([key, label], i) => `${i ? '<div class="arrow">→</div>' : ""}<div class="node ${esc(steps[key] || "pending")}" title="${esc(stages[key]?.message || "")}">${label}<small>${esc(stages[key]?.message || "")}</small></div>`).join("");
  const available = Object.keys(DRAWING_VIEWS).filter((key) => DRAWING_VIEWS[key].file(j.files));
  $("#views").querySelectorAll('button[data-view="trellis"],button[data-view="model"],button[data-view="solidworks"]').forEach((b) => b.hidden = true);
  for (const key of Object.keys(DRAWING_VIEWS)) {
    let b = $(`#views button[data-view="${key}"]`);
    if (!b) { b = document.createElement("button"); b.dataset.view = key; $("#views").prepend(b); }
    b.hidden = false; b.textContent = key === "trace" ? "Trace overlay" : "3D SolidWorks part";
    b.disabled = !available.includes(key); b.classList.toggle("active", VIEW === key);
    b.onclick = () => { VIEW = key; VIEW_KEY = ""; renderDrawing(JOB); };
    $("#views").prepend(b);
  }
  $("#wire").parentElement.hidden = !available.includes("part");
  const measured = j.cad_result?.measured || result.measured;
  $("#report").innerHTML = `<table>
    <tr><td>Overall height</td><td>${fmt(dims.overall_height)} mm</td></tr>
    <tr><td>Symmetric</td><td>${dims.symmetric_about_vertical_axis ? "yes" : "no"}</td></tr>
    <tr><td>Fitted loops / entities</td><td>${fmt(result.loops, 0)} / ${fmt(result.entities, 0)}</td></tr>
    <tr><td>Trace max deviation</td><td>${fmt(result.max_deviation_mm)} mm</td></tr>
    <tr><td>Expected size</td><td>${result.expected?.bbox_mm?.map(v => fmt(v)).join(" × ") || "—"} mm</td></tr>
    <tr><td>Measured size</td><td>${measured?.bbox ? [0,1,2].map(i => fmt(measured.bbox[i+3] - measured.bbox[i])).join(" × ") : "—"} mm</td></tr>
    <tr><td>Measured volume</td><td>${measured?.volume_mm3 != null ? `${fmt(measured.volume_mm3, 0)} mm³` : "—"}</td></tr></table>`;
  const errors = j.cad_result?.errors || result.errors || [];
  $("#cad").innerHTML = !j.cad_result && !result.measured ? `<p class="muted">${esc(stages.cad?.message || "CAD not built yet.")}</p>`
    : errors.length ? errors.map(e => `<p style="color:var(--bad)">${esc(e.code || "CAD")}: ${esc(e.message || e)}</p>`).join("")
    : `<p>${esc(j.cad_result?.status || result.status || "CAD status pending")} · ${fmt(measured?.bodies, 0)} body/bodies · ${fmt(measured?.rebuild_errors, 0)} rebuild errors</p>`;
  $("#rerun").disabled = true;
  $("#files").innerHTML = Object.entries(j.files || {}).sort().map(([p, label]) => `<li><span>${esc(label)} <small>${esc(p)}</small></span><a href="${fileUrl(p)}" download>download</a></li>`).join("");
  const key = `${j.id}|${VIEW}|${DRAWING_VIEWS[VIEW]?.file(j.files) || ""}`;
  if (!available.includes(VIEW)) VIEW = available[0] || "trace";
  document.querySelectorAll("#views button").forEach(b => b.classList.toggle("active", b.dataset.view === VIEW));
  const file = DRAWING_VIEWS[VIEW]?.file(j.files);
  if (key !== VIEW_KEY) {
    VIEW_KEY = key;
    if (file?.endsWith(".png")) { disposeViewer(); $("#viewer").innerHTML = `<img class="overlay-view" src="${fileUrl(file)}" alt="Drawing with fitted trace"><div class="caption">${esc(DRAWING_VIEWS[VIEW].label)}</div>`; }
    else if (file) show(fileUrl(file), DRAWING_VIEWS[VIEW]);
    else placeholder(running(j) ? "Working…" : "No drawing preview available.");
  }
}

document.querySelectorAll("#views button").forEach((b) => (b.onclick = () => { VIEW = b.dataset.view; if (JOB) render(JOB); }));
$("#wire").onchange = () => VIEWER?.wire($("#wire").checked);

// ---- 3D viewer -----------------------------------------------------------------------------------
function placeholder(msg) { disposeViewer(); $("#viewer").innerHTML = `<div class="placeholder">${esc(msg)}</div>`; }
function disposeViewer() { if (VIEWER) { VIEWER.stop(); VIEWER = null; } }

async function show(url, view) {
  placeholder("Loading model…");
  const el = $("#viewer");
  try {
    const THREE = await import("three");
    const { OrbitControls } = await import("three/addons/controls/OrbitControls.js");
    let obj;
    if (url.endsWith(".glb") || url.endsWith(".gltf")) {
      const { GLTFLoader } = await import("three/addons/loaders/GLTFLoader.js");
      obj = (await new GLTFLoader().loadAsync(url)).scene;          // textured, as TRELLIS.2 made it
    } else if (url.toLowerCase().endsWith(".stl")) {
      const { STLLoader } = await import("three/addons/loaders/STLLoader.js");
      const geom = await new STLLoader().loadAsync(url);
      if (view.zUp) geom.rotateX(-Math.PI / 2);                    // part frame is Z-up; display is Y-up
      geom.computeVertexNormals();
      obj = new THREE.Mesh(geom, new THREE.MeshStandardMaterial({ color: 0x9fb3c8, metalness: 0.2, roughness: 0.55 }));
    } else { placeholder("This mesh format is shown in the 3D model view after the job runs."); return; }

    const box = new THREE.Box3().setFromObject(obj), size = new THREE.Vector3(), center = new THREE.Vector3();
    box.getSize(size); box.getCenter(center); obj.position.sub(center);
    const r = Math.max(size.length(), 1e-6);

    el.innerHTML = "";
    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    renderer.setPixelRatio(window.devicePixelRatio); renderer.setSize(el.clientWidth, el.clientHeight);
    el.appendChild(renderer.domElement);
    const scene = new THREE.Scene(); scene.add(obj);
    scene.add(new THREE.HemisphereLight(0xffffff, 0x445566, 1.4));
    const dl = new THREE.DirectionalLight(0xffffff, 1.5); dl.position.set(1, 2, 1.5); scene.add(dl);
    const grid = new THREE.GridHelper(r * 1.5, 20, 0x888888, 0xbbbbbb);
    grid.position.y = -size.y / 2; grid.material.transparent = true; grid.material.opacity = 0.35; scene.add(grid);
    const camera = new THREE.PerspectiveCamera(35, el.clientWidth / el.clientHeight, r / 100, r * 20);
    camera.position.set(r * 0.9, r * 0.7, r * 1.1);
    const controls = new OrbitControls(camera, renderer.domElement); controls.enableDamping = true;
    const wire = (on) => obj.traverse((o) => o.isMesh && [].concat(o.material).forEach((m) => (m.wireframe = on)));
    wire($("#wire").checked);

    let raf = 0; const loop = () => { controls.update(); renderer.render(scene, camera); raf = requestAnimationFrame(loop); }; loop();
    const onResize = () => { renderer.setSize(el.clientWidth, el.clientHeight); camera.aspect = el.clientWidth / el.clientHeight; camera.updateProjectionMatrix(); };
    window.addEventListener("resize", onResize);
    const caption = document.createElement("div"); caption.className = "caption"; caption.textContent = view.label; el.appendChild(caption);
    VIEWER = { wire, stop() { cancelAnimationFrame(raf); window.removeEventListener("resize", onResize); renderer.dispose(); } };
  } catch (e) { placeholder(`3D viewer unavailable (${e.message}).`); }
}

// ---- actions -------------------------------------------------------------------------------------
$("#rerun").onclick = async () => {
  const size = $("#rerun-size").value, faces = $("#rerun-faces").value;
  try {
    await api(`/api/jobs/${encodeURIComponent(CURRENT)}/rerun`, { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ size_mm: size ? +size : null, max_faces: faces ? parseInt(faces, 10) : null, dry_run: $("#rerun-dry").checked }) });
    VIEW_KEY = ""; toast("Re-running the 3D model and SolidWorks steps…"); refresh();
  } catch (e) { toast(e.message); }
};

loadMeta(); loadJobs().then(() => { if (location.hash.length > 1) select(decodeURIComponent(location.hash.slice(1))); });
