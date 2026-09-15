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
