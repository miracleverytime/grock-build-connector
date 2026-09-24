# Grok Build Connector

Mendaftarkan akun xAI baru lewat email sekali pakai (Tempik), lalu menyambungkannya ke **9router** sebagai provider **Grok Build** (`grok-cli`).

Bukan login akun yang sudah ada. Tiap run membuat akun dari nol.

## Alur

```
9router /api/oauth/grok-cli/device-code   -> user_code
Tempik  POST /api/inboxes                 -> email sekali pakai
browser accounts.x.ai/oauth2/device
    isi kode -> Continue
    Sign up -> Sign up with email -> isi email
    kode 6 digit dari inbox Tempik -> Confirm email
    first name, last name, password acak -> Complete sign up
    Continue (kode kedua) -> Allow
9router /api/oauth/grok-cli/poll          -> koneksi tersimpan
```

Token tidak lewat skrip ini. 9router yang menukar device code dan menyimpan koneksinya.

## Prasyarat

- 9router berjalan di `http://localhost:20128`.
- Python 3.11+ dengan `httpx`, `playwright`, `camoufox`, plus browser Camoufox (`python -m camoufox fetch`, sekali saja).
- Instance Tempik di `https://tempik.miraclelabs.my.id` (ubah di `config.toml` kalau pindah).

## Jalankan

```powershell
# Satu akun, sesuai [grok] count di config.toml
python connector.py

# Lima akun
python connector.py 5

# Tampilkan browser
python connector.py --headed 1
```

Tidak ada file akun masukan. Email dibuat sendiri tiap akun. Kredensial akun yang berhasil disimpan di `data/accounts.jsonl`.

## Config

`config.toml`:

| Section | Key | Arti |
|---|---|---|
| `proxy` | `mode` | `none` atau `file` (round-robin dari `proxies.txt`) |
| `signup` | `headless` | `true` background, `false` tampil |
| `signup` | `delay` | jeda antar akun, ditambah acak 0-5 detik |
| `signup` | `retry` | ulang per akun saat gagal (email baru tiap ulang) |
| `9router` | `base` | URL dashboard |
| `9router` | `purge_errors` | hapus koneksi `grok-cli` yang error sebelum mulai |
| `grok` | `count` | jumlah akun per run, kalau tidak disebut di CLI |
| `grok` | `poll_interval` | jarak minimal antar poll, detik |
| `grok` | `poll_timeout` | batas tunggu authorize selesai, detik |
| `tempik` | `base` | URL instance Tempik |
| `tempik` | `code_timeout` | batas tunggu email kode verifikasi, detik |

## Output

- `data/results.jsonl` — hasil per akun.
- `data/accounts.jsonl` — email, password, dan nama akun yang terdaftar.
- `data/done.txt` — email yang sudah terhubung.
- `data/connector.log` — log lengkap.
- `data/shots/` — screenshot saat gagal (`*_error`) dan saat selesai (`*_done`).

## Catatan

Form nama dan password xAI belum terlihat tanpa kode email sungguhan, jadi selector-nya dicocokkan dari `name` dan placeholder yang umum (`firstName`, `lastName`, `input[type=password]`, tombol "Complete sign up"). Kalau run pertama berhenti di situ, screenshot di `data/shots/` menunjukkan field yang sebenarnya.
