/**
 * ScanGrade exam media — the one place a pasted link becomes something a browser
 * can actually play.
 *
 * Both pages that show question media read this file: the teacher's builder, which
 * draws a preview beside the field, and the student's exam page, which draws the
 * real thing. They each carried their own copy of the same two functions before —
 * `youtubeEmbedUrl` and `gdriveDirect`, twice over — and two copies of one
 * transformation is how a preview comes to promise a player the exam page cannot
 * produce. The half that gets fixed is whichever half someone happens to look at.
 *
 * Two facts about the links teachers really paste.
 *
 * Google Drive. A share link is not a file URL, so it has to be rewritten — and
 * the rewrite everyone has used for years, `docs.google.com/uc?export=download&id=…`,
 * stopped working in January 2024: Drive began refusing that cross-site request
 * (Google issue 319531488), and the failure is silent. An `<audio>` whose source
 * 403s shows a player that never plays and prints nothing anywhere a teacher would
 * see. The endpoint Drive's own download button calls,
 * `drive.usercontent.google.com/download?id=…&export=download&confirm=t`, is the one
 * that serves the bytes, and it honours Range requests, so seeking inside a track
 * works instead of restarting it. Both are offered — see `audioSources` — because a
 * media element walks its `<source>` list in order and plays the first one that
 * loads, which makes the old endpoint a fallback rather than a single point of
 * failure.
 *
 * YouTube. The eleven-character id is the only part that matters, and a listening
 * paper often points at one verse, so the `t=`/`start=` offset is carried across
 * too. Every shape the browser bar can hand over is read: `youtu.be`, `watch?v=`,
 * `embed/`, `shorts/`, `live/`, a mobile host, and the bare id itself.
 *
 * A link that cannot be read resolves to nothing **on purpose**, and nothing is not
 * a rendering instruction: both pages test for it and say so out loud, because an
 * empty black player is the one outcome a pupil cannot act on.
 *
 * Since question media can also be *uploaded*, a third kind of URL reaches these
 * functions: the app's own `/media/<token>`, signed over one file, one pupil and
 * one sitting. It needs no rewriting and gets none — it is already the thing the
 * element should load.
 */
