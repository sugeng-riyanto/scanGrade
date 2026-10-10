"""The mail a school reads: one professional body, in two languages, twice.

Why this is a module and not four f-strings
-------------------------------------------
Every user-facing mail used to be a hand-written string at its own call site:

* a reset code framed in **box-drawing characters** (`┌─────┐`) — legible in a
  terminal and monospaced garbage in a mail client;
* an activation code inside a one-off orange HTML blob of its own;
* a super-admin password reset and a payment receipt as bare paragraphs.

Four copies of the same decisions is how the fourth one ends up without the brand,
without the language the school reads, and without the security note. So the decisions
live here, once:

* **Bilingual, Indonesian first.** The product is bilingual and the schools are
  Indonesian; a reset mail in English only is a support ticket. Each body carries an
  Indonesian section, then an English one behind a quiet divider — the reader's
  language is answered either way.
* **Two parts, always.** Every body ships a complete HTML document *and* a plain-text
  alternative, and `smtp_settings.send(..., text=...)` puts both in one
  `multipart/alternative`. A client that refuses HTML must still be able to read the
  code, and a one-part HTML mail is the shape spam filters score highest.
* **Data is escaped, never interpolated.** A pupil's name comes from an import sheet
  and a reset code is generated — but nothing in either's type says "safe", so both go
  through `html.escape`. A school that types `<b>Budi</b>` into a spreadsheet gets a
  name back, not markup in a mail the app signs.
* **The layout is deliberately old-fashioned HTML**: one centred card, inline styles,
  no external CSS, no web fonts, no images that need to load. It is what mail clients
  render, which is the only thing "amazing" can mean here.

One-way mail, said out loud
---------------------------
Every body names `SUPPORT_ADDRESS` and says the message is automated, because the
sending mailbox is not watched and a reply that disappears is worse than a reply that
is never sent. The address is overridable (`SMTP_REPLY_TO`, or the settings page) for
a school that wants answers routed somewhere real; the default is the product's own
domain rather than the personal Gmail the mail is sent from.
"""

from __future__ import annotations

import html

#: The sign-in page, from the one module that maps roles to doors. A body that
#: spelled the URL itself would be a second copy of the address, and the copy that
#: drifts is the one in a mail nobody re-reads.
from app.utils.auth import LOGIN_URL, login_door_for

BRAND_NAME = "ScanGrade"
BRAND_URL = "https://scangrade.web.id"
#: Where a reply is pointed. A reset code is a one-way message and the sending mailbox
#: (`scangrade9@gmail.com`) is not watched, so this is the product's own domain — the
#: same default `app/services/smtp_settings.py` resolves, named here as well because
#: every body repeats it in its footer.
SUPPORT_ADDRESS = "noreply@scangrade.web.id"
BRAND_COLOR = "#2563eb"        # tailwind.config.js `primary.600`
ACCENT_COLOR = "#059669"       # emerald-600, the landing page's accent
INK = "#0f172a"                # surface.900
MUTED = "#64748b"              # surface.500
LINE = "#e2e8f0"               # surface.200
CANVAS = "#f1f5f9"             # surface.100

_FONT = ("-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,'Helvetica Neue',"
         "Arial,sans-serif")
_MONO = "'SFMono-Regular',Consolas,'Liberation Mono',Menlo,monospace"


def escape(value) -> str:
    """Text that is data, made safe to place in HTML — never a template."""
    return html.escape(str(value if value is not None else ""), quote=True)


def _header_line(value) -> str:
    """A subject, safe to put in a header.

    `email.message` refuses a header containing a bare newline (or silently folds it),
    and a subject is built from a school name — a name typed with a newline in a
    spreadsheet would otherwise be a way to smuggle text into the headers.
    """
    return " ".join(str(value if value is not None else "").split())


def code_box(code) -> str:
    """The one thing the reader came for, large, monospaced and copyable."""
    return (
        f'<div style="margin:20px 0 8px;text-align:center;">'
        f'<div style="display:inline-block;padding:16px 28px;border-radius:14px;'
        f'background:{CANVAS};border:1px dashed {BRAND_COLOR};">'
        f'<span style="font-family:{_MONO};font-size:30px;font-weight:700;'
        f'letter-spacing:6px;color:{BRAND_COLOR};">{escape(code)}</span>'
        f'</div></div>')


def button(url, label) -> str:
    return (
        f'<div style="margin:24px 0 8px;text-align:center;">'
        f'<a href="{escape(url)}" style="display:inline-block;background:{BRAND_COLOR};'
        f'color:#ffffff;padding:13px 30px;border-radius:10px;text-decoration:none;'
        f'font-weight:700;font-size:15px;">{escape(label)}</a></div>')


