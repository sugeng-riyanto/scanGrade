/* ScanGrade Live pilot. Does not touch exam, answers, autosave, timer or grades. */
(() => {
  "use strict";
  const root = document.getElementById("scan-live");
  if (!root) return;
  const $ = id => document.getElementById(id);
  const examId = root.dataset.examId;
  const staff = root.dataset.role === "teacher";
  const csrf = root.dataset.csrf;
  const grid = $("live-grid"), start = $("live-start"), end = $("live-end");
  const hand = $("live-hand"), mic = $("live-mic"), queue = $("live-queue");
  let room = null, sid = null, connecting = false, names = {}, students = {}, micAllowed = false;

  function message(msg, failed = false) {
    $("live-status").textContent = msg;
    $("live-warning").hidden = !failed;
  }
  async function call(path, method = "GET") {
    const response = await fetch(path, {
      method, credentials: "same-origin", cache: "no-store",
      headers: {"Accept": "application/json", "X-CSRF-Token": csrf},
    });
    const body = await response.json().catch(() => ({}));
    if (!response.ok) throw Error(body.error || "HTTP " + response.status);
    return body;
  }
  function role(participant) {
    try { return JSON.parse(participant.metadata || "{}").role; }
    catch (_) { return ""; }
  }
  function tile(identity, name) {
    let item = Array.from(grid.children).find(x => x.dataset.identity === identity);
    if (item) return item;
    item = document.createElement("div");
    item.className = "rounded-xl overflow-hidden bg-slate-900 text-white";
    item.dataset.identity = identity;
    const media = document.createElement("div");
    media.className = "h-40";
    media.dataset.media = "true";
    const label = document.createElement("div");
    label.className = "p-2 text-sm";
    label.textContent = name;
    item.append(media, label);
    grid.append(item);
    return item;
  }
  function attach(track, publication, participant) {
    const local = participant === room.localParticipant;
    if (!local && !staff && role(participant) !== "teacher") return;
    if (track.kind === "audio") {
      if (!local) {
        const element = track.attach();
        element.autoplay = true;
        element.hidden = true;
        root.append(element);
      }
      return;
    }
    const name = local ? "Kamera saya" : names[participant.identity] ||
      (role(participant) === "teacher" ? "Guru" : "Peserta");
    const target = tile(participant.identity, name).querySelector("[data-media]");
    const video = track.attach();
    video.className = "w-full h-full object-cover";
    video.autoplay = true;
    video.playsInline = true;
    video.muted = local;
    target.replaceChildren(video);
  }
  function detach(track, participant) {
    track.detach().forEach(el => el.remove());
    if (track.kind === "video" && participant) {
      const item = Array.from(grid.children).find(x => x.dataset.identity === participant.identity);
      if (item) item.querySelector("[data-media]").replaceChildren();
    }
  }
  async function roster() {
    if (!staff || !sid) return;
    const result = await call("/api/live/sessions/" + sid + "/roster");
    names = result.participants || {};
    students = result.students || {};
  }
  async function connect() {
    if (connecting || room || !sid) return;
    if (!window.LivekitClient) {
      message("LiveKit SDK tidak tersedia. Ujian tetap aman.", true);
      return;
    }
    connecting = true;
    try {
      const lib = window.LivekitClient;
      const grant = await call("/api/live/sessions/" + sid + "/token", "POST");
      room = new lib.Room({adaptiveStream: true, dynacast: true});
      room.on(lib.RoomEvent.TrackSubscribed, attach);
      room.on(lib.RoomEvent.TrackUnsubscribed, detach);
      room.on(lib.RoomEvent.ParticipantDisconnected, participant => {
        const item = Array.from(grid.children).find(x => x.dataset.identity === participant.identity);
        if (item) item.remove();
      });
      room.on(lib.RoomEvent.Disconnected, () => {
        message("Koneksi pengawasan terputus. Ujian tetap berjalan.", true);
      });
      await room.connect(grant.url, grant.token);
      // LiveKit enforces this at the SFU. A pupil only permits the room
      // supervisor to receive their camera/mic, even if another pupil hacks
      // their own canSubscribe client. Set BEFORE any track is published.
      if (!staff) {
        if (!grant.supervisor_identity ||
            typeof room.localParticipant.setTrackSubscriptionPermissions !== "function") {
          throw Error("Media subscription privacy guard missing");
        }
        room.localParticipant.setTrackSubscriptionPermissions(false, [{
          participantIdentity: grant.supervisor_identity, allowAll: true,
        }]);
      }
      try {
        await room.localParticipant.setCameraEnabled(true);
      } catch (e) {
        if (!staff) {
          message("Izinkan kamera untuk bergabung dengan Live. Ujian tidak terdampak.", true);
          await room.disconnect();
          room = null;
          return;
        }
        message("Guru bergabung tanpa kamera.");
      }
      const camera = room.localParticipant.getTrackPublication("camera");
      if (camera && camera.track) attach(camera.track, camera, room.localParticipant);
      if (mic && staff) mic.hidden = false;
      if (hand) hand.disabled = false;
      if (start) start.hidden = true;
      if (end) end.hidden = false;
      message("Live tersambung. Kamera aktif.");
    } catch (e) {
      console.warn("ScanGrade Live connection:", e.message);
      message("Live belum tersambung; ujian tetap berjalan.", true);
      if (room) await room.disconnect().catch(() => {});
      room = null;
    } finally {
      connecting = false;
    }
  }
  async function discover() {
    try {
      const data = await call("/api/live/exams/" + encodeURIComponent(examId));
      sid = data.session && data.session.id;
      if (sid) {
        if (staff) await roster();
        await connect();
      } else {
        message(staff ? "Klik Mulai pengawasan." : "Menunggu guru membuka Live.");
      }
    } catch (_) {
      message("Live tidak tersedia; ujian tetap berjalan.", true);
    }
  }
  async function poll() {
    if (!sid || !room) {
      if (!staff && !sid) await discover();
      return;
    }
    try {
      const data = await call("/api/live/sessions/" + sid + "/hand-raises");
      if (staff) {
        const audit = await call("/api/live/sessions/" + sid + "/events");
        const eventsEl = $("live-events");
        eventsEl.replaceChildren();
        for (const ev of (audit.events || []).slice(0, 8)) {
          const row = document.createElement("p");
          const ident = names[ev.participant_identity] || "Peserta";
          row.textContent = ident + ": " + ev.event_type;
          if (["track_unpublished", "participant_left",
               "participant_connection_aborted"].includes(ev.event_type)) {
            row.className = "text-amber-700";
          }
          eventsEl.append(row);
        }
        queue.replaceChildren();
        const requests = data.requests || [];
        if (!requests.length) queue.textContent = "Belum ada permintaan.";
        for (const item of requests) {
          const line = document.createElement("div");
          line.className = "rounded-lg border p-2 space-y-2";
          const desc = document.createElement("div");
          desc.textContent = (students[item.student_id] || item.student_id.slice(0, 8)) + " — " + item.status;
          line.append(desc);
          const actions = item.status === "pending"
            ? [["approve", "Izinkan"], ["reject", "Tolak"]]
            : item.status === "approved" ? [["close", "Akhiri bicara"]] : [];
          for (const pair of actions) {
            const button = document.createElement("button");
            button.type = "button";
            button.className = "mr-2 rounded bg-primary-600 text-white px-2 py-1";
            button.textContent = pair[1];
            button.onclick = async () => {
              button.disabled = true;
              try {
                await call("/api/live/sessions/" + sid + "/hand-raises/" +
                           item.id + "/" + pair[0], "POST");
                await poll();
              } catch (e) { message("Moderasi gagal: " + e.message, true); }
              finally { button.disabled = false; }
            };
            line.append(button);
          }
          queue.append(line);
        }
      } else {
        micAllowed = !!(data.request && data.request.status === "approved");
        if (mic) mic.hidden = !micAllowed;
        $("live-raise-status").textContent = micAllowed
          ? "Guru menyetujui. Klik Aktifkan mikrofon jika siap."
          : "Mikrofon menunggu izin guru.";
        const camera = room.localParticipant.getTrackPublication("camera");
        if (!camera || camera.isMuted) {
          message("Kamera terputus. Mohon aktifkan kembali.", true);
        }
      }
    } catch (_) {
      message("Status pengawasan tidak dapat diperbarui; ujian tetap berjalan.", true);
    }
  }
  if (start) start.onclick = async () => {
    start.disabled = true;
    try {
      sid = (await call("/api/live/exams/" + encodeURIComponent(examId) +
                       "/sessions", "POST")).session_id;
      await roster();
      await connect();
    } catch (e) { message("Gagal memulai: " + e.message, true); }
    finally { start.disabled = false; }
  };
  if (hand) hand.onclick = async () => {
    hand.disabled = true;
    try {
      await call("/api/live/sessions/" + sid + "/hand-raises", "POST");
      await poll();
    } catch (e) { message("Permintaan gagal: " + e.message, true); }
    finally { hand.disabled = false; }
  };
  if (mic) mic.onclick = async () => {
    if (!room || (!staff && !micAllowed)) return;
    const publication = room.localParticipant.getTrackPublication("microphone");
    const enabled = !!(publication && !publication.isMuted);
    try {
      await room.localParticipant.setMicrophoneEnabled(!enabled);
      mic.textContent = enabled ? "Aktifkan mikrofon" : "Matikan mikrofon";
    } catch (_) { message("Izin mikrofon belum tersedia.", true); }
  };
  if (end) end.onclick = async () => {
    if (!confirm("Akhiri Live? Ujian siswa akan tetap berjalan.")) return;
    try {
      await call("/api/live/sessions/" + sid + "/end", "POST");
      await room.disconnect();
      room = null; sid = null;
      end.hidden = true; start.hidden = false;
      grid.replaceChildren();
      message("Live selesai. Ujian tetap berjalan.");
    } catch (e) { message("Gagal mengakhiri Live: " + e.message, true); }
  };
  window.addEventListener("pagehide", () => { if (room) room.disconnect(); });
  discover();
  window.setInterval(poll, 4000);
})();
