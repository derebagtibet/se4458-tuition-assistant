# Yol Haritası: Teslime kadar yapılacaklar

21 Kasım 2026 kontrolü: temel mimari mevcut ve yerel PostgreSQL / API / Kong / MCP sistemi çalışıyor. Teslim henüz tamamlanmadı.

| Durum | İş |
|---|---|
| Doğrulandı | Gerçek gateway üzerinden JWT 401/200, başka hesap için 403, öğrenci bazlı 3/gün sınırı ve 429; Swagger, ödeme/duplicate, CSV ve sayfalama |
| Doğrulandı | Gerçek MCP protokolü üzerinden üç farklı politika sorgusu |
| Test kapsamında | 82 otomatik test: API 42, MCP 11, agent 29; gerçek LLM kullanılmıyor |
| Çözüldü | Ödeme sonrası gerçek Gemini isteği başarılı; çalışmayan model adı yerine `gemini-3.8-flash` ayarlandı |
| Doğrulandı | Gerçek Gemini ile üç canlı agent senaryosu: MCP-only, REST-only ve birleşik; log ve JSON trace dosyaları kaydedildi |
| Eklendi | LLM maliyeti: ölçülen token sayıları ve USD hesabı README §7'de (3 senaryo toplam ≈ 0,009 USD) |
| Doğrulandı | Öğrenci onaylı agent ödemesi: `yes` → Kong üzerinden Pay API → `SUCCESS`, 2026-FALL 3000 → 2500 USD (`docs/evidence/agent-payment-confirmed.log`) |
| Doğrulandı | Azure deploy: Kong, MCP, Tuition API (internal) Sweden Central'da, PostgreSQL West Europe'ta. URL'ler README §9'da. Bulutta gateway demo PASSED, 3 MCP sorgusu ve agent demosu (onaylı ödeme dahil) `docs/evidence/cloud-*.log` içinde |
| Eksik | Kısa video ve bağlantısı, GitHub repo yayını |

Bu kontrolde CSV'deki NaN/Infinity ve kolon/sözdizimi hataları, agent'ın ödeme tutarını sessizce yuvarlaması, geçersiz dönemle ödeme denemesi, duplicate işlem hatasında başka öğrenci bilgisinin açığa çıkması ve Windows UTF-8/geçici klasör sorunları düzeltildi. Yerel `.env` içindeki örnek JWT sırrı rastgele bir değerle değiştirildi. CLI `.env` ve `MCP_HOST_PORT` ayarlarını okuyor; sağlayıcı hatalarını anlaşılır mesajla gösteriyor. Güncel kanıtlar `docs/evidence/` içinde.

## 1. Lokalde çalıştır (≈15 dk)
1. Docker Desktop bu bilgisayarda mevcut; yerel stack çalışır durumda.
2. Kullanılabilir kotası olan Gemini anahtarı ve başarılı `gemini-3.8-flash` modeli yerel `.env` içine ayarlandı. Önceki 402 hatası ödeme sonrası çözüldü. Anahtar kanıtlara eklenmedi.
3. Repo kökünde şunları çalıştır:
   ```bash
   # Yalnızca .env henüz yoksa .env.example dosyasından oluştur.
   # .env içine şunları yaz:
   #   JWT_SECRET=$(openssl rand -hex 32) çıktısı
   #   GEMINI_API_KEY=...
   docker compose up --build -d
   python scripts/gateway_demo.py        # log otomatik docs/evidence/gateway-demo.log içine yazılır
   ```
4. Tarayıcıda http://localhost:8000/api/docs adresini aç. Swagger UI'ı gateway üzerinden görmelisin.
5. Agent'ı çalıştır:
   ```bash
   cd agent
   pip install -r requirements.txt
   python -m tuition_agent.cli --username s1001 --trace-dir ../docs/evidence/agent --demo
   # Şifre: Passw0rd! ; .env otomatik okunur.
   ```
   Gösterilen ödeme özetini kontrol edip `yes` yaz. Trace dosyaları `docs/evidence/agent/` klasörüne yazılır. Üç okuma senaryosu tamamlandı; öğrenci onaylı ödeme adımı hâlâ kaydedilmeli.
5a. **Onaylı ödeme kanıtını kaydet** (stack çalışırken, `agent/` klasöründe, PowerShell):
   ```powershell
   $env:PYTHONIOENCODING="utf-8"
   python -u -m tuition_agent.cli --username s1001 -q "Pay 500 dollars of my 2026-FALL balance." --trace-dir ../docs/evidence/agent 2>&1 | Tee-Object -FilePath ../docs/evidence/agent-payment-confirmed.log
   ```
   Şifreyi (`Passw0rd!`) gir, özette 2026-FALL bakiyesinin 3000 → 2500 USD olacağını gör ve `yes` yaz. Logda `CONFIRMATION confirmed=true`, `pay_tuition` çağrısı ve `SUCCESS` görünmeli. Ardından README §12'deki "Confirmed simulated payment" kutusunu işaretle ve `docs/evidence/README.md` tablosuna bu logu ekle. Bu adımı videoda da göster.