def details(rows) -> str:
    """A small two-column table — `[(label, value), …]`, both escaped."""
    body = "".join(
        f'<tr><td style="padding:6px 0;color:{MUTED};font-size:13px;">{escape(label)}</td>'
        f'<td style="padding:6px 0;color:{INK};font-size:13px;font-weight:600;'
        f'text-align:right;">{escape(value)}</td></tr>'
        for label, value in rows)
    return (f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
            f'style="border-collapse:collapse;margin:12px 0;">{body}</table>')


def layout(*, title, preheader, indonesian, english, footnote="") -> str:
    """The one card every body wears. `indonesian`/`english` are already-built HTML."""
    return f"""<!DOCTYPE html>
<html lang="id">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light only">
<title>{escape(title)}</title>
</head>
<body style="margin:0;padding:0;background:{CANVAS};">
<div style="display:none;font-size:1px;color:{CANVAS};max-height:0;overflow:hidden;">{escape(preheader)}</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:{CANVAS};padding:24px 12px;">
  <tr><td align="center">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:560px;background:#ffffff;border-radius:16px;border:1px solid {LINE};overflow:hidden;">
      <tr><td style="background:{BRAND_COLOR};padding:20px 28px;">
        <span style="font-family:{_FONT};font-size:19px;font-weight:800;color:#ffffff;letter-spacing:.3px;">
          {BRAND_NAME}<span style="color:#bfdbfe;font-weight:600;"> — {escape(title)}</span>
        </span>
      </td></tr>
      <tr><td style="padding:26px 28px 4px;font-family:{_FONT};font-size:15px;line-height:1.62;color:{INK};">
        {indonesian}
        <div style="height:1px;background:{LINE};margin:26px 0 18px;"></div>
        <div style="font-size:12px;font-weight:700;letter-spacing:.6px;text-transform:uppercase;color:{ACCENT_COLOR};margin-bottom:10px;">English</div>
        {english}
      </td></tr>
      <tr><td style="padding:6px 28px 24px;font-family:{_FONT};font-size:12px;line-height:1.7;color:{MUTED};">
        {footnote}
        <div style="margin-top:12px;color:{MUTED};">
          {BRAND_NAME} · <a href="{BRAND_URL}" style="color:{BRAND_COLOR};text-decoration:none;">{BRAND_URL.replace('https://', '')}</a><br>
          {escape(SUPPORT_ADDRESS)} — pesan otomatis, balasan ke alamat ini tidak dibaca / automated message, replies are not read.
        </div>
      </td></tr>
    </table>
  </td></tr>
</table>
</body>
</html>"""


def _paragraph(text) -> str:
    return f'<p style="margin:0 0 14px;">{text}</p>'


def _greeting(name) -> str:
    return _paragraph(f"Yth. Bapak/Ibu/Saudara <strong>{escape(name)}</strong>,")


def reset_code(*, name, code, minutes: int = 10) -> dict:
    """The password-reset code, the mail `/auth/forgot-password` sends."""
    subject = _header_line("[ScanGrade] Kode Reset Password / Password Reset Code")
    html_body = layout(
        title="Reset Password",
        preheader=f"Kode verifikasi Anda: {code} (berlaku {minutes} menit)",
        indonesian=(
            _greeting(name)
            + _paragraph("Kami menerima permintaan untuk mengatur ulang kata sandi akun "
                         f"{BRAND_NAME} Anda. Masukkan kode verifikasi berikut pada halaman "
                         "verifikasi:")
            + code_box(code)
            + _paragraph(f'<span style="color:{MUTED};font-size:13px;">Kode ini berlaku '
                         f"selama <strong>{escape(minutes)} menit</strong> dan hanya bisa "
                         f"dipakai sekali.</span>")
            + _paragraph(f'<span style="color:{MUTED};font-size:13px;">Jika Anda tidak '
                         f"merasa meminta ini, abaikan saja email ini — kata sandi Anda "
                         f"tidak berubah dan tidak ada yang perlu dilakukan.</span>")
        ),
        english=(
            _paragraph(f"Dear <strong>{escape(name)}</strong>,")
            + _paragraph(f"We received a request to reset the password for your {BRAND_NAME} "
                         "account. Enter this verification code on the verification page:")
            + code_box(code)
            + _paragraph(f'<span style="color:{MUTED};font-size:13px;">The code is valid for '
                         f"<strong>{escape(minutes)} minutes</strong> and can be used once.</span>")
            + _paragraph(f'<span style="color:{MUTED};font-size:13px;">If you did not request '
                         "this, ignore this email — your password is unchanged and there is "
                         "nothing to do.</span>")
        ),
        footnote="Kode ini tidak pernah diminta lewat telepon atau pesan pribadi. / "
                 "This code is never requested over the phone or in a private message.",
    )
    text = f"""Yth. Bapak/Ibu/Saudara {name},

Kami menerima permintaan untuk mengatur ulang kata sandi akun {BRAND_NAME} Anda.
Masukkan kode verifikasi berikut pada halaman verifikasi:

    {code}

Kode ini berlaku selama {minutes} menit dan hanya bisa dipakai sekali.
Jika Anda tidak merasa meminta ini, abaikan saja email ini — kata sandi Anda tidak
berubah dan tidak ada yang perlu dilakukan.

------------------------------------------------------------------
English

Dear {name},

We received a request to reset the password for your {BRAND_NAME} account.
Enter this verification code on the verification page:

    {code}

The code is valid for {minutes} minutes and can be used once.
If you did not request this, ignore this email — your password is unchanged.

--
{BRAND_NAME} · {BRAND_URL}
{SUPPORT_ADDRESS} — pesan otomatis, balasan ke alamat ini tidak dibaca /
automated message, replies are not read.
"""
    return {"subject": subject, "html": html_body, "text": text}


