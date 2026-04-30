# Extrakce směn z PDF do Excelu

Tento skript automaticky přečte PDF s tabulkou rozpisu směn, detekuje mřížku tabulky, rozpozná text v jednotlivých buňkách pomocí OCR a výsledek uloží do přehledného Excelu.

---

## Požadavky

- **Python 3.9+**
- **Tesseract OCR** nainstalovaný v systému
- **Poppler** (pro převod PDF na obrázky pomocí `pdf2image`)

### Instalace systémových závislostí

**Ubuntu / Debian:**
```bash
sudo apt-get update
sudo apt-get install -y tesseract-ocr tesseract-ocr-ces poppler-utils
```

**macOS (Homebrew):**
```bash
brew install tesseract tesseract-lang poppler
```

**Windows:**
- Stáhněte a nainstalujte [Tesseract for Windows](https://github.com/UB-Mannheim/tesseract/wiki).
- Stáhněte a nainstalujte [Poppler for Windows](http://blog.alivate.com.au/poppler-windows/).
- Přidejte obě složky `bin` do proměnné prostředí `PATH`.

---

## Instalace Python závislostí

```bash
cd shift_extractor
pip install -r requirements.txt
```

---

## Použití

### 1. Nahrajte PDF do složky `input/`

Zkopírujte svůj soubor (např. `Souhrnný přehled směn.pdf`) do složky:

```
shift_extractor/input/
```

### 2. Spusťte skript

```bash
python extract_shifts.py
```

Skript automaticky:
1. Načte PDF ze složky `input/` (název je nastaven v proměnné `INPUT_PDF` na začátku skriptu).
2. Detekuje tabulku pomocí analýzy čar (OpenCV).
3. Přečte text každé buňky pomocí OCR (pytesseract).
4. Namapuje buňky na zaměstnance a data.
5. Uloží výsledek do `výsledek_směn.xlsx` vedle skriptu.

### 3. Alternativní spuštění s vlastními cestami

```bash
python extract_shifts.py "cesta/ke/svemu/souboru.pdf" "cesta/k/vystupu.xlsx"
```

---

## Nastavení

Na začátku souboru `extract_shifts.py` jsou tyto proměnné, které lze upravit:

| Proměnná | Výchozí hodnota | Popis |
|---|---|---|
| `INPUT_DIR` | `./input` | Složka se vstupními PDF |
| `INPUT_PDF` | `Souhrnný přehled směn.pdf` | Název PDF souboru |
| `OUTPUT_XLSX` | `./výsledek_směn.xlsx` | Cesta k výstupnímu Excelu |
| `PDF_DPI` | `300` | Rozlišení převodu PDF→obrázek (vyšší = přesnější, pomalejší) |
| `MIN_LINE_RATIO` | `0.3` | Minimální délka čáry jako podíl šířky/výšky (pro filtraci šumu) |
| `MIN_CELL_WIDTH` | `20` | Minimální šířka buňky v pixelech |
| `MIN_CELL_HEIGHT` | `15` | Minimální výška buňky v pixelech |
| `TESSERACT_LANG` | `ces+eng` | Jazyk OCR (`ces` = čeština, `eng` = angličtina) |
| `TESSERACT_PSM` | `6` | Tesseract PSM mód pro jednotlivé buňky |

---

## Jak skript funguje

```
PDF soubor
   │
   ▼  (pdf2image)
Obrázek stránky
   │
   ▼  (OpenCV – detekce horizontálních a vertikálních čar)
Mřížka tabulky
   │
   ▼  (OpenCV – konturní analýza)
Souřadnice buněk (x, y, šířka, výška)
   │
   ▼  (pytesseract OCR)
Text v každé buňce
   │
   ▼  (prostorové mapování – 1. řádek = sloupce, 1. sloupec = zaměstnanci)
Strukturovaná tabulka (DataFrame)
   │
   ▼  (openpyxl)
Excel soubor (výsledek_směn.xlsx)
```

---

## Ladění (debug)

Pokud skript nedetekuje tabulku správně, spusťte `process_pdf()` s parametrem `save_debug=True`:

```python
from extract_shifts import process_pdf
process_pdf("input/Souhrnný přehled směn.pdf", save_debug=True)
```

Vedle Excelu se pak uloží PNG obrázky s vizualizací detekovaných buněk.

Případně upravte hodnoty `MIN_LINE_RATIO`, `MIN_CELL_WIDTH`, `MIN_CELL_HEIGHT` a `PDF_DPI` dle potřeby.

---

## Struktura výsledného Excelu

| Zaměstnanec | 1.1.2025 | 2.1.2025 | 3.1.2025 | … |
|---|---|---|---|---|
| Novák Jan | CD | N | | … |
| Svobodová Eva | | CD | CD | … |

- **První sloupec**: jméno zaměstnance (převzato z prvního sloupce tabulky v PDF)
- **Záhlaví**: data nebo jiné identifikátory (převzato z prvního řádku tabulky v PDF)
- **Buňky**: typ směny (CD, N, apod.) přesně z PDF
