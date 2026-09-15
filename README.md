# KosFlow AI v9

Personal finance planner yang bisa di-host online di Wasmer/GitHub tetapi tetap memakai Ollama lokal di laptop.

## Arsitektur

- **Wasmer / cloud app**
  - Menyajikan UI.
  - Mengambil snapshot crypto.
  - Mengambil saham IDX.
  - Mencari bunga bank digital.
  - Mencari reksadana.
- **Browser**
  - Menampilkan crypto live bergerak memakai Binance WebSocket.
  - Mengatur progress analisis.
  - Mengirim paket data final ke Ollama lokal.
- **Local Ollama Bridge**
  - Jalan di `127.0.0.1:8788`.
  - Meneruskan request ke Ollama `127.0.0.1:11434`.
  - Menangani CORS dan Private Network Access preflight.
  - Memerlukan pairing token, jadi endpoint generate tidak terbuka bebas ke website lain.

## Jalankan lokal

### 1. Cloud/web app secara lokal
```bash
python -m pip install -r requirements.txt
uvicorn app:app --host 127.0.0.1 --port 8787
```

Buka http://127.0.0.1:8787

### 2. Local Ollama bridge
Klik:
`local_bridge/run_bridge.bat`

CMD akan menampilkan pairing token. Masukkan token itu di web, lalu klik **Hubungkan Ollama Lokal**.

## Deploy GitHub → Wasmer

Wasmer Edge dapat deploy langsung dari repository GitHub. Push seluruh project ini ke repo GitHub, hubungkan repo tersebut ke Wasmer Edge, gunakan branch `main`, lalu deploy.

Alternatif dari folder lokal:
```bash
wasmer login
wasmer deploy
```

Wasmer mendeteksi project Python melalui `requirements.txt` / `app.py`.

## Tentang realtime

### Crypto
UI memakai Binance WebSocket untuk tick yang bergerak terus. Saat tombol analisis diklik, `/api/crypto-snapshot` mengambil snapshot REST baru dan snapshot tersebut dikunci untuk konteks Ollama.

### Saham IDX
Google Finance diprioritaskan, Yahoo Finance digunakan sebagai fallback. Data publik tidak boleh dianggap exchange-grade zero-delay. Timestamp/sumber ditampilkan ketika tersedia.

## CORS / localhost Ollama

Jangan membuka Ollama langsung ke internet. Web hosted memanggil **local bridge**, bukan port Ollama secara langsung.

Bridge:
- `Access-Control-Allow-Origin: *` karena tidak menggunakan cookies/credentials.
- endpoint `/models` dan `/generate` tetap membutuhkan `X-KosFlow-Token`.
- merespons `Access-Control-Allow-Private-Network: true` bila browser mengirim PNA preflight.
- hanya bind ke `127.0.0.1`.

Jika browser perusahaan atau kebijakan keamanan memblokir public-site → loopback access, jalankan UI lokal atau izinkan local network access untuk situs tersebut.

## Default pendapatan app yang sudah dimasukkan

- App 1: Rp517–Rp917/hari
- App 2: Rp1.400/hari
- App 3: Rp5.000–Rp10.000/hari
- App 4–6: belum diberi pendapatan harian tetap sehingga tidak dipaksakan ke estimasi.
