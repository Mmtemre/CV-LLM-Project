# CV-LLM-Project

PC  bozuk olduğuu için arkadaşın pc den yaptımm isimller farklı oldu.
# CV Standardizasyon Projesi Raporu

## 1. Projenin Amacı ve Kapsamı

Bu projenin amacı, farklı düzenlerde hazırlanmış (özellikle çok sütunlu / sidebar’lı) CV PDF dosyalarını **tek bir standart veri modeline** dönüştürmek ve ardından **profesyonel bir şablon** ile tekrar PDF olarak üretmektir.

Kapsam olarak proje üç aşamalı bir “standardization pipeline” uygular:

1. **Uzamsal çıkarım (PDF → Markdown):** CV’nin okuma sırasını/kolonlarını koruyarak metni çıkarır.
2. **Anlamsal yapılandırma (Markdown → JSON):** Çıkarılan metni standart bir şemaya dönüştürür.
3. **Profesyonel render (JSON/YAML → PDF):** Standart veriyi endüstri standardı CV şablonlarına basar.

Bu yaklaşım, “kötü yerleşimli PDF → iyi görünümlü PDF” dönüşümünü **veri odaklı** hale getirerek ölçeklenebilir kılar.

## 2. Problem Tanımı

Standart PDF metin çıkarım kütüphaneleri (örn. basit text extraction) CV’lerde sıkça şu hatayı yapar:

- Çok sütunlu tasarımlarda soldaki “Skills” gibi blokları sağdaki “Experience” ile **satır bazında karıştırır**.
- Başlıklar, madde imleri ve alt başlıklar bozulur; okuma sırası kayar.

Bu nedenle proje, kolon algılama ve okuma sırası tespiti yapan bir araçla (Marker) başladığı için daha güvenilir bir metin temeli elde eder.

## 3. Çözüm Mimarisi

Projenin çekirdeği tek bir CLI script’idir:

- `standardize_cv.py`: Marker → Ollama → RenderCV zincirini çalıştırır.

### 3.1 Aşama 1 – Marker ile PDF → Markdown

Araç: `marker-pdf` (CLI: `marker_single`)

Çıktı olarak Marker; markdown, bloklar (JSON) ve sayfa görselleri üretebilir. Bu repo, `--output_format markdown` ile markdown çıktısını kullanır.

Not: Marker ilk çalıştırmada büyük modeller indirebilir (OCR/layout). Bu normaldir ve sonraki çalıştırmalar hızlanır.

### 3.2 Aşama 2 – Ollama (qwen3:8b) ile Markdown → JSON

Araç: Ollama HTTP API (`/api/chat`)

Yaklaşım:

- Markerdan gelen Markdown, sıkı bir “system prompt” ile **JSON objesine** dönüştürülür.
- Tarihler mümkün olduğunda `YYYY-MM` formatına normalize edilir.
- Çıktı saf JSON olmalıdır; script parse eder ve `resume_data.json` olarak yazar.

Bu aşama yerel çalıştırıldığında veri (CV) dışarıya çıkmadan işlenebilir (gizlilik avantajı).

### 3.3 Aşama 3 – RenderCV ile JSON/YAML → PDF

Araç: RenderCV

Yaklaşım:

- Aşama 2’de üretilen JSON, RenderCV’nin beklediği YAML yapısına dönüştürülür.
- `rendercv render resume_data.rendercv.yaml` ile PDF çıktısı alınır.

## 4. Girdi/Çıktı Sözleşmeleri

### 4.1 Girdi

- PDF CV dosyası (tek dosya)

### 4.2 Çıktılar

Çalışma klasörü (varsayılan: `cv_out/`) içinde:

- `resume.md`: Marker çıktısı (seçilen en iyi markdown dosyasının kopyası)
- `resume_data.json`: Ollama tarafından yapılandırılmış veri
- `resume_data.rendercv.yaml`: RenderCV uyumlu YAML
- RenderCV’nin oluşturduğu PDF(ler) (dosya adları RenderCV sürümüne/temasına göre değişebilir)

Marker ara çıktıları da `marker_out/` altında tutulur.

## 5. Proje Dosya Yapısı (Özet)

- `standardize_cv.py`: Uçtan uca pipeline
- `requirements.txt`: Python bağımlılıkları
- `README_cv_pipeline.md`: Kullanım yönergesi
- `cv_out_emirhan/marker_out/...`: Örnek bir çalıştırmada Marker çıktıları

## 6. Kurulum ve Çalıştırma

### 6.1 Python bağımlılıkları

PowerShell:

```powershell
python -m pip install -r requirements.txt
```

Repo içindeki venv ile çalıştırmak için:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

### 6.2 Ollama kurulumu

Bu ortamda `ollama` komutu PATH üzerinde görünmüyordu ve `http://localhost:11434` portu erişilemez durumdaydı. Bu nedenle Aşama 2 (LLM) çalışması için Ollama’nın kurulup servisinin başlatılması gerekir.

Genel olarak (kurulumdan sonra):

```powershell
ollama serve
ollama pull qwen3:8b
```

### 6.3 Pipeline çalıştırma

```powershell
.\.venv\Scripts\python.exe .\standardize_cv.py path\to\cv.pdf --work-dir .\cv_out
```

Sadece ara çıktıları almak (RenderCV atlamak) için:

```powershell
.\.venv\Scripts\python.exe .\standardize_cv.py path\to\cv.pdf --work-dir .\cv_out --skip-render
```

## 7. Uygulama Notları ve Gözlemler

1. **Marker CLI uyumluluğu:** Bazı kaynaklarda geçen `--batch_multiplier` flag’i mevcut Marker sürümünde yok. Bu projede çağrı `--output_dir`, `--output_format markdown` ve best-effort `--layout_batch_size` ile uyumlu hale getirildi.
2. **Eksik bağımlılık:** Marker/Surya/Transformers zinciri için `protobuf` gerekli çıktı; `requirements.txt` içine eklendi.
3. **Windows/venv çalıştırma:** `marker_single` ve `rendercv` komutları PATH’te olmayabilir. Script, venv içindeki `.venv\Scripts` dizininden executable çözümlemeyi dener.
4. **Model indirme:** Marker ilk çalıştırmada büyük model indirebilir; bu süre uzundur ama tek seferliktir.

## 8. Sınırlamalar

- **Aşama 2 bağımlılığı:** Ollama kurulu ve çalışır değilse pipeline Aşama 1’de kalır.
- **Şema basitliği:** JSON şeması (name/contact/experience/education/skills) minimal tutuldu; ek alanlar (projeler, sertifikalar, diller vb.) eklenmek istenirse prompt + dönüştürücü genişletilmeli.
- **Veri doğruluğu:** LLM tabanlı çıkarımda hatalar olabilir; özellikle tarih aralıkları ve çok sütunlu hizalamalarda manuel kontrol önerilir.

## 9. Gelecek İyileştirmeler (Öneri)

- CV şemasını RenderCV’nin daha geniş alanlarını kapsayacak şekilde genişletmek.
- Ollama çıktısı için JSON Schema doğrulaması ekleyerek hataları erken yakalamak.
- Çok sayıda CV için batch işleme ve hata raporlama.

---

Bu rapor, repo içindeki kod ve mevcut çalışma çıktıları temel alınarak hazırlanmıştır.