def activation_code(*, name, school_name, code, expires_at) -> dict:
    """The school-activation code, the mail `notify_approval` sends."""
    subject = _header_line(f"[ScanGrade] Kode Aktivasi Sekolah {school_name}")
    activate_url = f"{BRAND_URL}/auth/activate"
    html_body = layout(
        title="Aktivasi Sekolah",
        preheader=f"{school_name} disetujui — kode aktivasi Anda: {code}",
        indonesian=(
            _greeting(name)
            + _paragraph(f"Kabar baik: pendaftaran <strong>{escape(school_name)}</strong> "
                         f"telah disetujui. Gunakan kode aktivasi berikut untuk mengaktifkan "
                         "akun sekolah Anda:")
            + code_box(code)
            + details([("Sekolah", school_name), ("Berlaku hingga", expires_at)])
            + button(activate_url, "Aktivasi Sekarang")
            + _paragraph(f'<span style="color:{MUTED};font-size:13px;">Setelah aktif, seluruh '
                         "guru dan murid dapat masuk memakai akun yang dibagikan admin "
                         "sekolah.</span>")
        ),
        english=(
            _paragraph(f"Dear <strong>{escape(name)}</strong>,")
            + _paragraph(f"Good news: the registration for <strong>{escape(school_name)}</strong> "
                         "has been approved. Use this activation code to activate your school "
                         "account:")
            + code_box(code)
            + details([("School", school_name), ("Valid until", expires_at)])
            + button(activate_url, "Activate Now")
            + _paragraph(f'<span style="color:{MUTED};font-size:13px;">Once active, every '
                         "teacher and pupil can sign in with the accounts the school admin "
                         "hands out.</span>")
        ),
    )
    text = f"""Yth. Bapak/Ibu/Saudara {name},

Kabar baik: pendaftaran {school_name} telah disetujui.
Kode Aktivasi Anda:

    {code}

Sekolah     : {school_name}
Berlaku     : {expires_at}
Aktivasi    : {activate_url}

Setelah aktif, seluruh guru dan murid dapat masuk memakai akun yang dibagikan admin
sekolah.

------------------------------------------------------------------
English

Dear {name},

Good news: the registration for {school_name} has been approved.
Your activation code:

    {code}

School      : {school_name}
Valid until : {expires_at}
Activate    : {activate_url}

--
{BRAND_NAME} · {BRAND_URL}
{SUPPORT_ADDRESS} — pesan otomatis, balasan ke alamat ini tidak dibaca /
automated message, replies are not read.
"""
    return {"subject": subject, "html": html_body, "text": text}


