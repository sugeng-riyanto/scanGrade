/* ScanGrade Live pilot. Does not touch exam, answers, autosave, timer or grades. */
(() => {
  "use strict";
  const root = document.getElementById("scan-live");
  if (!root) return;
  const $ = id => document.getElementById(id);
  const examId = root.dataset.examId;
  const staff = root.dataset.role === "teacher";
  const csrf = root.dataset.csrf;
  const embedded = root.dataset.embedded === "1";
  const join = $("live-join");
  const tr = (id, en) => window.sgT ? window.sgT(id, en) : en;
  let joinIntent = !embedded, generation = 0, discovering = false, polling = false;
  let sdkLoading = null;
  const grid = $("live-grid"), start = $("live-start"), end = $("live-end");
  const hand = $("live-hand"), mic = $("live-mic"), queue = $("live-queue");
  const switchButton = $("live-switch"), switchState = $("live-switch-state");
  const switchOffNote = $("live-switch-off");
  let room = null, sid = null, connecting = false, names = {}, students = {}, micAllowed = false;
  // The paper's own switch, only ever written by the server response.
  let paperOn = false;
  let sessionDeadline = 0, mediaVersion = 1;
  async function updateSessionTime() {
    const clock = $("live-session-time");
    if (!clock || !sessionDeadline || !room) { if (clock) clock.hidden = true; return; }
    const seconds = Math.max(0, Math.ceil((sessionDeadline - Date.now()) / 1000));
    clock.hidden = false;
    clock.textContent = tr("Sisa pengawasan: ", "Supervision remaining: ") + Math.floor(seconds / 60) + ":" + String(seconds % 60).padStart(2, "0");
    if (!seconds) {
      sessionDeadline = 0;
      await stopMedia();
      if (start) start.hidden = !paperOn;
      message(tr("Durasi ScanMeet selesai. Ujian dan jawaban tetap berjalan.", "ScanMeet time ended. Your exam and answers continue."));
    }
  }
  window.setInterval(updateSessionTime, 1000);

  function message(msg, failed = false) {
    if (embedded) root.hidden = false;
    $("live-status").textContent = msg;
    $("live-warning").hidden = !failed;
  }
  async function call(path, method = "GET", body = null) {
    const headers = {"Accept": "application/json", "X-CSRF-Token": csrf};
    if (body) headers["Content-Type"] = "application/json";
    const response = await fetch(path, {
      method, credentials: "same-origin", cache: "no-store", headers,
      body: body ? JSON.stringify(body) : undefined,
    });
    // Not `body`: that name is this function's own parameter, and redeclaring it
    // in the same scope is a SyntaxError the browser raises while *parsing* — so
    // the whole script never ran, `discover()` never fired, and the teacher's page
    // sat on "Memeriksa sesi Live..." with every control still hidden.
    const reply = await response.json().catch(() => ({}));
    if (!response.ok) {
      const error = Error(reply.error || "HTTP " + response.status);
      error.status = response.status;
      throw error;
    }
    return reply;
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
        element.dataset.liveAudio = "true";
        root.append(element);
      }
      return;
    }
    const name = local ? tr("Kamera saya", "My camera") : names[participant.identity] ||
      (role(participant) === "teacher" ? tr("Pengawas", "Supervisor") : tr("Peserta", "Participant"));
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
  // Invalidate asynchronous joins before awaiting disconnect: a permission prompt
  // may resolve AFTER the teacher has turned this paper off on another device.
  async function release(candidate) {
    if (!candidate) return;
    for (const publication of candidate.localParticipant.trackPublications.values()) {
      try { if (publication.track) publication.track.stop(); } catch (_) { /* release remaining tracks too */ }
    }
    await candidate.disconnect().catch(() => {});
  }
  async function stopMedia(forgetIntent = true) {
    generation++;
    const previous = room;
    room = null; sid = null; connecting = false; sessionDeadline = 0; micAllowed = false;
    if (forgetIntent) joinIntent = false;
    if (hand) hand.disabled = true;
    if (mic) {
      mic.hidden = true; mic.dataset.active = "0";
      mic.textContent = tr("Aktifkan mikrofon", "Enable microphone");
    }
    if (end) end.hidden = true;
    if (join) join.hidden = true;
    const clock = $("live-session-time");
    if (clock) clock.hidden = true;
    grid.replaceChildren();
    root.querySelectorAll("audio[data-live-audio]").forEach(el => el.remove());
    await release(previous);
  }
  async function ensureSdk() {
    if (window.LivekitClient) return;
    if (!sdkLoading) sdkLoading = new Promise((resolve, reject) => {
      const url = new URL(root.dataset.sdk || "", window.location.href);
      if (!root.dataset.sdk || url.origin !== window.location.origin) {
        reject(Error("Local media SDK is unavailable")); return;
      }
      const script = document.createElement("script");
      script.src = url.href;
      const timeout = window.setTimeout(() => { script.remove(); reject(Error("Media SDK load timed out")); }, 15000);
      script.onload = () => {
        window.clearTimeout(timeout);
        window.LivekitClient ? resolve() : reject(Error("Media SDK did not initialize"));
      };
      script.onerror = () => { window.clearTimeout(timeout); script.remove(); reject(Error("Media SDK could not load")); };
      document.head.append(script);
    }).catch(error => { sdkLoading = null; throw error; });
    await sdkLoading;
  }
  async function connect() {
    if (connecting || room || !sid || !paperOn || !joinIntent) return;
    connecting = true;
    if (join) join.hidden = true;
    const epoch = generation, target = sid, targetVersion = mediaVersion;
    const current = () => epoch === generation && paperOn && sid === target && mediaVersion === targetVersion && joinIntent;
    let candidate = null;
    try {
      if (!window.LivekitClient) await ensureSdk();
      if (!current()) return;
      const lib = window.LivekitClient;
      const grant = await call("/api/live/sessions/" + encodeURIComponent(target) + "/token", "POST");
      if (!current()) return;
      candidate = new lib.Room({adaptiveStream: true, dynacast: true});
      room = candidate;
      candidate.on(lib.RoomEvent.TrackSubscribed, (track, publication, participant) => {
        if (room === candidate && current()) attach(track, publication, participant);
      });
      candidate.on(lib.RoomEvent.TrackUnsubscribed, (track, publication, participant) => {
        if (room === candidate) detach(track, participant);
      });
      candidate.on(lib.RoomEvent.ParticipantDisconnected, participant => {
        const item = Array.from(grid.children).find(x => x.dataset.identity === participant.identity);
        if (item) item.remove();
      });
      candidate.on(lib.RoomEvent.Disconnected, () => {
        if (room !== candidate) return;
        // Reconnect requires a fresh server authorization. Release local tracks
        // even if the transport vanished before the next status poll.
        void stopMedia(false);
        message(tr("Koneksi pengawasan terputus. Ujian tetap berjalan.",
                   "Supervision disconnected. Your exam continues."), true);
      });
      await candidate.connect(grant.url, grant.token);
      if (!current()) { await release(candidate); return; }
      // A room may have retired while a token/connect promise was pending.
      // Revalidate before camera access; room rotation is not JWT revocation.
      const fresh = await call("/api/live/exams/" + encodeURIComponent(examId));
      if (!current() || !fresh.enabled || fresh.handover_pending || fresh.can_join === false ||
          !fresh.session || fresh.session.id !== target ||
          (fresh.session.media_version || 1) !== targetVersion ||
          (grant.media_version || 1) !== targetVersion) {
        if (room === candidate) await stopMedia(false);
        else await release(candidate);
        return;
      }
      sessionDeadline = grant.expires_at ? Date.parse(grant.expires_at) : 0;
      if (!Number.isFinite(sessionDeadline)) sessionDeadline = 0;
      if (sessionDeadline && sessionDeadline <= Date.now()) throw Error("Supervision has ended");
      // Set the SFU-enforced receive boundary BEFORE publishing pupil media.
      if (!staff) {
        if (!grant.supervisor_identity ||
            typeof candidate.localParticipant.setTrackSubscriptionPermissions !== "function") {
          throw Error("Media subscription privacy guard missing");
        }
        candidate.localParticipant.setTrackSubscriptionPermissions(false, [{
          participantIdentity: grant.supervisor_identity, allowAll: true,
        }]);
      }
      // Capture first, then recheck authorization after the native permission
      // prompt, before publishing. A late prompt must not publish in a retired room.
      let captured = null;
      try {
        captured = await lib.createLocalVideoTrack();
      } catch (error) {
        if (!staff) throw error;
        message(tr("Pengawas bergabung tanpa kamera.", "Supervisor joined without a camera."));
      }
      if (captured) {
        try {
          const authorized = await call("/api/live/exams/" + encodeURIComponent(examId));
          if (!current() || !authorized.enabled || authorized.handover_pending ||
              authorized.can_join === false || !authorized.session || authorized.session.id !== target ||
              (authorized.session.media_version || 1) !== targetVersion) {
            captured.stop();
            if (room === candidate) await stopMedia(false);
            else await release(candidate);
            return;
          }
          await candidate.localParticipant.publishTrack(captured, {source: lib.Track.Source.Camera});
        } catch (error) {
          captured.stop();
          throw error;
        }
      }
      if (!current()) { await release(candidate); return; }
      const camera = candidate.localParticipant.getTrackPublication("camera");
      if (camera && camera.track) attach(camera.track, camera, candidate.localParticipant);
      if (mic && staff) mic.hidden = false;
      if (hand) hand.disabled = false;
      if (start) start.hidden = true;
      if (end) end.hidden = false;
      message(camera && camera.track && !camera.isMuted
        ? tr("ScanMeet tersambung. Kamera aktif.", "ScanMeet connected. Camera active.")
        : tr("Pengawas tersambung tanpa kamera.", "Supervisor connected without a camera."));
      await updateSessionTime();
    } catch (error) {
      if (current()) {
        await stopMedia();
        if (paperOn && ![401, 403, 404, 409].includes(error.status)) sid = target;
        if (join) join.hidden = !paperOn || !sid;
        message(tr("ScanMeet belum tersambung. Periksa izin kamera, lalu coba lagi; ujian tetap berjalan.",
                   "ScanMeet could not connect. Check camera permission and retry; your exam continues."), true);
      } else if (candidate) await release(candidate);
    } finally {
      if (epoch === generation) connecting = false;
    }
  }
  function paintSwitch(on) {
    paperOn = !!on;
    if (switchState) switchState.textContent = on ? "AKTIF" : "NONAKTIF";
    if (switchButton) switchButton.textContent = on ? "Nonaktifkan ScanMeet" : "Aktifkan ScanMeet";
    if (start) start.hidden = !on || !!sid;
    if (switchOffNote) switchOffNote.hidden = on;
    if (join) join.hidden = !on || !sid || connecting || !!room;
    if (embedded) root.hidden = false;
  }
  async function discover() {
    if (discovering) return;
    discovering = true;
    try {
      const data = await call("/api/live/exams/" + encodeURIComponent(examId));
      const next = data.session && data.session.id;
      const nextVersion = data.session && (data.session.media_version || 1);
      if (data.handover_pending) {
        if (room || connecting || sid) await stopMedia(false);
        paintSwitch(data.enabled);
        if (start) start.hidden = true;
        message(tr("Sedang mengganti pengawas. Kamera dan mikrofon dihentikan sementara.",
                   "Changing supervisor. Camera and microphone are temporarily stopped."));
        return;
      }
      if (staff && data.can_join === false) {
        if (room || connecting || sid) await stopMedia();
        paintSwitch(data.enabled);
        if (start) start.hidden = true;
        message(tr("Pengawas lain sedang menangani sesi ini. Kontrol OFF tetap tersedia untuk pengelola ujian.",
                   "Another supervisor owns this session. Exam managers can still switch it off."));
        return;
      }
      if (!data.enabled || !next) {
        if (room || connecting || sid) await stopMedia();
        paintSwitch(data.enabled);
        message(!data.enabled
          ? tr("ScanMeet nonaktif. Kamera dan mikrofon tidak digunakan.", "ScanMeet is off. Camera and microphone are not used.")
          : tr("Menunggu pengawas memulai ScanMeet.", "Waiting for the supervisor to start ScanMeet."));
        return;
      }
      if (sid && (sid !== next || mediaVersion !== nextVersion)) await stopMedia(false);
      sid = next;
      mediaVersion = nextVersion;
      paintSwitch(true);
      if (!room && !connecting) {
        if (joinIntent) {
          if (staff) await roster();
          // Do not block status polling on a native permission prompt.
          void connect();
        } else {
          message(tr("Pengawasan aktif. Klik Aktifkan kamera untuk bergabung di halaman ini.",
                     "Supervision is active. Select Enable camera to join on this page."));
        }
      }
      return data;
    } catch (error) {
      if ([401, 403, 404, 409].includes(error.status)) {
        await stopMedia();
        paintSwitch(false);
      }
      message(tr("ScanMeet belum tersedia; ujian tetap berjalan.", "ScanMeet is unavailable; your exam continues."), true);
    } finally {
      discovering = false;
    }
  }
  async function poll() {
    if (polling) return;
    polling = true;
    try {
      const state = await discover();
      if (!state || !sid || !room || connecting) return;
      const data = staff ? await call("/api/live/sessions/" + sid + "/hand-raises") : state;
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
          // A parked ``approving``/``closing`` row has no decision to offer; the
          // only way out of it is the fail-closed reconcile action.
          const parked = item.status === "approving" || item.status === "closing";
          const actions = parked ? [["reconcile", "Pulihkan"]]
            : item.status === "pending"
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
        const microphone = room.localParticipant.getTrackPublication("microphone");
        if (!micAllowed && microphone && !microphone.isMuted) {
          await room.localParticipant.setMicrophoneEnabled(false);
        }
        if (!micAllowed && mic) {
          mic.dataset.active = "0";
          mic.textContent = tr("Aktifkan mikrofon", "Enable microphone");
        }
        if (!room) return;
        $("live-raise-status").textContent = micAllowed
          ? tr("Pengawas menyetujui. Klik Aktifkan mikrofon jika siap.", "Approved. Select Enable microphone when ready.")
          : tr("Mikrofon menunggu izin pengawas.", "Microphone awaits supervisor approval.");
        const camera = room.localParticipant.getTrackPublication("camera");
        if (!camera || camera.isMuted) {
          message(tr("Kamera terputus. Mohon aktifkan kembali.", "Camera disconnected. Please enable it again."), true);
        }
      }
    } catch (_) {
      message(tr("Status pengawasan tidak dapat diperbarui; ujian tetap berjalan.",
                 "Supervision status could not refresh; your exam continues."), true);
    } finally { polling = false; }
  }
  if (switchButton) switchButton.onclick = async () => {
    switchButton.disabled = true;
    const wanted = !paperOn;
    try {
      const data = await call("/api/live/exams/" + encodeURIComponent(examId) +
                              "/enabled", "POST", {enabled: wanted});
      paintSwitch(data.enabled);
      if (!data.enabled) {
        // OFF is a revocation: drop this device's own media immediately rather
        // than waiting for the next poll to notice the room is gone.
        await stopMedia();
        message(data.media_cleanup === "pending"
          ? "ScanMeet nonaktif. Pembersihan media tertunda; semua token join baru ditolak."
          : "ScanMeet nonaktif untuk ujian ini. Ujian tetap berjalan.");
      } else {
        message("ScanMeet aktif. Klik Mulai pengawasan.");
      }
    } catch (e) {
      // Re-read rather than assume: the button reflects what was stored.
      message("Gagal mengubah ScanMeet: " + e.message, true);
      await discover();
    } finally {
      switchButton.disabled = false;
    }
  };
  if (start) start.onclick = async () => {
    start.disabled = true;
    try {
      joinIntent = true;
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
    const candidate = room, epoch = generation;
    const publication = candidate.localParticipant.getTrackPublication("microphone");
    const enabled = !!(publication && !publication.isMuted);
    try {
      await candidate.localParticipant.setMicrophoneEnabled(!enabled);
      if (epoch !== generation || room !== candidate || !paperOn) {
        await release(candidate); return;
      }
      if (!staff && !micAllowed) {
        await candidate.localParticipant.setMicrophoneEnabled(false); return;
      }
      mic.dataset.active = enabled ? "0" : "1";
      mic.textContent = enabled ? tr("Aktifkan mikrofon", "Enable microphone") : tr("Matikan mikrofon", "Mute microphone");
    } catch (_) {
      if (epoch === generation) message(tr("Izin mikrofon belum tersedia.", "Microphone permission is unavailable."), true);
    }
  };
  if (end) end.onclick = async () => {
    if (!confirm("Akhiri Live? Ujian siswa akan tetap berjalan.")) return;
    try {
      await call("/api/live/sessions/" + sid + "/end", "POST");
      await stopMedia();
      end.hidden = true; start.hidden = false;
      message("Live selesai. Ujian tetap berjalan.");
    } catch (e) { message("Gagal mengakhiri Live: " + e.message, true); }
  };
  if (join) join.onclick = async () => {
    // A real user action is needed for device permission; a synthetic click
    // cannot bypass the authoritative paper/session checks below.
    if (!paperOn || !sid || connecting || room) return;
    joinIntent = true;
    await connect();
  };
  window.addEventListener("pagehide", () => { void stopMedia(); });
  discover();
  window.setInterval(poll, 4000);
})();
