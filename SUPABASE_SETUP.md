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


## Update v10.14 — Dana orang tua dipindah ke form utama

Tidak ada migration database baru.

Perubahan UI:
- `Dana orang tua khusus kos` sekarang diisi sekali di bagian atas, dekat saldo pribadi dan uang saku.
- Pilihan `Orang tua` atau `Campuran` tidak lagi menampilkan input nominal baru.
- Nominal dana orang tua pada pilihan sumber pembayaran otomatis mengambil nilai dari form utama tersebut.
- Tetap memakai kolom Supabase `kos_parent_contribution`.


## Update v10.15 — Dana simulasi bank otomatis

Tidak ada migration database baru.

Perubahan:
- Input `Dana simulasi` tidak lagi manual.
- Nilainya otomatis mengikuti `Surplus Aman Investasi`.
- Jika surplus aman investasi Rp0, simulasi bank juga Rp0.
- Durasi dan frekuensi compounding tetap bisa dipilih manual.


## Update v10.24 — Fix 504 Gateway Timeout /api/research

Tidak ada migration database baru.

- Bank, reksadana, dan saham diproses paralel.
- Scan saham dan dividen tidak lagi serial.
- Tiap cabang riset punya timeout dan hasil parsial.
- Frontend membatasi riset 35 detik dan tetap melanjutkan analisis jika sumber lambat.


## Update v10.25 — Reksadana Semua Manajer Investasi

Tidak ada migration database baru.

Perubahan:
- empat kategori reksadana tersedia sekaligus: Pasar Uang, Pendapatan Tetap, Campuran, dan Saham;
- default semua kategori aktif;
- backend memakai mode `ALL_MI_DISCOVERY_NOT_LIMITED_TO_FIXED_LIST`;
- pencarian internet tidak dibatasi Syailendra, BNI AM, atau daftar MI tertentu;
- pencarian dilakukan lintas web umum serta platform reksadana yang memuat produk dari banyak MI;
- alias MI di backend hanya digunakan untuk mengenali/menamai hasil, bukan membatasi pencarian;
- hasil reksadana memiliki `fund_category`, `manager`, `product_name`, `return_facts`, dan sumber URL;
- AI diwajibkan membandingkan produk berdasarkan kategori, profil risiko, likuiditas, biaya/horizon, dan sumber — bukan hanya return historis tertinggi.

Pilihan kategori disimpan di browser lokal. Supabase tidak membutuhkan kolom baru sehingga update ini tidak menimbulkan error schema pada `/api/state`.


## Update v10.26 — Card ringkas, Detail overlay, Bank product intelligence, Bahasa Indonesia

Tidak ada migration database baru.

Perubahan utama:
- card saham menampilkan harga/lembar, biaya 1 lot, status cukup/kurang dana, kekurangan, dan dividen secara ringkas;
- deskripsi panjang dipindahkan ke tombol `Detail` yang membuka overlay;
- card bank diprioritaskan menjadi produk penting, rate, syarat utama, dan sumber; noise menu website dibuang;
- Bank Saqu Saku Booster dan Superbank Celengan mempunyai ringkasan produk resmi agar fakta penting tidak tenggelam dalam hasil crawl;
- reksadana mempunyai card sendiri; jika sumber kosong, kategori tetap muncul dengan status belum ditemukan;
- hasil Analisis Ollama diringkas di card; teks panjang dan output mentah hanya lewat Detail;
- prompt Analisis Ollama dipaksa Bahasa Indonesia;
- Generate Catatan AI menyimpan teks biasa ke textarea meskipun model sempat membungkus output dalam JSON;
- saham hanya pencarian informasi dan tidak mengurangi saldo.


## Update v10.27 — Fix Campuran Kos, Reksadana, Bank Bersih, Catatan AI

Tidak ada migration Supabase baru.

Perubahan:
- mode `Campuran` menghitung dana orang tua yang dipakai otomatis sebagai `Kos bulanan - kontribusi pribadi`, dibatasi dana orang tua yang tersedia;
- contoh kos Rp700.000: pribadi Rp200.000 => orang tua Rp500.000; pribadi Rp330.000 => orang tua Rp370.000;
- dana orang tua tersedia tetap dipisahkan dari dana orang tua yang benar-benar dipakai;
- card bank tanpa angka bunga terverifikasi tidak ditampilkan;
- detail bank tidak lagi menampilkan dump menu/HTML panjang;
- bunga bank difilter sesuai range yang dipilih;
- reksadana memakai DuckDuckGo + Bing fallback + seed halaman resmi agar tidak 0 hasil ketika search engine gagal;
- discovery reksadana tetap tidak dibatasi daftar MI tertentu;
- Generate Catatan AI sekarang membaca JSON bertingkat `output.catatan_analisis_keuangan` dan memasukkan teksnya langsung ke form.


