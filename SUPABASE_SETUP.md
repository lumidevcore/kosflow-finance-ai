# Supabase setup — KosFlow Finance v10.5

1. Buat project Supabase.
2. Buka SQL Editor, jalankan `supabase/schema.sql`.
3. Di Supabase > Project Settings > API, ambil:
   - Project URL
   - `service_role` key (RAHASIA, jangan taruh di frontend/GitHub)
4. Di Wasmer > Environment Vars tambahkan:
   - `SUPABASE_URL` = Project URL
   - `SUPABASE_SERVICE_ROLE_KEY` = service_role key
5. Redeploy Wasmer.

## Yang disimpan
- Device/browser ID anonim.
- Setting budget: saldo, uang saku, kos, dana aman, kebutuhan, risiko, saham, bank, catatan, sumber pembayaran kos.
- Konfigurasi Ollama: bridge URL, model, pairing token **dalam bentuk AES-GCM ciphertext**. Kunci decrypt hanya tersimpan di browser lokal.
- Riwayat analisis AI.
- Snapshot crypto/saham yang dipakai pada setiap analisis.
- Sumber riset bank digital/reksadana yang dipakai pada setiap analisis.

Jika CMD bridge ditutup lalu dibuka lagi, token baru memang berubah. Klik **Edit Pairing**, masukkan token CMD baru, lalu klik **Simpan & Hubungkan Ulang**. Token baru otomatis menggantikan token lama di penyimpanan lokal dan Supabase.


## Update v10.6 — Range bunga bank multi-select

Jalankan bagian migration berikut di SQL Editor jika schema v10.5 sudah pernah dibuat:

```sql
alter table public.finance_settings
  add column if not exists bank_interest_ranges jsonb
  not null default '["0.5-4","4-6"]'::jsonb;
```

Pilihan di UI:
- 0,5%–4%
- 4%–6%
- 6%–10%

Pengguna dapat memilih satu, dua, atau semua range sekaligus. Pilihan disimpan ke browser lokal dan Supabase.


## Update v10.7 — Pencarian saham otomatis berdasarkan 1 lot

Jika database v10.6 sudah ada, jalankan migration:

```sql
alter table public.finance_settings
  add column if not exists stock_lot_mode text
  not null default 'auto';

alter table public.finance_settings
  add column if not exists stock_lot_budget numeric
  not null default 100000;

alter table public.finance_settings
  add column if not exists stock_recommendation_count integer
  not null default 5;
```

Fitur:
- 1 lot IDX dihitung sebagai 100 lembar.
- Mode otomatis mencari kandidat saham yang estimasi biaya 1 lot <= budget pengguna.
- Pengguna dapat memilih 3, 5, 8, atau 10 kandidat.
- Setting disimpan ke local storage dan Supabase.


## Update v10.9 — pendapatan manual, pembagian kos, dan dividen saham

Jalankan migration berikut jika database sebelumnya sudah ada:

```sql
alter table public.finance_settings
  add column if not exists income_weekly_min numeric(18,2) not null default 0;
alter table public.finance_settings
  add column if not exists income_weekly_max numeric(18,2) not null default 0;
alter table public.finance_settings
  add column if not exists kos_self_contribution numeric(18,2) not null default 0;
alter table public.finance_settings
  add column if not exists kos_parent_contribution numeric(18,2) not null default 0;
```

Fitur baru:
- Pendapatan aplikasi/tambahan per minggu diisi manual min/max.
- Uang saku, pendapatan aplikasi, dan pembayaran kos dipisahkan.
- Sumber kos `self`, `parent`, atau `mixed` menampilkan input nominal yang sesuai.
- Riset saham mencari info dividen terbaru dan dividend yield indikatif jika data per-saham ditemukan.


## Update v10.10 — periode skema pembayaran kos

Jika database sudah dibuat sebelumnya, jalankan:

```sql
alter table public.finance_settings
  add column if not exists kos_cycle_start date;

alter table public.finance_settings
  add column if not exists kos_funding_scope text
  not null default 'current_cycle';
```

`current_cycle` berarti skema uang sendiri/orang tua/campuran hanya berlaku untuk satu periode kos. `ongoing` berarti skema yang sama dianggap berulang setiap bulan sampai pengguna mengubahnya.


## Update v10.11 — Simulasi penempatan bank & compounding
Jika database v10.10 sudah ada, jalankan:
```sql
alter table public.finance_settings add column if not exists bank_simulation_amount numeric(18,2) not null default 0;
alter table public.finance_settings add column if not exists bank_simulation_months integer not null default 12;
alter table public.finance_settings add column if not exists bank_compound_frequency integer not null default 12;
```
Simulasi menghitung nilai akhir berdasarkan rate p.a. terverifikasi yang dipilih AI. Hasil bruto sebelum pajak/biaya.


## Update v10.12 — Tanggal bayar selanjutnya + simulasi market

Jika database v10.11 sudah ada, jalankan:

```sql
alter table public.finance_settings
  add column if not exists market_simulation_months integer not null default 12;
alter table public.finance_settings
  add column if not exists market_bear_growth_pct numeric(8,3) not null default -10;
alter table public.finance_settings
  add column if not exists market_base_growth_pct numeric(8,3) not null default 8;
alter table public.finance_settings
  add column if not exists market_bull_growth_pct numeric(8,3) not null default 20;
```

Tanggal bayar kos berikutnya dihitung di browser dari `kos_cycle_start` + jumlah hari pembayaran awal.
Tidak memerlukan kolom database baru.


## Update v10.13 — Pemisahan saldo pribadi, dana orang tua, dan dana aman otomatis

Tidak ada kolom Supabase baru.

Perubahan memakai kolom yang sudah ada:
- `cash` = saldo pribadi sekarang
- `allowance` = uang saku per minggu
- `weekly_needs` = kebutuhan per minggu
- `buffer` = dana aman otomatis (`allowance - weekly_needs`)
- `kos_self_contribution` = kontribusi kos dari saldo pribadi
- `kos_parent_contribution` = dana orang tua khusus kos

Jadi migration SQL baru tidak diperlukan untuk v10.13.
