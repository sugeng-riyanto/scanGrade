/* Admin-only assignment management. The server owns school, actor and rights. */
(() => {
  "use strict";
  const root = document.getElementById("supervisor-management");
  if (!root) return;
  const $ = id => document.getElementById(id);
  const t = (id, en) => window.sgT ? window.sgT(id, en) : en;
  const base = "/api/live/exams/" + encodeURIComponent(root.dataset.examId);
  const form = $("supervisor-form"), target = $("supervisor-target");
  const until = $("supervisor-until"), reason = $("supervisor-reason"), status = $("supervisor-status");
  let snapshot = null, busy = false;
  $("supervisor-link").value = new URL("/live/exam/" + encodeURIComponent(root.dataset.examId), location.origin).href;
  const date = new Date(Date.now() + 86400000);
  until.value = new Date(date.getTime() - date.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
  async function api(path, body) {
    const response = await fetch(base + path, {method: body ? "POST" : "GET", credentials: "same-origin",
      headers: {"Content-Type": "application/json", "X-CSRFToken": root.dataset.csrf},
      ...(body ? {body: JSON.stringify(body)} : {})});
    const data = await response.json();
    if (!response.ok) throw Error(data.error || "ScanMeet unavailable");
    return data;
  }
  function paint() {
    const assignment = snapshot && snapshot.assignments.find(a => a.supervisor_id === target.value);
    $("supervisor-revoke").disabled = busy || !assignment || !!assignment.revoked_at;
    const active = assignment && !assignment.revoked_at && Date.parse(assignment.valid_until) > Date.now();
    const candidate = snapshot && snapshot.candidates.find(c => c.id === target.value);
    const allowed = target.value === (snapshot && snapshot.owner_id) || active || (candidate && candidate.role === "admin_sekolah");
    $("supervisor-grant").disabled = busy || !candidate || candidate.id === snapshot.owner_id || candidate.role === "admin_sekolah";
    const session = snapshot && snapshot.session;
    $("supervisor-transfer").disabled = busy || !session || session.transfer_state !== "ready" || session.created_by === target.value || !allowed;
  }
  async function load() {
    snapshot = await api("/supervisors");
    const selected = target.value;
    target.replaceChildren();
    snapshot.candidates.forEach(candidate => {
      const option = document.createElement("option");
      option.value = candidate.id;
      option.textContent = candidate.full_name || candidate.role;
      target.append(option);
    });
    if ([...target.options].some(option => option.value === selected)) target.value = selected;
    paint();
  }
  async function save(action) {
    if (busy || !snapshot || !reason.reportValidity()) return;
    if (action === "grant" && !until.reportValidity()) return;
    busy = true;
    [...form.elements].forEach(element => { element.disabled = true; });
    const assignment = snapshot.assignments.find(a => a.supervisor_id === target.value);
    try {
      let body = {target_id: target.value, reason: reason.value.trim()};
      if (action === "transfer") {
        body = {...body, session_id: snapshot.session.id, version: snapshot.session.media_version};
      } else {
        body = {...body, version: assignment ? assignment.version : 0, revoke: action === "revoke",
          valid_until: action === "revoke" ? null : new Date(until.value).toISOString()};
      }
      const data = await api(action === "transfer" ? "/handover" : "/supervisors", body);
      status.textContent = data.media_cleanup === "pending"
        ? t("Pengalihan tercatat. Menunggu pembersihan ruang lama; muat ulang untuk memeriksa.", "Handover recorded. Waiting for the retired room cleanup; refresh to check.")
        : t("Perubahan berhasil disimpan.", "Changes saved successfully.");
      await load();
    } catch (error) {
      status.textContent = t("Belum tersimpan: ", "Not saved: ") + error.message;
      await load().catch(() => {});
    } finally {
      busy = false;
      [...form.elements].forEach(element => { element.disabled = false; });
      paint();
    }
  }
  target.addEventListener("change", paint);
  form.addEventListener("submit", event => { event.preventDefault(); void save("grant"); });
  $("supervisor-revoke").onclick = () => void save("revoke");
  $("supervisor-transfer").onclick = () => void save("transfer");
  load().catch(() => { status.textContent = t("Pengelolaan pengawas belum tersedia.", "Supervisor management is unavailable."); });
})();