## Update v10.28 — Hotfix Generate Catatan AI

Tidak ada migration database baru.

Perubahan:
- parser Catatan AI kini menerima JSON bersih, JSON bertingkat `output.catatan_analisis_keuangan`, JSON yang terbungkus field `response`, dan JSON yang tercampur reasoning/model text;
- fallback regex mengekstrak `catatan_analisis_keuangan` bila JSON model tidak 100% bersih;
- respons paragraf biasa tetap diterima;
- pesan gagal lama otomatis hilang ketika Catatan diisi/diedit manual;
- textarea selalu menyimpan teks catatan, bukan object JSON.


## Update v10.29 — Indonesia-only Research + Bank Simulation Terverifikasi + AI Bahasa Indonesia

Tidak ada migration Supabase baru.

Perubahan:
- reksadana memakai Google Search Indonesia sebagai discovery utama;
- hasil reksadana dibatasi sumber investasi Indonesia tepercaya (Makmur, Ajaib, Bareksa, Bibit, BNI AM, Syailendra, Mandiri Investasi, dan MI resmi lain);
- hasil sampah seperti Gmail, film, apartemen, Wikipedia asing, Google Maps, dan situs non-investasi dibuang sebelum masuk UI/AI;
- saham tetap memakai Google Finance IDX untuk harga dan sumber Indonesia seperti IDX/Ajaib/Bareksa untuk pencarian dividen;
- simulasi bank TIDAK lagi memakai bank hasil karangan AI; hanya memakai rate yang ditemukan dari research bank Indonesia dan lolos range bunga pengguna;
- bank luar negeri seperti Bank of America, Chase, Ally tidak mungkin masuk simulasi;
- preview card bank dipendekkan; detail panjang hanya di tombol Detail;
- output Ollama yang masih berbahasa Inggris otomatis dipaksa melalui repair pass Bahasa Indonesia;
- prompt mengabaikan instruksi/noise dari snippet internet dan melarang analisis SERP/raw dump/spam/privacy;
- Generate Catatan AI tetap memakai parser v10.28.


## Update v10.30 — Current Bank Rates + Clean Fund Details

Tidak ada migration Supabase baru.

Perubahan penting:
- Bank Saqu: `Saku Booster 10%` dipisahkan dari `Deposito Reguler`; promo deposito "hingga 10%" yang sudah berakhir tidak lagi dianggap bunga deposito saat ini.
- Deposito Reguler Bank Saqu memakai tier bunga resmi terbaru yang tersedia pada halaman penyesuaian, bukan 10%.
- Krom Bank memakai data produk terstruktur resmi: Kantong Basic 6%, Kantong Boost 6,25%, Krom Flex hingga 7,5%, Krom Max hingga 8%.
- Generic parser Krom/Saqu/Superbank tidak boleh lagi mengambil angka acak dari tabel perbandingan lalu menyebutnya sebagai rate produk.
- Simulasi bank menghormati minimum penempatan. Dana Rp30.000 tidak akan disimulasikan ke Deposito Reguler Bank Saqu (min Rp1 juta) atau deposito Krom (min Rp100 ribu).
- Saku Booster tidak dipakai sebagai penempatan dana simulasi umum karena saldo produk terkait reward/cashback.
- Reksadana detail tidak lagi menampilkan dump navigasi halaman atau teks seperti YO! Inves/Help Center.
- Detail reksadana hanya menampilkan fakta produk terstruktur dan kalimat utuh; tidak dipotong di tengah kata/kalimat.


## Update v10.31 — Fix Simulasi Crypto & Saham Tidak Muncul

Tidak ada migration database baru.

Perubahan:
- memperbaiki bug jalur analisis utama yang lupa memanggil `renderMarketSimulation(...)`;
- card simulasi BTC/ETH/SOL sekarang muncul kembali setelah analisis selesai;
- parser simulasi crypto toleran terhadap response `items` langsung maupun `crypto.items`;
- section simulasi otomatis dibuka setelah render;
- restore hasil analisis terakhir juga membuka kembali section Crypto & Saham;
- ditambah label terpisah `Crypto` dan `Saham IDX` agar hasil tidak terlihat seperti satu blok kosong;
- jika snapshot crypto benar-benar tidak tersedia, UI menampilkan alasan yang jelas, bukan section kosong.
