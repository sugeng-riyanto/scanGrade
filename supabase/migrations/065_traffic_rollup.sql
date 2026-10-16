-- ──────────────────────────────────────────────────────────────
-- Migration 065: tabel ringkasan lalu lintas (traffic rollup)
-- ──────────────────────────────────────────────────────────────
-- Jalankan lewat deploy/apply_migration.py (dry run dulu, baru --commit), atau
-- tempel di Supabase SQL Editor. Dry run wajib: berkas ini mengubah skema.
--
-- APA YANG DITAMBAHKAN
-- ────────────────────
-- Dua tabel ringkasan dan fungsi-fungsi penulisnya:
--
--   traffic_rollup_hourly          satu baris per (jam, kelompok rute, sekolah)
--   traffic_rollup_daily           bentuk yang sama, per hari, + sesi unik
--   traffic_rollup_merge_buckets   menjumlahkan dua larik ember durasi
--   traffic_rollup_bucket_total    agregat dari fungsi di atas (untuk GROUP BY)
--   traffic_rollup_add(jsonb)      menambahkan satu batch hitungan (delta)
--   traffic_rollup_sessions(jsonb) mencatat sesi unik harian (bukan delta)
--   traffic_rollup_purge(int, int) membuang baris yang sudah lebih tua dari N hari
--
-- KENAPA DUA TABEL, BUKAN AGREGASI SAAT DIBUKA
-- ────────────────────────────────────────────
-- Kotak ini 1 vCPU dengan tiga worker gevent dan ~500 murid per sekolah. Setiap
-- angka di halaman super-admin sebelumnya adalah `count="exact"` langsung ke
-- Supabase — 100-165 ms per putaran. Menghitung ulang "request per jam selama 30
-- hari terakhir" saat halaman dibuka akan menjadikan halaman analitik itu sendiri
-- sebagai beban yang ia ukur. Isinya sudah dihitung, jadi halaman hanya membaca.
--
-- KENAPA YANG DITULIS ADALAH *DELTA*, DAN KENAPA ITU AMAN
-- ───────────────────────────────────────────────────────
-- Tiga worker menerima request yang berbeda-beda, jadi tidak ada satu proses pun
-- yang melihat seluruh lalu lintas. Yang bisa dilakukan masing-masing adalah
-- menambahkan bagiannya sendiri, berkala, dalam satu batch. Maka `on conflict do
-- update` di sini **menjumlahkan**, bukan menggantikan.
--
-- Karena itu pula sebuah batch dengan kunci yang muncul dua kali harus dijumlahkan
-- lebih dulu, bukan ditolak: PostgreSQL menolak satu pernyataan `insert ... on
-- conflict` yang menyentuh baris yang sama dua kali ("ON CONFLICT DO UPDATE command
-- cannot affect row a second time") **seluruhnya** — satu kunci ganda membuang
-- seluruh batch. Itu bukan hipotesis: probe pertama terhadap fungsi ini menemukannya.
-- Jadi `src` mengelompokkan per kunci lebih dulu (`sum` untuk cacahan, agregat ember
-- untuk lariknya, `max` untuk sesi unik), dan penelepon tidak perlu tahu.
--
-- `grain` ikut masuk kunci pengelompokan, dan itu bukan kelebihan: satu hari dan satu
-- jam pada tengah malam adalah **instan yang sama persis**, jadi tanpa `grain` sebuah
-- baris harian bisa dilipat ke dalam baris jam pada 00:00 dan angka hariannya hilang.
--
-- `duration_bucket_counts` adalah larik bigint berpanjang tetap, bukan jsonb:
-- penjumlahannya jadi seposisi, satu pernyataan tanpa membongkar peta kunci. Batas
-- ember ada di Python (`traffic_rollup_service.DURATION_BOUNDS_MS`) dan **tidak** di
-- sini — dua daftar batas, satu di SQL dan satu di Python, adalah dua definisi dari
-- satu hal.
--
-- PRIVASI: TIDAK ADA BARIS PER MURID
-- ──────────────────────────────────
-- Tidak ada `student_id`, tidak ada `user_id`, tidak ada IP, tidak ada cookie
-- mentah, dan tidak ada user-agent mentah di kedua tabel ini. Yang tersimpan
-- hanya cacah: berapa request, berapa 4xx, berapa 5xx, perangkat kasar
-- (`mobile`/`desktop`/`other`, bukan sidik jari perangkat), dan satu **cacah**
-- sesi unik per hari yang datang dari `SCARD` sebuah himpunan di Redis yang
-- anggotanya sudah di-hash dan kedaluwarsa 48 jam. Angka itu menjawab "berapa
-- banyak orang memakai sistem ini hari itu", bukan "siapa". Isi halaman murid,
-- ujian, jawaban dan nilai tidak pernah masuk ke sini.
--
-- CATATAN `school_key`
-- ────────────────────
-- `school_id` boleh NULL (request tanpa sesi sekolah: halaman publik, `/health`,
-- aset statis). Sebuah UNIQUE di atas kolom yang boleh NULL tidak pernah cocok
-- untuk NULL, karena NULL ≠ NULL — jadi `on conflict` akan selalu menambah baris
-- baru, bukan menjumlahkan. `school_key` adalah bentuk teksnya (`''` untuk NULL)
-- dan itulah yang dijadikan kunci. Ia **diturunkan di sini** dari `school_id`,
-- bukan dikirim penelepon: satu bentuk yang bisa salah di dua tempat adalah satu
-- bentuk yang pasti akan salah di salah satunya.

-- ── tabel ────────────────────────────────────────────────────

create table if not exists public.traffic_rollup_hourly (
    id                    bigserial primary key,
    -- Awal periode, dipotong ke jam penuh (UTC). Semua batas waktu di tabel ini
    -- UTC; yang mengubahnya ke WIB adalah `tz` filter milik template.
    period_start          timestamptz not null,
    -- Kelompok rute, mis. `/student`, `/teacher`, `/api`, `/static`, `/other`.
    route_group           text        not null,
    school_id             uuid        null,
    school_key            text        not null default '',
    requests              bigint      not null default 0,
    status_2xx            bigint      not null default 0,
    status_3xx            bigint      not null default 0,
    status_4xx            bigint      not null default 0,
    status_5xx            bigint      not null default 0,
    device_mobile         bigint      not null default 0,
    device_desktop        bigint      not null default 0,
    device_other          bigint      not null default 0,
    -- Cacah per ember waktu respons; batasnya ada di Python (lihat kepala berkas).
    duration_bucket_counts bigint[]   not null default '{}'::bigint[],
    duration_sum_ms       double precision not null default 0,
    duration_max_ms       integer     not null default 0,
    updated_at            timestamptz not null default now(),
    constraint traffic_rollup_hourly_key
        unique (period_start, route_group, school_key)
);

create index if not exists traffic_rollup_hourly_period_idx
    on public.traffic_rollup_hourly (period_start desc);

create table if not exists public.traffic_rollup_daily (
    id                    bigserial primary key,
    -- Awal periode, dipotong ke hari penuh (UTC).
    period_start          timestamptz not null,
    route_group           text        not null,
    school_id             uuid        null,
    school_key            text        not null default '',
    requests              bigint      not null default 0,
    status_2xx            bigint      not null default 0,
    status_3xx            bigint      not null default 0,
    status_4xx            bigint      not null default 0,
    status_5xx            bigint      not null default 0,
    device_mobile         bigint      not null default 0,
    device_desktop        bigint      not null default 0,
    device_other          bigint      not null default 0,
    duration_bucket_counts bigint[]   not null default '{}'::bigint[],
    duration_sum_ms       double precision not null default 0,
    duration_max_ms       integer     not null default 0,
    -- Bukan delta. Sesi unik seharian tidak bisa dijumlahkan antar batch — tiga
    -- worker yang masing-masing melihat 100 sesi tidak berarti 300 orang. Nilainya
    -- adalah `max` dari pembacaan, dan naik monoton sepanjang hari karena himpunan
    -- di Redis hanya bertambah. Lihat `traffic_rollup_sessions`.
    unique_sessions       bigint      not null default 0,
    updated_at            timestamptz not null default now(),
    constraint traffic_rollup_daily_key
        unique (period_start, route_group, school_key)
);

create index if not exists traffic_rollup_daily_period_idx
    on public.traffic_rollup_daily (period_start desc);

-- ── penjumlah dua ember durasi, dan agregatnya ───────────────
-- Saat panjang kedua larik berbeda — tanda daftar batas di Python berubah — yang
-- benar adalah memakai larik yang **lebih panjang** sambil mengganjal yang pendek
-- dengan nol. Versi pertama fungsi ini membuang batch yang lebih panjang, yaitu
-- kehilangan satu menit data tanpa sepatah kata.

create or replace function public.traffic_rollup_merge_buckets(a bigint[], b bigint[])
returns bigint[]
language sql
immutable
set search_path = public
as $$
    select coalesce(array_agg(coalesce(u.x, 0) + coalesce(u.y, 0) order by u.ord),
                    '{}'::bigint[])
    from unnest(
        case when coalesce(array_length(a, 1), 0) >= coalesce(array_length(b, 1), 0)
             then a else b end,
        case when coalesce(array_length(a, 1), 0) >= coalesce(array_length(b, 1), 0)
             then b else a end
    ) with ordinality as u(x, y, ord)
$$;

-- Agregat (bukan fungsi biasa) supaya `src` di bawah bisa `group by` dan tetap
-- menjumlahkan lariknya elemen demi elemen. `initcond` kosong aman: menggabungkan
-- '{}' dengan larik apa pun mengembalikan larik itu (lihat fungsi di atas).
create or replace aggregate public.traffic_rollup_bucket_total (bigint[]) (
    sfunc    = public.traffic_rollup_merge_buckets,
    stype    = bigint[],
    initcond = '{}'
);

-- ── penulis: tambahkan satu batch delta ──────────────────────

create or replace function public.traffic_rollup_add(p_rows jsonb)
returns integer
language plpgsql
security definer
set search_path = public
as $$
declare
    touched integer := 0;
begin
    if p_rows is null or jsonb_typeof(p_rows) <> 'array' then
        return 0;
    end if;

    with raw as (
        select
            (r->>'period_start')::timestamptz                    as period_start,
            coalesce(r->>'route_group', '/other')                as route_group,
            nullif(r->>'school_id', '')::uuid                     as school_id,
            -- school_key diturunkan, tidak dikirim: lihat kepala berkas.
            coalesce(r->>'school_id', '')                        as school_key,
            coalesce((r->>'requests')::bigint, 0)                as requests,
            coalesce((r->>'status_2xx')::bigint, 0)              as status_2xx,
            coalesce((r->>'status_3xx')::bigint, 0)              as status_3xx,
            coalesce((r->>'status_4xx')::bigint, 0)              as status_4xx,
            coalesce((r->>'status_5xx')::bigint, 0)              as status_5xx,
            coalesce((r->>'device_mobile')::bigint, 0)           as device_mobile,
            coalesce((r->>'device_desktop')::bigint, 0)          as device_desktop,
            coalesce((r->>'device_other')::bigint, 0)            as device_other,
            -- Lariknya datang sebagai JSON array dari PostgREST, dan sebuah cast
            -- `jsonb -> bigint[]` langsung **tidak** bisa: PostgreSQL membaca
            -- `[0, 0, 5]` sebagai literal larik lalu menolaknya ("malformed array
            -- literal"). Yang benar adalah membongkar elemennya satu per satu — dan
            -- itu juga yang menjaga urutannya, karena urutan ember adalah artinya.
            coalesce((select array_agg(e.value::bigint order by e.ord)
                      from jsonb_array_elements_text(r->'duration_bucket_counts')
                           with ordinality as e(value, ord)),
                     '{}'::bigint[]) as duration_bucket_counts,
            coalesce((r->>'duration_sum_ms')::double precision, 0) as duration_sum_ms,
            coalesce((r->>'duration_max_ms')::integer, 0)        as duration_max_ms,
            (r->>'grain')                                        as grain
        from jsonb_array_elements(p_rows) as r
    ),
    -- Satu kunci, satu baris: `grain` ikut, karena tengah malam satu hari dan
    -- tengah malam satu jam adalah instan yang sama.
    src as (
        select
            period_start,
            route_group,
            (array_agg(school_id) filter (where school_id is not null))[1] as school_id,
            school_key,
            grain,
            sum(requests)                                        as requests,
            sum(status_2xx)                                      as status_2xx,
            sum(status_3xx)                                      as status_3xx,
            sum(status_4xx)                                      as status_4xx,
            sum(status_5xx)                                      as status_5xx,
            sum(device_mobile)                                   as device_mobile,
            sum(device_desktop)                                  as device_desktop,
            sum(device_other)                                    as device_other,
            public.traffic_rollup_bucket_total(duration_bucket_counts) as duration_bucket_counts,
            sum(duration_sum_ms)                                 as duration_sum_ms,
            max(duration_max_ms)                                 as duration_max_ms
        from raw
        group by period_start, route_group, school_key, grain
    ),
    hourly as (
        insert into public.traffic_rollup_hourly as t (
            period_start, route_group, school_id, school_key, requests,
            status_2xx, status_3xx, status_4xx, status_5xx,
            device_mobile, device_desktop, device_other,
            duration_bucket_counts, duration_sum_ms, duration_max_ms, updated_at)
        select period_start, route_group, school_id, school_key, requests,
               status_2xx, status_3xx, status_4xx, status_5xx,
               device_mobile, device_desktop, device_other,
               duration_bucket_counts, duration_sum_ms, duration_max_ms, now()
        from src where grain = 'hour'
        on conflict (period_start, route_group, school_key) do update set
            requests        = t.requests + excluded.requests,
            status_2xx      = t.status_2xx + excluded.status_2xx,
            status_3xx      = t.status_3xx + excluded.status_3xx,
            status_4xx      = t.status_4xx + excluded.status_4xx,
            status_5xx      = t.status_5xx + excluded.status_5xx,
            device_mobile   = t.device_mobile + excluded.device_mobile,
            device_desktop  = t.device_desktop + excluded.device_desktop,
            device_other    = t.device_other + excluded.device_other,
            duration_bucket_counts = public.traffic_rollup_merge_buckets(
                t.duration_bucket_counts, excluded.duration_bucket_counts),
            duration_sum_ms = t.duration_sum_ms + excluded.duration_sum_ms,
            duration_max_ms = greatest(t.duration_max_ms, excluded.duration_max_ms),
            updated_at      = now(),
            -- `school_id` hanya diisi kalau belum ada: baris pertama yang membuat
            -- kelompok ini sudah menyimpan id sekolahnya dan batch berikutnya
            -- membawa id yang sama, jadi menimpanya hanya membuat `updated_at`
            -- terlihat seperti sebuah perubahan yang bukan perubahan.
            school_id       = coalesce(t.school_id, excluded.school_id)
        returning 1
    ),
    daily as (
        insert into public.traffic_rollup_daily as t (
            period_start, route_group, school_id, school_key, requests,
            status_2xx, status_3xx, status_4xx, status_5xx,
            device_mobile, device_desktop, device_other,
            duration_bucket_counts, duration_sum_ms, duration_max_ms, updated_at)
        select period_start, route_group, school_id, school_key, requests,
               status_2xx, status_3xx, status_4xx, status_5xx,
               device_mobile, device_desktop, device_other,
               duration_bucket_counts, duration_sum_ms, duration_max_ms, now()
        from src where grain = 'day'
        on conflict (period_start, route_group, school_key) do update set
            requests        = t.requests + excluded.requests,
            status_2xx      = t.status_2xx + excluded.status_2xx,
            status_3xx      = t.status_3xx + excluded.status_3xx,
            status_4xx      = t.status_4xx + excluded.status_4xx,
            status_5xx      = t.status_5xx + excluded.status_5xx,
            device_mobile   = t.device_mobile + excluded.device_mobile,
            device_desktop  = t.device_desktop + excluded.device_desktop,
            device_other    = t.device_other + excluded.device_other,
            duration_bucket_counts = public.traffic_rollup_merge_buckets(
                t.duration_bucket_counts, excluded.duration_bucket_counts),
            duration_sum_ms = t.duration_sum_ms + excluded.duration_sum_ms,
            duration_max_ms = greatest(t.duration_max_ms, excluded.duration_max_ms),
            updated_at      = now(),
            school_id       = coalesce(t.school_id, excluded.school_id)
        returning 1
    )
    select (select count(*) from hourly) + (select count(*) from daily) into touched;

    return touched;
end;
$$;

-- ── penulis: sesi unik seharian (bukan delta) ────────────────

create or replace function public.traffic_rollup_sessions(p_rows jsonb)
returns integer
language plpgsql
security definer
set search_path = public
as $$
declare
    touched integer := 0;
begin
    if p_rows is null or jsonb_typeof(p_rows) <> 'array' then
        return 0;
    end if;

    with raw as (
        select
            (r->>'period_start')::timestamptz            as period_start,
            coalesce(r->>'route_group', '/other')        as route_group,
            nullif(r->>'school_id', '')::uuid             as school_id,
            coalesce(r->>'school_id', '')                as school_key,
            coalesce((r->>'unique_sessions')::bigint, 0) as unique_sessions
        from jsonb_array_elements(p_rows) as r
    ),
    src as (
        -- Satu kunci satu baris, dengan alasan yang sama seperti di atas; `max`
        -- sebelum `greatest` karena yang dikirim adalah total, bukan tambahan.
        select period_start, route_group,
               (array_agg(school_id) filter (where school_id is not null))[1] as school_id,
               school_key,
               max(unique_sessions) as unique_sessions
        from raw
        group by period_start, route_group, school_key
    ),
    upsert as (
        insert into public.traffic_rollup_daily as t (
            period_start, route_group, school_id, school_key, unique_sessions, updated_at)
        select period_start, route_group, school_id, school_key, unique_sessions, now()
        from src
        on conflict (period_start, route_group, school_key) do update set
            -- `greatest`, bukan `+`: angka ini total sejauh hari itu, bukan
            -- tambahan sejak pembacaan terakhir.
            unique_sessions = greatest(t.unique_sessions, excluded.unique_sessions),
            updated_at      = now(),
            school_id       = coalesce(t.school_id, excluded.school_id)
        returning 1
    )
    select count(*) from upsert into touched;

    return touched;
end;
$$;

-- ── retensi: buang yang sudah terlalu tua ────────────────────

create or replace function public.traffic_rollup_purge(
    p_keep_hourly_days integer default 14,
    p_keep_daily_days  integer default 400
)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
    cut_hourly timestamptz := date_trunc('hour', now())
                              - make_interval(days => greatest(p_keep_hourly_days, 1));
    cut_daily  timestamptz := date_trunc('day', now())
                              - make_interval(days => greatest(p_keep_daily_days, 1));
    del_hourly integer := 0;
    del_daily  integer := 0;
begin
    delete from public.traffic_rollup_hourly where period_start < cut_hourly;
    get diagnostics del_hourly = row_count;
    delete from public.traffic_rollup_daily where period_start < cut_daily;
    get diagnostics del_daily = row_count;
    return jsonb_build_object(
        'hourly_deleted', del_hourly,
        'daily_deleted', del_daily,
        'hourly_before', cut_hourly,
        'daily_before', cut_daily);
end;
$$;

-- ── RLS: tidak ada policy, dan itu jawabannya ────────────────
-- Tidak ada CREATE POLICY sama sekali di sini. Tidak ada sekolah yang boleh
-- membaca angka lalu lintas lintas sekolah, jadi tidak ada klausa `using` yang
-- benar untuk ditulis. RLS menyala tanpa policy berarti default-deny — `anon` dan
-- `authenticated` melihat nol baris — dan service key (yang menembus RLS) adalah
-- satu-satunya pembaca. Berbeda dari 064, yang punya policy `TO authenticated`
-- karena di sana barisnya memang milik satu sekolah.

alter table public.traffic_rollup_hourly enable row level security;
alter table public.traffic_rollup_daily  enable row level security;

-- ── izin menjalankan fungsi ──────────────────────────────────
-- PostgreSQL memberi EXECUTE kepada `public` secara bawaan, dan sebuah fungsi
-- `security definer` yang bisa dipanggil siapa pun adalah penulis tabel ini yang
-- terbuka: pemegang kunci anon bisa menyuntikkan angka lalu lintas palsu. Jadi
-- haknya dicabut dulu, lalu diberikan hanya kepada peran yang benar-benar
-- memanggilnya. Ini bukan kebocoran data — yang bisa ditulis cuma cacah — tapi
-- dashboard yang bisa dibohongi lebih buruk daripada dashboard yang kosong.

revoke all on function public.traffic_rollup_add(jsonb) from public;
revoke all on function public.traffic_rollup_sessions(jsonb) from public;
revoke all on function public.traffic_rollup_purge(integer, integer) from public;
revoke all on function public.traffic_rollup_merge_buckets(bigint[], bigint[]) from public;
revoke all on function public.traffic_rollup_bucket_total(bigint[]) from public;

grant execute on function public.traffic_rollup_add(jsonb) to service_role;
grant execute on function public.traffic_rollup_sessions(jsonb) to service_role;
grant execute on function public.traffic_rollup_purge(integer, integer) to service_role;

comment on table public.traffic_rollup_hourly is
    'Ringkasan lalu lintas per jam. Agregat saja — tidak ada murid, IP, atau cookie.';
comment on table public.traffic_rollup_daily is
    'Ringkasan lalu lintas per hari, termasuk cacah sesi unik (bukan daftar sesi).';
comment on function public.traffic_rollup_add(jsonb) is
    'Menjumlahkan satu batch delta ke tabel ringkasan; kunci ganda dalam satu batch digabung lebih dulu.';
comment on function public.traffic_rollup_sessions(jsonb) is
    'Mencatat cacah sesi unik harian dengan greatest(), karena ia total dan bukan delta.';
comment on function public.traffic_rollup_purge(integer, integer) is
    'Retensi: membuang baris ringkasan yang lebih tua dari N hari.';
comment on function public.traffic_rollup_merge_buckets(bigint[], bigint[]) is
    'Menjumlahkan dua larik ember durasi seposisi, memakai larik yang lebih panjang.';
comment on aggregate public.traffic_rollup_bucket_total(bigint[]) is
    'Agregat penjumlah ember durasi, supaya batch bisa dikelompokkan per kunci.';
