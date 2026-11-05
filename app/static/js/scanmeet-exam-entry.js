/* Exam editor entry controls. No media or exam-answer state is opened here. */
(() => {
  "use strict";
  const root = document.querySelector("[data-scanmeet-entry]");
  if (!root) return;
  const status = root.querySelector("[data-entry-status]");
  const toggle = root.querySelector("[data-entry-toggle]");
  const link = root.querySelector("[data-entry-link]");
  const copy = root.querySelector("[data-entry-copy]");
  const tr = (id, en) => window.sgT ? window.sgT(id, en) : en;
  let enabled = false, busy = false;
  const endpoint = "/api/live/exams/" + encodeURIComponent(root.dataset.examId);
  const say = (text, failed = false) => {
    status.textContent = text;
    status.setAttribute("role", failed ? "alert" : "status");
  };
  async function request(url, body) {
    const response = await fetch(url, {method: body ? "POST" : "GET", cache: "no-store",
      credentials: "same-origin", headers: {"Accept": "application/json",
        "Content-Type": "application/json", "X-CSRF-Token": root.dataset.csrf},
      body: body ? JSON.stringify(body) : undefined});
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw Error(data.error || "HTTP " + response.status);
    return data;
  }
  async function refresh() {
    if (!toggle) return;
    toggle.disabled = true;
    try {
      const data = await request(endpoint);
      enabled = data.enabled === true;
      say(data.session ? tr("Pengawasan sedang berjalan.", "Supervision is running.")
        : enabled ? tr("ScanMeet ON. Buka pengawasan untuk memulai sesi.", "ScanMeet ON. Open supervision to start a session.")
          : tr("ScanMeet OFF. Kamera dan mikrofon murid tidak digunakan.", "ScanMeet OFF. Pupil cameras and microphones are not used."));
      toggle.dataset.enabled = enabled ? "1" : "0";
      toggle.textContent = enabled ? tr("Nonaktifkan ScanMeet", "Disable ScanMeet") : tr("Aktifkan ScanMeet", "Enable ScanMeet");
      toggle.disabled = false;
    } catch (error) { say(tr("Status ScanMeet belum dapat dimuat: ", "ScanMeet status could not load: ") + error.message, true); }
  }
  if (toggle) toggle.onclick = async () => {
    if (busy) return;
    if (enabled && !window.confirm(tr("Nonaktifkan ScanMeet dan hentikan pengawasan? Ujian tetap berjalan.",
                                     "Disable ScanMeet and stop supervision? The exam will continue."))) return;
    busy = true; toggle.disabled = true;
    try {
      await request(endpoint + "/enabled", {enabled: !enabled});
      await refresh();
    } catch (error) {
      say(tr("Belum tersimpan: ", "Could not save: ") + error.message, true);
      toggle.disabled = false;
    } finally { busy = false; }
  };
  if (copy && link) copy.onclick = async () => {
    try {
      if (!navigator.clipboard || !window.isSecureContext) throw Error("clipboard unavailable");
      await navigator.clipboard.writeText(link.value);
      say(tr("Link murid disalin. Peserta tetap harus login.", "Pupil link copied. Participants still need to sign in."));
    } catch (_) {
      link.focus(); link.select();
      say(tr("Salin tautan yang sudah dipilih.", "Copy the selected link."));
    }
  };
  if (root.dataset.available === "1") {
    void refresh();
    window.setInterval(() => { if (!busy && !document.hidden) void refresh(); }, 15000);
  }
})();
