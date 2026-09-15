"""Every message the auth door shows, in both languages.

Why the server cannot pick one: the language lives in ``localStorage``, which is
client-side, so at render time the server genuinely does not know whether the
reader wants Indonesian or English. The pages therefore carry **both** strings
and let Alpine choose, which also keeps Jinja interpolation inside Jinja — a
school name containing an apostrophe inside an Alpine expression would close the
JavaScript string early and render the alert empty (the reason
``test_a_text_binding_never_escapes_a_quote`` exists).

A message in this catalogue is a ``(id, en)`` pair. Anything else — a Supabase
error string, a message built somewhere the catalogue does not own — is a plain
string and is shown exactly as it arrives, unchanged in both languages. That is
the same rule the i18n sweep states for a path or a role slug: *data*, not copy.

``auth_error()`` is the only way a route in ``auth.py`` may build an error, and a
guard enforces that: a literal passed to ``error=`` there is a string that will
read Indonesian in English mode.
"""

MESSAGES: dict[str, tuple[str, str]] = {
    # ── shared validation ──────────────────────────────────────────
    "all_required": ("Semua field wajib diisi", "All fields are required"),
    "password_short": ("Password minimal 6 karakter", "Password must be at least 6 characters"),
    "password_mismatch": ("Password tidak cocok", "Passwords do not match"),

    # ── registration ───────────────────────────────────────────────
    "tos_required": (
        "Anda harus menyetujui Syarat & Ketentuan dan Kebijakan Privasi",
        "You must accept the Terms & Conditions and the Privacy Policy",
    ),
    "npsn_pending": (
        "NPSN ini sudah memiliki permohonan pendaftaran yang menunggu verifikasi",
        "This NPSN already has a registration request awaiting verification",
    ),
    "npsn_taken": (
        "NPSN ini sudah terdaftar untuk sekolah '{school}'. Hubungi Super Admin.",
        "This NPSN is already registered to '{school}'. Contact the Super Admin.",
    ),
    "npsn_taken_plain": (
        "NPSN ini sudah terdaftar untuk sekolah '{school}'",
        "This NPSN is already registered to '{school}'",
    ),
    "email_taken": ("Email sudah terdaftar", "That email is already registered"),
    "account_create_failed": (
        "Gagal membuat akun: {reason}",
        "Could not create the account: {reason}",
    ),
    "profile_save_failed": ("Gagal menyimpan data profil", "Could not save the profile"),
    "request_create_failed": (
        "Gagal membuat permohonan registrasi. Silakan hubungi admin.",
        "Could not submit the registration request. Please contact an admin.",
    ),

    # ── activation ─────────────────────────────────────────────────
    "activate_required": (
        "Email dan kode aktivasi wajib diisi",
        "Email and activation code are required",
    ),
    "activate_code_shape": (
        "Kode aktivasi harus 12 karakter alfanumerik",
        "The activation code must be 12 alphanumeric characters",
    ),
    "activate_code_used": (
        "Kode aktivasi tidak valid atau sudah digunakan",
        "The activation code is invalid or has already been used",
    ),
    "activate_code_expired_contact": (
        "Kode aktivasi sudah kedaluwarsa. Silakan hubungi admin.",
        "The activation code has expired. Please contact an admin.",
    ),
    "activate_code_expired": (
        "Kode aktivasi tidak valid atau sudah kedaluwarsa",
        "The activation code is invalid or has expired",
    ),

    # ── login ──────────────────────────────────────────────────────
    "login_required_fields": ("Email dan password wajib diisi", "Email and password are required"),
    "login_wrong_page": (
        "Halaman ini untuk Admin. Guru/Murid silakan masuk di halaman login terpisah.",
        "This page is for Admins. Teachers and students should use the separate login page.",
    ),
    "login_bad_credentials": ("Email atau password salah", "Wrong email or password"),
    "login_auth_busy": (
        "Server autentikasi sedang sibuk (batas permintaan). Tunggu beberapa detik, lalu coba lagi.",
        "The authentication service is busy (rate limit). Wait a few seconds and try again.",
    ),
    "login_transient": (
        "Gagal masuk karena gangguan sementara. Silakan coba lagi sebentar lagi.",
        "Login failed because of a temporary problem. Please try again shortly.",
    ),
    "login_user_required": (
        "Email/NISN dan password wajib diisi",
        "Email/NISN and password are required",
    ),
    "login_user_wrong_page": (
        "Halaman ini untuk Guru/Murid. Admin silakan masuk di halaman login utama.",
        "This page is for Teachers and students. Admins should use the main login page.",
    ),

    # ── password reset ─────────────────────────────────────────────
    "forgot_required": (
        "Email aktif atau NISN wajib diisi",
        "An active email or NISN is required",
    ),
    "forgot_not_found": (
        "Email atau NISN tidak ditemukan. Hubungi admin sekolah.",
        "Email or NISN not found. Contact your school admin.",
    ),
    "forgot_email_failed": (
        "Gagal mengirim email. Coba lagi nanti.",
        "Could not send the email. Please try again later.",
    ),
    "code_required": ("Kode wajib diisi", "The code is required"),
    "code_invalid": (
        "Kode tidak valid atau sudah kedaluwarsa. Minta kode baru.",
        "The code is invalid or has expired. Request a new one.",
    ),
    "code_wrong": ("Kode salah. Coba lagi.", "Wrong code. Try again."),
    "reset_session_expired": (
        "Sesi kedaluwarsa. Ulangi proses reset.",
        "The session has expired. Restart the reset process.",
    ),
    "reset_user_missing": ("User tidak ditemukan", "User not found"),
    "reset_failed": (
        "Gagal mereset password. Coba lagi.",
        "Could not reset the password. Please try again.",
    ),
    "reset_token_missing": (
        "Token reset tidak ditemukan. Silakan ulangi proses reset password.",
        "The reset token was not found. Please restart the password reset process.",
    ),
    "reset_token_expired": (
        "Gagal mereset password. Token mungkin kedaluwarsa.",
        "Could not reset the password. The token may have expired.",
    ),

    # ── the session notices `login_required` flashes onto the door ──
    "session_required": (
        "Silakan login terlebih dahulu",
        "Please log in first",
    ),
    "session_idle": (
        "Sesi Anda berakhir karena tidak ada aktivitas selama {minutes} menit. "
        "Silakan masuk kembali.",
        "Your session ended after {minutes} minutes without activity. "
        "Please log in again.",
    ),
    "session_absolute": (
        "Sesi Anda berakhir karena sudah mencapai batas {hours} jam. Silakan masuk kembali.",
        "Your session ended because it reached its {hours}-hour limit. Please log in again.",
    ),
}