6. MCP üzerinden 3 retrieval sorgusunu göster: `python scripts/mcp_demo.py`
7. Testleri çalıştır: `python scripts/run_tests.py` (82 test; log otomatik kaydedilir).

> **Windows PowerShell:** `.venv/Scripts/Activate.ps1` ile ortamı etkinleştir; ardından Python demo/test komutlarını doğrudan çalıştır. Bu bilgisayarda `python` kısayolu çalışmıyorsa etkinleştirme öncesinde `.venv/Scripts/python.exe` kullan. `.sh` dosyaları için Git Bash veya WSL gerekir. Güncel yerel MCP adresi `.env` ayarına göre `http://localhost:8002/mcp`.

> Bu hesapta `gemini-flash-latest` bulunamadı, `gemini-2.5-flash` ise yeni kullanıcılara kapalı yanıtı verdi. Sağlayıcının önerdiği `gemini-3.8-flash` ile gerçek çağrı ve üç agent senaryosu başarılı oldu.

## 2. Azure'a deploy et (≈30 dk, çoğu bekleme)
1. Azure for Students hesabını aktifleştir ve Azure CLI'ı kur.
2. `az login` ile giriş yap, sonra `bash deploy/azure/deploy.sh` çalıştır.
   Bölge izni hatası alırsan şöyle dene: `LOCATION=northeurope bash deploy/azure/deploy.sh`
3. Çıktıdaki **Gateway URL / Swagger / MCP URL** değerlerini README §9'a yaz.
4. `GATEWAY_URL=https://... bash scripts/gateway_demo.sh`, `python scripts/mcp_demo.py https://<mcp-fqdn>/mcp` ve agent `--demo` komutlarını cloud URL'leriyle tekrar çalıştır.
5. Not verildikten sonra kaynakları sil: `az group delete -n se4458-tuition-rg --yes`

## 3. Kanıt ve video
`docs/evidence/` klasörüne şunların ekran görüntüsünü veya logunu koy:
- 3 agent senaryosu (MCP-only, REST-only, combined) ve onaylı ödeme
- `pytest` çıktısı (MCP retrieval testleri dahil)
- Gateway'de korumalı route (tokensız 401, tokenlı 200) ve 4. mobil sorguda 429
- Swagger UI

README'de ad ve öğrenci numarası zaten doldurulmuş. `agent-mcp-only.log`, `agent-rest-only.log`, `agent-combined.log` ve `agent/` altındaki JSON dosyaları başarılı canlı kanıtlardır. `agent-live-attempt.log` eski 402 denemesidir; sorun çözüldü. Gateway demosundaki doğrudan API ödemesi, öğrenci onaylı agent demosu yerine geçmez.

Kısa bir video çek: mimari diyagramı → gateway demo → agent demo → testlerin çalışması. README §12'deki checklist'i işaretle.

## 4. GitHub
Bu klasörde henüz `.git` bulunmuyor. Yeni bir repo aç, push et ve repo linkini hocanın istediği yöntemle teslim et. `.env` dosyasını **commit etme** (`.gitignore` içinde zaten var). Gerçek anahtarları, JWT'leri ve `deploy/azure/.deploy-state` dosyasını repoya ekleme.

## Videoda anlatman gerekenler (sorulabilir)
- **LLM tool seçimi** keyword routing ile değil, Gemini function calling ile yapılıyor (`agent.py` → `TOOLS`). Araçları agent çalıştırıyor, çünkü `automatic_function_calling` kapalı.
- **Başka öğrencinin verisi alınamaz.** Agent'ın araçlarında `studentNo` parametresi yok. Login olunan JWT kullanılıyor ve API'deki `ensure_can_access_student` başka öğrenci için 403 döndürüyor.
- **Ödeme onayı kodda zorunlu.** `_pay()` önce bakiyeyi okuyor, özeti gösteriyor ve `yes` gelmeden API'yi çağırmıyor. `transactionId` idempotency key olarak kullanılıyor ve tekrar eden istek 409 alıyor.
- **3 sorgu/gün kuralı** IP'ye göre değil, path'teki öğrenci numarası ve gün anahtarıyla Kong'da sayılıyor. Aynı kural API'de de var (defense in depth).
- **İndeks bir kez kuruluyor.** Docker build sırasında BM25 indeksi oluşturuluyor (`policy_index.json`) ve sorgu başına yeniden indekslenmiyor.
