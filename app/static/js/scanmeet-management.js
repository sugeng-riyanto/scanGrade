/* License and offer forms. No credentials, payment collection or media. */
(() => {
  "use strict";
  const text = (id, en) => typeof window.sgT === "function" ? window.sgT(id, en) : id;
  document.querySelectorAll("[data-open-dialog]").forEach(button => {
    button.addEventListener("click", () => document.getElementById(button.dataset.openDialog)?.showModal());
  });
  document.querySelectorAll("[data-close-dialog]").forEach(button => {
    button.addEventListener("click", () => button.closest("dialog")?.close());
  });
  const plan = document.getElementById("scanmeet-plan");
  const preview = () => document.querySelectorAll("[data-package-code]").forEach(node => {
    node.hidden = node.dataset.packageCode !== plan?.value;
  });
  if (plan) { plan.addEventListener("change", preview); preview(); }
  document.querySelectorAll("form[data-scanmeet-form]").forEach(form => {
    let busy = false;
    form.addEventListener("submit", async event => {
      event.preventDefault();
      if (busy || !form.reportValidity()) return;
      if (form.dataset.confirm && !(form.dataset.confirm === "license" && form.querySelector('[name="action"]')?.value === "extend")) {
        const message = form.dataset.confirm === "license"
          ? text("Aktifkan paket ini? Aktivasi mengganti sisa jatah sebelumnya. Gunakan Perpanjang untuk menambah jatah paket yang sama.", "Activate this plan? Activation replaces the previous allowance. Use Extend to add allowance to the same plan.")
          : form.dataset.confirm === "reject"
          ? text("Tolak pengajuan ini? Lisensi sekolah tidak berubah.", "Decline this request? The school license will not change.")
          : text("Cabut akses dan hentikan semua ruang pengawasan sekolah ini? Ujian tetap berjalan.", "Revoke access and stop every supervision room for this school? Exams continue.");
        if (!window.confirm(message)) return;
      }
      const output = form.querySelector("[data-form-result]");
      let succeeded = false;
      const controller = new AbortController();
      const timeout = window.setTimeout(() => controller.abort(), 30000);
      const buttons = [...form.querySelectorAll("button[type=submit], button:not([type])")];
      busy = true;
      form.setAttribute("aria-busy", "true");
      buttons.forEach(button => { button.disabled = true; });
      if (output) output.textContent = text("Menyimpan…", "Saving…");
      try {
        const response = await fetch(form.action, {
          method: "POST", credentials: "same-origin",
          signal: controller.signal,
          headers: {"Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded",
            "X-CSRF-Token": form.querySelector('[name="_csrf_token"]')?.value || ""},
          body: new URLSearchParams(new FormData(form)),
        });
        const reply = await response.json().catch(() => null);
        if (!reply) throw Error(text("Server tidak memberi jawaban yang dapat dibaca. Muat ulang untuk memeriksa status.", "The server did not return a readable response. Reload to check the status."));
        if (!response.ok) throw Error(reply.error || text("Perubahan belum tersimpan.", "Changes were not saved."));
        succeeded = true;
        if (output) output.textContent = reply.cleanup_pending?.length
          ? text("Akses dicabut. Penghentian media sedang dicoba ulang; ujian tetap berjalan.", "Access revoked. Media shutdown is being retried; exams continue.")
          : reply.message || text("Tersimpan.", "Saved.");
        window.setTimeout(() => window.location.reload(), 1000);
      } catch (error) {
        if (output) { output.textContent = error.name === "AbortError"
          ? text("Batas waktu terlewati. Muat ulang untuk memeriksa status sebelum mencoba lagi.", "The request timed out. Reload to check the status before trying again.")
          : error.message; output.setAttribute("role", "alert"); }
      } finally {
        window.clearTimeout(timeout);
        busy = false;
        form.removeAttribute("aria-busy");
        buttons.forEach(button => { button.disabled = succeeded; });
      }
    });
  });
})();