# Rate-limit notices: `Terlalu banyak {what}. Coba lagi dalam {minutes} menit.`
# The gerund is the only part that changes, so it is the only part catalogued.
RATE_LIMIT_ACTIONS: dict[str, tuple[str, str]] = {
    "login": ("percobaan login", "login attempts"),
    "registration": ("percobaan pendaftaran", "registration attempts"),
    "code_request": ("permintaan kode", "code requests"),
    "code_trial": ("percobaan kode", "code attempts"),
}


def auth_error(key: str, **fmt) -> tuple[str, str]:
    """The ``(id, en)`` pair for ``key``, with any placeholders filled.

    Both halves are formatted, so a count appears in both languages rather than
    only the one that happens to be rendered.
    """
    id_text, en_text = MESSAGES[key]
    if not fmt:
        return id_text, en_text
    return id_text.format(**fmt), en_text.format(**fmt)


def rate_limit_error(key: str, retry_after, ) -> tuple[str, str]:
    """The same notice the hook shows, in minutes, in both languages.

    ``retry_after`` is seconds; a partial minute rounds up, because telling
    someone to wait 0 minutes is telling them to retry immediately and fail.
    """
    minutes = max(1, (int(retry_after) + 59) // 60)
    id_what, en_what = RATE_LIMIT_ACTIONS[key]
    return (
        f"Terlalu banyak {id_what}. Coba lagi dalam {minutes} menit.",
        f"Too many {en_what}. Try again in {minutes} minute(s).",
    )


def first(message):
    """The Indonesian half of a pair, or the message itself.

    JSON error responses are strings — a client that reads
    ``response.error`` would get an array if a pair were serialized — so the API
    paths keep taking the Indonesian form they have always taken. The pair is a
    *page* concern; the API's own wording is a separate contract.
    """
    if isinstance(message, (tuple, list)) and len(message) == 2:
        return message[0]
    return message
