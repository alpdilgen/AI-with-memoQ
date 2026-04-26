# memoQ AI Translator

AI destekli çeviri aracı. memoQ Server'a bağlanarak TM ve TB verilerini kullanır, OpenAI modelleriyle çeviri üretir ve sonucu memoQ'ya uyumlu XLIFF olarak teslim eder.

> **Beta:** Şu an iç test aşamasındadır.

---

## Ne yapar?

1. **XLIFF yükle** — memoQ'dan export edilen `.mqxliff` dosyasını yükle
2. **memoQ Server'a bağlan** — TM ve TB seç
3. **Çeviriyi başlat** — GPT-4o veya GPT-4o-mini ile segment bazlı çeviri
4. **İndir** — memoQ metadata'sıyla (match score, status) dolu XLIFF'i al, memoQ'ya import et

---

## Gereksinimler

- OpenAI API key
- memoQ Server erişimi (URL, kullanıcı adı, şifre)
- memoQ'dan export edilmiş `.mqxliff` dosyası

---

## Ayarlar

| Parametre | Açıklama |
|-----------|----------|
| Model | `gpt-4o` (kaliteli) veya `gpt-4o-mini` (hızlı/ucuz) |
| Kabul eşiği | Bu oran ve üzerindeki TM eşleşmeleri çevrilmez, doğrudan alınır (varsayılan: %95) |
| TM eşleşme eşiği | TM bağlamı için minimum benzerlik oranı (varsayılan: %70) |

---

## memoQ'ya import

İndirilen XLIFF'i memoQ'da **Import > Import with options** ile aç.  
Segmentler `mq:status` ve `mq:percent` değerleriyle gelir — TM eşleşmeleri `ManuallyConfirmed`, AI çevirileri `PartiallyEdited` olarak işaretlenir.