const sgExamMedia = (function () {
  // The eleven characters YouTube uses for a video id. Checked against the
  // *pattern* rather than a length alone, so `watch?v=not-a-video` is refused.
  const VIDEO_ID = /^[A-Za-z0-9_-]{11}$/;
  // A Drive file id is longer and its exact length is not documented, so the
  // floor is the loose one: anything plausible is read, and the endpoint answers
  // for a wrong id by failing to play it — the same refusal a bad link gets.
  const FILE_ID = /^[A-Za-z0-9_-]{10,}$/;

  function text(url) {
    return String(url === null || url === undefined ? "" : url).trim();
  }

  /** Parse, or `null` — callers treat an unparseable link as no link at all. */
  function parse(url) {
    try {
      return new URL(url);
    } catch (err) {
      return null;
    }
  }

  /** Seconds from `t=` or `start=`: `90`, `90s`, and `1h2m3s` all arrive here. */
  function offsetSeconds(parsed) {
    const start = parsed.searchParams.get("start");
    if (start && /^\d+$/.test(start)) return parseInt(start, 10);
    const t = parsed.searchParams.get("t");
    if (!t) return null;
    if (/^\d+$/.test(t)) return parseInt(t, 10);
    const parts = t.match(/^(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?$/i);
    if (!parts) return null;
    const seconds = (+(parts[1] || 0)) * 3600 + (+(parts[2] || 0)) * 60 + (+(parts[3] || 0));
    return seconds || null;
  }

  /**
   * The video id inside any YouTube link, or `""`.
   *
   * `youtu.be/<id>` is handled apart from the rest because the id is the *path* and
   * there is no query to look in; every other shape either carries `v=` or puts the
   * id under one of the four path segments YouTube uses.
   */
  function youtubeId(url) {
    const raw = text(url);
    if (!raw) return "";
    if (VIDEO_ID.test(raw)) return raw;           // the bare id, pasted alone
    const parsed = parse(raw);
    if (!parsed) return "";
    const host = parsed.hostname.toLowerCase();
    if (/^(?:www\.)?youtu\.be$/.test(host)) {
      const first = parsed.pathname.split("/").filter(Boolean)[0] || "";
      return VIDEO_ID.test(first) ? first : "";
    }
    if (!/(?:^|\.)(?:youtube\.com|youtube-nocookie\.com)$/.test(host)) return "";
    const v = parsed.searchParams.get("v");
    if (v && VIDEO_ID.test(v)) return v;
    const inPath = parsed.pathname.match(
      /\/(?:embed|shorts|live|v)\/([A-Za-z0-9_-]{11})(?:[/?#]|$)/);
    return inPath ? inPath[1] : "";
  }

  /**
   * The document is never audio, and a folder has no bytes to stream, so both are
   * refused. Reading an id out of them would hand the element a URL that cannot
   * play, which is exactly the silent failure this module exists to end.
   */
  const NOT_MEDIA = /^\/(?:document|spreadsheets|presentation|forms)\//;
  const FOLDER = /^\/drive\/(?:folders|u\/\d+\/folders)\//;

  /** The Drive file id inside a share link, or `""`. */
  function driveId(url) {
    const raw = text(url);
    if (!raw) return "";
    const parsed = parse(raw);
    if (!parsed) return "";
    if (!/(?:^|\.)(?:drive|docs)\.google\.com$/.test(parsed.hostname.toLowerCase())) {
      return "";
    }
    const path = parsed.pathname;
    if (NOT_MEDIA.test(path) || FOLDER.test(path)) return "";
    const named = parsed.searchParams.get("id");
    if (named && FILE_ID.test(named)) return named;
    const inPath = path.match(/\/(?:file\/)?d\/([A-Za-z0-9_-]{10,})/);
    return inPath ? inPath[1] : "";
  }

  /**
   * Candidate sources for one audio link, best first.
   *
   * A Google link that yielded no id is refused rather than passed through: it is
   * a folder, a document, or a form, and handing it to the element produces the
   * silent non-player this module is here to remove. Any other http(s) URL is the
   * teacher's own host and is passed through untouched — a school that serves its
   * own mp3 over https needs no rewriting from us.
   */
  function audioSources(url) {
    const raw = text(url);
    if (!raw) return [];
    // The app's own `\/media\/<token>` URLs are already the source. They are relative
    // on purpose — the same host that served the page serves the bytes — and they
    // are only served to the sitting they were minted for, so there is nothing to
    // rewrite and nothing to fall back to. The guard refuses `//host/path`,
    // which is not one of ours. See app/services/exam_media.py.
    if (/^\/(?!\/)/.test(raw)) return [raw];
    const id = driveId(raw);
    if (id) {
      return [
        "https://drive.usercontent.google.com/download?id=" + id +
          "&export=download&confirm=t",
        "https://docs.google.com/uc?export=download&id=" + id,
      ];
    }
    const parsed = parse(raw);
    if (!parsed) return [];
    if (/\.google\.com$/.test(parsed.hostname.toLowerCase())) return [];
    return /^https?:$/.test(parsed.protocol) ? [raw] : [];
  }

  /**
   * The embed URL for one YouTube link, or `""` when the link is not a video.
   *
   * `rel=0` keeps the player from offering other people's videos at the end, and
   * `start` is the offset the teacher pointed at. `www.youtube.com` rather than the
   * cookie-less host on purpose: the embed inside an exam is the same embed
   * everywhere else, and a host the school's network has never seen is a variable
   * with no upside.
   */
  function youtubeEmbed(url) {
    const id = youtubeId(url);
    if (!id) return "";
    const query = new URLSearchParams({ rel: "0", modestbranding: "1" });
    const parsed = parse(text(url));
    const start = parsed ? offsetSeconds(parsed) : null;
    if (start) query.set("start", String(start));
    return "https://www.youtube.com/embed/" + id + "?" + query.toString();
  }

  return {
    youtubeId: youtubeId,
    youtubeEmbed: youtubeEmbed,
    driveId: driveId,
    audioSources: audioSources,
  };
})();
