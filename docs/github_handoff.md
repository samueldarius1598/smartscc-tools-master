# Menggunakan Tools Master dari GitHub

Repo: https://github.com/samueldarius1598/smartscc-tools-master (publik).

Siapa pun dapat membaca dan clone source tanpa login. Untuk perubahan, gunakan
fork dan pull request; pemilik juga dapat memberi akses push melalui GitHub
Settings > Collaborators. Gunakan credential GitHub masing-masing untuk push;
token pemilik tidak dibagikan melalui source. Akses source publik tidak memberi
akses GAS/Odoo.

## Clone dan jalankan di Windows

Gunakan Python 3.13, Git for Windows, dan terminal Command Prompt. GUI memerlukan
desktop Windows. Integrasi workbook memakai Microsoft Excel bila workflow tersebut
memerlukannya. Backend juga menggunakan model/modul kustom Odoo perusahaan;
repo ini tidak memasang modul server Odoo.

```cmd
git clone https://github.com/samueldarius1598/smartscc-tools-master.git
cd smartscc-tools-master
python -m venv .venv
call .venv\Scripts\activate.bat
python tools\configure_dev_env.py
python -m pip install -r build_tools\requirements.txt
```

API key GAS diperoleh dari pemilik melalui saluran privat. Key ini memberi akses
ke konfigurasi koneksi Odoo yang digunakan aplikasi, sehingga jangan dimasukkan
ke prompt AI, screenshot, commit, atau log. Pilih salah satu:

- Buat `%APPDATA%\smartscc_tools\gas_api_key.txt` dengan isi API key saja.
  File ini berada di profil pengguna, di luar clone, dan tetap digunakan setelah
  aplikasi dimulai ulang.
- Atur environment variable `SMARTSCC_GAS_API_KEY` pada proses yang menjalankan
  aplikasi. Nilai environment yang tidak kosong mengalahkan file lokal.

Fallback CSV konfigurasi Odoo bersifat opsional dan alamatnya tidak disertakan
dalam source publik. Bila diperlukan, pemilik memberikan ID spreadsheet melalui
saluran privat untuk `SMARTSCC_GAS_FALLBACK_CONFIG_SSID` atau file
`%APPDATA%\smartscc_tools\fallback_config_ssid.txt`. Tanpa pengaturan ini,
aplikasi hanya mengambil konfigurasi melalui GAS dengan API key.

Setelah konfigurasi tersedia:

```cmd
python main.py
```

Template bermakro `Exc- List Update Standard Cost All Company Odoo.xlsm`
memuat credential di VBA dan tidak disertakan dalam GitHub. Minta file ini ke
pemilik lewat saluran privat, lalu tempatkan di `build_tools/assets/update_std_cost/`
untuk tombol Open Template dan build installer lengkap. File tersebut diabaikan
Git. Modul Update Standard Cost juga dapat memilih workbook `.xlsx` atau `.xlsm`
yang disiapkan sesuai format Preparation Cost tanpa membuka tombol template.

Tanpa key, GUI dapat dibuka untuk pengembangan lokal, tetapi tindakan yang
memerlukan konfigurasi GAS akan memberi pesan bahwa API key belum diatur.
Gunakan Settings > Database Profiles untuk menentukan database dan pilihan
Follow Global Default pada modul; pastikan database yang efektif sebelum aksi
yang menulis transaksi.

## Memodifikasi bersama AI Agent

Mulai dari `AGENTS.md`, `README.md`, dan dokumentasi fitur yang diubah. Skill
repo tersedia di `.codex/skills/`. Path absolut yang menunjuk komputer pemilik
bukan dependency clone; jangan membuat junction atau menyalin environment lama.

```cmd
git switch -c feature/nama-perubahan
python -m unittest discover -s tests -q
git add nama-file-yang-diubah
git commit -m "Jelaskan perubahan"
git push -u origin feature/nama-perubahan
```

Buka pull request untuk menggabungkan perubahan ke `main`. Untuk perubahan GUI,
jalankan `python main.py` dan verifikasi workflow serta data tampilan yang berubah.
Tests dengan fake clients tidak membuktikan akses atau kesesuaian data Odoo live.

## Isi distribusi

Repo memuat source Python, tests, dokumentasi, script build, ikon,
dan skill lokal. Repo tidak memuat template VBA bercredential, `.venv`, installer hasil build,
workbook operasional, hasil ekspor, log produksi, permission AI lokal, atau
credential GitHub/GAS/Odoo. Source awal tidak mempunyai history Git sebelumnya;
history publik dimulai dari snapshot bersih. History private sebelum sanitasi
tetap disimpan terpisah dan tidak dipush ke repo publik.

GitHub source ini bukan rilis installer. Untuk membuat installer, ikuti bagian
Build Installer Windows di README. Konfigurasi GAS lokal tetap disediakan pada
komputer penerima. Updater memerlukan manifest dan installer GitHub Releases
yang benar; publikasi source ini belum menyediakan rilis installer.
