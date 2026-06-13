# API Reference

## Authentication

All endpoints except login/register/pricing require authentication via:
- **Cookie**: `access_token` (set on login)
- **Header**: `Authorization: Bearer <token>`

Unathenticated requests return 401.

## Endpoints

### Auth (`/auth/`)

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET/POST | /auth/register | No | Register new school |
| GET/POST | /auth/login | No | Admin login |
| GET/POST | /auth/login-user | No | Teacher/student login |
| POST | /auth/logout | Yes | Logout (clear session) |

### Teacher Exams (`/teacher/`)

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | /teacher/dashboard | Teacher | Dashboard with stats |
| GET/POST | /teacher/exams/new | Teacher | Create exam |
| GET/POST/DELETE | /teacher/exams/<id> | Teacher | Exam detail/edit/delete |
| POST | /teacher/exams/<id>/publish-exam | Teacher | Publish exam |
| POST | /teacher/exams/<id>/delete | Teacher | Delete exam |
| POST | /teacher/exams/<id>/duplicate | Teacher | Duplicate exam |
| POST | /teacher/exams/<id>/toggle-status | Teacher | Toggle active/draft |
| POST | /teacher/exams/<id>/toggle-visibility | Teacher | Toggle published |
| GET | /teacher/preview/<id> | Teacher | Preview exam |
| GET/POST | /teacher/exams/<id>/upload-pdf | Teacher | Upload exam PDF |
| GET | /teacher/results?exam_id=xxx | Teacher | Results page |
| GET | /teacher/grade/<sub_id> | Teacher | Grade detail page |
| POST | /teacher/grade/<sub_id>/override | Teacher | Override score |
| POST | /teacher/publish/<exam_id> | Teacher | Publish scores |
| GET | /teacher/export/xlsx?exam_id=xxx | Teacher | Export XLSX |
| GET | /teacher/export/pdf?exam_id=xxx | Teacher | Export PDF |
| GET | /teacher/ai-settings | Teacher | AI settings page |
| POST | /teacher/ai-settings/add-key | Teacher | Add AI API key |
| POST | /teacher/ai-settings/<id>/toggle | Teacher | Toggle AI key |
| POST | /teacher/ai-settings/<id>/delete | Teacher | Delete AI key |
| POST | /teacher/ai-settings/save-prompt | Teacher | Save prompt template |

### Student (`/student/`)

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | /student/dashboard | Student | Dashboard |
| GET | /student/exams | Student | Available exams |
| GET | /student/exams/<id> | Student | Take exam |
| POST | /student/exams/<id>/submit | Student | Submit exam |
| GET | /student/results | Student | Results list |
| GET | /student/result/<sub_id> | Student | Result detail |
| GET | /student/result/<sub_id>/pdf | Student | Download result PDF |

### Admin Sekolah (`/admin-sekolah/`)

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | /admin-sekolah/dashboard | Admin | Dashboard |
| GET/POST | /admin-sekolah/classes | Admin | Manage classes |
| POST | /admin-sekolah/classes/<id>/edit | Admin | Edit class |
| POST | /admin-sekolah/classes/<id>/delete | Admin | Delete class |
| GET | /admin-sekolah/students | Admin | Student list |
| POST | /admin-sekolah/students/add | Admin | Add student |
| POST | /admin-sekolah/students/<id>/edit | Admin | Edit student |
| POST | /admin-sekolah/students/<id>/delete | Admin | Delete student |
| POST | /admin-sekolah/students/<id>/reset-password | Admin | Reset password |
| GET | /admin-sekolah/teachers | Admin | Teacher list |
| POST | /admin-sekolah/teachers/add | Admin | Add teacher |
| POST | /admin-sekolah/teachers/<id>/edit | Admin | Edit teacher |
| POST | /admin-sekolah/teachers/<id>/delete | Admin | Delete teacher |
| GET | /admin-sekolah/subscription | Admin | Subscription page |
| GET | /admin-sekolah/invoices | Admin | Invoices list |
| GET | /admin-sekolah/profil | Admin | School profile |

### API Endpoints (`/api/`)

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| POST | /api/scan/process | Yes | OMR scan image |
| POST | /api/violation/log | Yes | Log anti-cheat violation |
| GET | /api/violation/count | Yes | Get violation count |
| POST | /api/student/auto-save | Yes | Auto-save draft |
| POST | /api/student/sync-draft | Yes | Sync draft |
| POST | /api/grade/auto-save/<sub_id> | Yes | Save grading draft |
| POST | /api/grade/batch | Yes | Batch grade exam |
| POST | /api/grade/ai-suggest | Yes | AI essay grading |
| POST | /api/ai/test-key | Yes | Test AI API key |
| POST | /api/students/import | Yes | CSV student import |
| GET | /api/exams/<id>/report | Yes | Exam report (?format=excel) |
| GET/POST | /api/activation/redeem | Yes | Redeem activation code |
| GET | /api/transaction/status | Yes | Check payment status |
| POST | /api/demo-request | No | Landing page demo request |

### Super Admin (`/super-admin/`)

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | /super-admin/dashboard | Super Admin | Dashboard |
| GET | /super-admin/midtrans | Super Admin | Midtrans settings |
| POST | /super-admin/midtrans/save | Super Admin | Save Midtrans config |
| GET | /super-admin/plans | Super Admin | Subscription plans |
| POST | /super-admin/plans/save | Super Admin | Save/update plan |
| GET | /super-admin/activation-codes | Super Admin | Activation codes |
| POST | /super-admin/activation-codes/generate | Super Admin | Generate code |
| GET | /super-admin/pricing | Super Admin | Pricing settings |
| POST | /super-admin/pricing/save | Super Admin | Save pricing config |
| GET | /super-admin/demo-settings | Super Admin | Demo settings |
| POST | /super-admin/demo-settings/save | Super Admin | Save demo config |
| GET | /super-admin/whatsapp-settings | Super Admin | WhatsApp number |
| POST | /super-admin/whatsapp-settings/save | Super Admin | Save WhatsApp |

### Public (`/`)

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | / | No | Landing page |
| GET | /pricing | No | Pricing page |
| GET | /demo | No | Demo page |
| GET | /tutorial/guru | No | Teacher tutorial |
| GET | /tutorial/murid | No | Student tutorial |
| GET | /tutorial/admin-sekolah | No | Admin tutorial |
| GET | /health | No | Health check |

## Error Response Format

All API errors return:
```json
{
    "success": false,
    "error": "ERROR_CODE",
    "message": "User-friendly message in Bahasa Indonesia",
    "timestamp": "2026-06-08T12:00:00+00:00"
}
```

Common error codes:
- `FILE_TOO_LARGE` (413) — File exceeds size limit
- `AI_PROCESSING_ERROR` (422) — AI grading failed
- `GRADING_ERROR` (500) — Scoring calculation error
- `VALIDATION_ERROR` (422) — Invalid input
- `FEATURE_LIMIT` (403) — Tier limit exceeded
- `RATE_LIMITED` (429) — Too many requests