def password_reset_by_admin(*, name, new_password) -> dict:
    """A temporary password set by an operator — the mail the school admin receives."""
    subject = _header_line("[ScanGrade] Kata Sandi Baru / New Password")
    # This mail goes to a school admin (its only caller resets `admin_sekolah`
    # accounts), so the door is that role's — not the admin alias, which forwards
    # to the one page and drops the group the reader belongs on.
    login_url = f"{BRAND_URL}{login_door_for('admin_sekolah')}"
    html_body = layout(
        title="Kata Sandi Baru",
        preheader="Kata sandi akun Anda telah diatur ulang oleh administrator.",
        indonesian=(
            _greeting(name)
            + _paragraph("Administrator telah mengatur ulang kata sandi akun "
                         f"{BRAND_NAME} Anda. Gunakan kata sandi sementara berikut untuk "
                         "masuk:")
            + code_box(new_password)
            + button(login_url, "Masuk Sekarang")
            + _paragraph(f'<span style="color:{MUTED};font-size:13px;">Demi keamanan, segera '
                         "ubah kata sandi ini setelah berhasil masuk (menu <em>Ubah "
                         "Password</em>). Jangan teruskan email ini kepada siapa pun.</span>")
        ),
        english=(
            _paragraph(f"Dear <strong>{escape(name)}</strong>,")
            + _paragraph("An administrator has reset the password on your "
                         f"{BRAND_NAME} account. Sign in with this temporary password:")
            + code_box(new_password)
            + button(login_url, "Sign In Now")
            + _paragraph(f'<span style="color:{MUTED};font-size:13px;">For your security, '
                         "change it once you are signed in (<em>Change Password</em>). Do not "
                         "forward this email to anyone.</span>")
        ),
    )
    text = f"""Yth. Bapak/Ibu/Saudara {name},

Administrator telah mengatur ulang kata sandi akun {BRAND_NAME} Anda.
Kata sandi sementara Anda:

    {new_password}

Masuk       : {login_url}

Demi keamanan, segera ubah kata sandi ini setelah berhasil masuk.
Jangan teruskan email ini kepada siapa pun.

------------------------------------------------------------------
English

Dear {name},

An administrator has reset the password on your {BRAND_NAME} account.
Temporary password:

    {new_password}

Sign in     : {login_url}

For your security, change it once you are signed in.

--
{BRAND_NAME} · {BRAND_URL}
{SUPPORT_ADDRESS} — pesan otomatis, balasan ke alamat ini tidak dibaca /
automated message, replies are not read.
"""
    return {"subject": subject, "html": html_body, "text": text}


def payment_success(*, name, plan_name, starts, ends, login_url) -> dict:
    """The receipt-and-activation mail a school gets when a payment clears."""
    subject = _header_line(f"[ScanGrade] Pembayaran Berhasil — {plan_name}")
    html_body = layout(
        title="Pembayaran Berhasil",
        preheader=f"Langganan {plan_name} aktif — terima kasih.",
        indonesian=(
            _greeting(name)
            + _paragraph("Terima kasih — pembayaran langganan "
                         f"<strong>{escape(plan_name)}</strong> Anda sudah kami terima dan "
                         "sekolah Anda kini aktif.")
            + details([("Paket", plan_name), ("Mulai", starts), ("Masa aktif hingga", ends)])
            + button(login_url or f"{BRAND_URL}{LOGIN_URL}", "Masuk ke Dashboard")
            + _paragraph(f'<span style="color:{MUTED};font-size:13px;">Invoice resmi dapat '
                         "diunduh dari halaman langganan sekolah Anda.</span>")
        ),
        english=(
            _paragraph(f"Dear <strong>{escape(name)}</strong>,")
            + _paragraph(f"Thank you — we received your payment for the "
                         f"<strong>{escape(plan_name)}</strong> subscription and your school "
                         "is now active.")
            + details([("Plan", plan_name), ("Starts", starts), ("Active until", ends)])
            + button(login_url or f"{BRAND_URL}{LOGIN_URL}", "Go to Dashboard")
            + _paragraph(f'<span style="color:{MUTED};font-size:13px;">The official invoice '
                         "can be downloaded from your school's subscription page.</span>")
        ),
    )
    text = f"""Yth. Bapak/Ibu/Saudara {name},

Terima kasih — pembayaran langganan {plan_name} Anda sudah kami terima dan sekolah
Anda kini aktif.

Paket               : {plan_name}
Mulai               : {starts}
Masa aktif hingga   : {ends}
Masuk               : {login_url or BRAND_URL + LOGIN_URL}

Invoice resmi dapat diunduh dari halaman langganan sekolah Anda.

------------------------------------------------------------------
English

Dear {name},

Thank you — we received your payment for the {plan_name} subscription and your
school is now active.

Plan          : {plan_name}
Starts        : {starts}
Active until  : {ends}
Sign in       : {login_url or BRAND_URL + LOGIN_URL}

--
{BRAND_NAME} · {BRAND_URL}
{SUPPORT_ADDRESS} — pesan otomatis, balasan ke alamat ini tidak dibaca /
automated message, replies are not read.
"""
    return {"subject": subject, "html": html_body, "text": text}


def registration_received(*, name, school_name, npsn, position=None) -> dict:
    """The acknowledgement `/auth/register` sends the school that just signed up.

    Nothing was sent before this body existed. A school filled in four fields, was
    shown "Registration Received!" in a browser, and then waited — with no message
    of its own that said the request had arrived, who reviews it, or what the next
    step is. The wait for approval is the one part of registration that happens
    *after* the page closes, so it is the part that needs an email.

    No code and no credential: the thing this body confirms is that the request
    exists, and everything it asks the reader to do next happens on a page they can
    open in their own time.
    """
    subject = _header_line(
        f"[ScanGrade] Pendaftaran Diterima — {school_name} / Registration Received")
    activate_url = f"{BRAND_URL}/auth/activate"
    rows = [("Sekolah", school_name), ("NPSN", npsn)]
    if position:
        rows.append(("Jabatan pendaftar", position))

    def _next_steps(labels):
        return ('<ol style="margin:6px 0 14px;padding-left:20px;">'
                + "".join(f'<li style="margin:4px 0;">{escape(s)}</li>' for s in labels)
                + "</ol>")

    html_body = layout(
        title="Pendaftaran Diterima",
        preheader=f"Pendaftaran {school_name} sudah kami terima dan sedang menunggu verifikasi.",
        indonesian=(
            _greeting(name)
            + _paragraph(f"Pendaftaran <strong>{escape(school_name)}</strong> telah kami "
                         "terima. Data yang Anda kirim:")
            + details(rows)
            + _paragraph("Saat ini pendaftaran berada di meja <strong>Super Admin</strong> "
                         "ScanGrade untuk diverifikasi. Anda tidak perlu melakukan apa pun "
                         "sekarang.")
            + _paragraph("Langkah selanjutnya:")
            + _next_steps([
                "Super Admin memeriksa dan menyetujui pendaftaran sekolah Anda.",
                "Kode aktivasi dikirim ke alamat email ini.",
                f"Akun diaktifkan di {activate_url}",
            ])
            + _paragraph(f'<span style="color:{MUTED};font-size:13px;">Bila pendaftaran '
                         "tidak disetujui atau ada data yang perlu diperbaiki, tim kami akan "
                         "menghubungi Anda lewat email atau nomor WhatsApp yang Anda "
                         "cantumkan.</span>")
        ),
        english=(
            _paragraph(f"Dear <strong>{escape(name)}</strong>,")
            + _paragraph(f"Your registration for <strong>{escape(school_name)}</strong> "
                         "has been received. This is what you sent us:")
            + details([("School", school_name), ("NPSN", npsn)]
                      + ([("Position", position)] if position else []))
            + _paragraph("It now sits with a ScanGrade <strong>Super Admin</strong> for "
                         "verification. There is nothing you need to do right now.")
            + _paragraph("What happens next:")
            + _next_steps([
                "A Super Admin reviews and approves your school's registration.",
                "An activation code is sent to this email address.",
                f"The account is activated at {activate_url}",
            ])
            + _paragraph(f'<span style="color:{MUTED};font-size:13px;">If the registration '
                         "is not approved or something needs correcting, our team will "
                         "contact you at the email or WhatsApp number you provided.</span>")
        ),
    )
    position_line = f"Jabatan pendaftar   : {position}\n" if position else ""
    english_position_line = f"Position      : {position}\n" if position else ""
    text = f"""Yth. Bapak/Ibu/Saudara {name},

Pendaftaran {school_name} telah kami terima. Data yang Anda kirim:

Sekolah             : {school_name}
NPSN                : {npsn}
{position_line}
Saat ini pendaftaran berada di meja Super Admin ScanGrade untuk diverifikasi.
Anda tidak perlu melakukan apa pun sekarang.

Langkah selanjutnya:
  1. Super Admin memeriksa dan menyetujui pendaftaran sekolah Anda.
  2. Kode aktivasi dikirim ke alamat email ini.
  3. Akun diaktifkan di {activate_url}

Bila pendaftaran tidak disetujui atau ada data yang perlu diperbaiki, tim kami akan
menghubungi Anda lewat email atau nomor WhatsApp yang Anda cantumkan.

------------------------------------------------------------------
English

Dear {name},

Your registration for {school_name} has been received.

School        : {school_name}
NPSN          : {npsn}
{english_position_line}
It now sits with a ScanGrade Super Admin for verification. There is nothing you
need to do right now.

What happens next:
  1. A Super Admin reviews and approves your school's registration.
  2. An activation code is sent to this email address.
  3. The account is activated at {activate_url}

--
{BRAND_NAME} · {BRAND_URL}
{SUPPORT_ADDRESS} — pesan otomatis, balasan ke alamat ini tidak dibaca /
automated message, replies are not read.
"""
    return {"subject": subject, "html": html_body, "text": text}
