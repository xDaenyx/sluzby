#!/usr/bin/env python3
"""
extract_shifts.py
=================
Automatická extrakce tabulky směn z PDF souboru do Excelu.

Postup:
  1. Převede každou stránku PDF na obrázek.
  2. Detekuje mřížku tabulky pomocí OpenCV (analýza horizontálních a
     vertikálních čar).
  3. Identifikuje jednotlivé buňky a seřadí je do řádků a sloupců.
  4. Z každé buňky přečte text pomocí pytesseract OCR.
  5. Namapuje obsah buněk na zaměstnance (řádky) a data/sloupce (první řádek).
  6. Výsledek uloží do Excelu.

Nastavení (upravte dle potřeby):
"""

# ---------------------------------------------------------------------------
# NASTAVENÍ – upravte cestu ke svému PDF a výstupnímu souboru
# ---------------------------------------------------------------------------
import os

# Složka s PDF soubory
INPUT_DIR = os.path.join(os.path.dirname(__file__), "input")

# Název vstupního PDF (nebo None pro zpracování všech PDF v INPUT_DIR)
INPUT_PDF = "Souhrnný přehled směn.pdf"

# Cesta k výstupnímu Excelu (vytvoří se vedle tohoto skriptu)
OUTPUT_XLSX = os.path.join(os.path.dirname(__file__), "výsledek_směn.xlsx")

# DPI pro převod PDF → obrázek (vyšší = přesnější OCR, pomalejší zpracování)
PDF_DPI = 300

# Minimální délka čáry jako podíl šířky/výšky obrázku (pro filtraci šumu)
MIN_LINE_RATIO = 0.3

# Minimální velikost buňky v pixelech (filtruje příliš malé oblasti)
MIN_CELL_WIDTH = 20
MIN_CELL_HEIGHT = 15

# Tesseract jazyk – pro češtinu použijte "ces", pro angličtinu "eng"
# Pro oba jazyky najednou: "ces+eng"
TESSERACT_LANG = "ces+eng"

# PSM (Page Segmentation Mode) pro pytesseract při OCR jedné buňky
TESSERACT_PSM = 6  # 6 = Assume a single uniform block of text

# Minimální rozměr (px) výřezu buňky, pod kterým se obrázek před OCR zvětší
MIN_OCR_DIMENSION = 60

# ---------------------------------------------------------------------------
# Konec nastavení
# ---------------------------------------------------------------------------

import sys
import logging
import re
from pathlib import Path
from typing import List, Tuple, Optional

import cv2
import numpy as np
from pdf2image import convert_from_path
import pytesseract
import pandas as pd

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pomocné typy
# ---------------------------------------------------------------------------
Cell = Tuple[int, int, int, int]  # (x, y, w, h)


# ---------------------------------------------------------------------------
# Krok 1 – převod PDF na obrázky
# ---------------------------------------------------------------------------

def pdf_to_images(pdf_path: str, dpi: int = PDF_DPI) -> List[np.ndarray]:
    """Převede všechny stránky PDF na seznam numpy obrázků (BGR)."""
    log.info("Převádím PDF na obrázky (DPI=%d): %s", dpi, pdf_path)
    pil_images = convert_from_path(pdf_path, dpi=dpi)
    images = []
    for pil_img in pil_images:
        img_bgr = cv2.cvtColor(np.array(pil_img), cv2.COLOR_RGB2BGR)
        images.append(img_bgr)
    log.info("Celkem stránek: %d", len(images))
    return images


# ---------------------------------------------------------------------------
# Krok 2 – detekce mřížky a extrakce buněk
# ---------------------------------------------------------------------------

def _extract_lines(binary: np.ndarray, horizontal: bool, min_ratio: float) -> np.ndarray:
    """Vrátí binární mapu obsahující pouze horizontální nebo vertikální čáry."""
    h, w = binary.shape
    if horizontal:
        length = max(1, int(w * min_ratio))
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (length, 1))
    else:
        length = max(1, int(h * min_ratio))
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (1, length))
    lines = cv2.erode(binary, kernel, iterations=1)
    lines = cv2.dilate(lines, kernel, iterations=1)
    return lines


def detect_cells(image: np.ndarray,
                 min_line_ratio: float = MIN_LINE_RATIO,
                 min_cell_w: int = MIN_CELL_WIDTH,
                 min_cell_h: int = MIN_CELL_HEIGHT) -> Tuple[List[Cell], np.ndarray]:
    """
    Detekuje buňky tabulky v obrázku pomocí analýzy čar (OpenCV).

    Vrátí:
        cells  – seznam (x, y, w, h) pro každou detekovanou buňku
        debug  – obrázek s nakreslenými boxy (pro ladění)
    """
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)

    # Invertovaný práh – čáry tabulky jsou tmavé, potřebujeme je jako bílé
    _, binary = cv2.threshold(~gray, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)

    horiz = _extract_lines(binary, horizontal=True,  min_ratio=min_line_ratio)
    vert  = _extract_lines(binary, horizontal=False, min_ratio=min_line_ratio)

    # Kombinace čar
    grid = cv2.add(horiz, vert)

    # Lehká dilatace pro propojení přerušených čar
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
    grid = cv2.dilate(grid, kernel, iterations=1)

    # Hledání kontur (buňky = uzavřené obdélníkové oblasti uvnitř mřížky)
    contours, _ = cv2.findContours(grid, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE)

    cells: List[Cell] = []
    for cnt in contours:
        x, y, w, h = cv2.boundingRect(cnt)
        if w < min_cell_w or h < min_cell_h:
            continue
        # Vyloučíme příliš velké oblasti (celá stránka / celá tabulka)
        if w > image.shape[1] * 0.95 or h > image.shape[0] * 0.95:
            continue
        cells.append((x, y, w, h))

    log.info("Detekováno %d kandidátních buněk (před deduplikací)", len(cells))
    cells = _deduplicate_cells(cells)
    log.info("Buněk po deduplikaci: %d", len(cells))

    debug = image.copy()
    for (x, y, w, h) in cells:
        cv2.rectangle(debug, (x, y), (x + w, y + h), (0, 255, 0), 2)

    return cells, debug


def _deduplicate_cells(cells: List[Cell], overlap_thresh: float = 0.7) -> List[Cell]:
    """Odstraní duplicitní nebo silně se překrývající buňky (ponechá větší)."""
    if not cells:
        return cells
    cells = sorted(cells, key=lambda c: c[2] * c[3], reverse=True)  # větší první
    kept: List[Cell] = []
    for cell in cells:
        x1, y1, w1, h1 = cell
        duplicate = False
        for kx, ky, kw, kh in kept:
            # Průnik
            ix = max(0, min(x1 + w1, kx + kw) - max(x1, kx))
            iy = max(0, min(y1 + h1, ky + kh) - max(y1, ky))
            inter = ix * iy
            area1 = w1 * h1
            if area1 > 0 and inter / area1 > overlap_thresh:
                duplicate = True
                break
        if not duplicate:
            kept.append(cell)
    return kept


# ---------------------------------------------------------------------------
# Krok 3 – organizace buněk do mřížky (řádky × sloupce)
# ---------------------------------------------------------------------------

def _cluster(values: List[int], gap: int) -> List[int]:
    """
    Seskupí blízké hodnoty do skupin a vrátí reprezentativní hodnotu
    (medián) pro každou skupinu. Používá se pro identifikaci řádků/sloupců.
    """
    if not values:
        return []
    sorted_vals = sorted(values)
    clusters: List[List[int]] = [[sorted_vals[0]]]
    for v in sorted_vals[1:]:
        if v - clusters[-1][-1] <= gap:
            clusters[-1].append(v)
        else:
            clusters.append([v])
    return [int(np.median(c)) for c in clusters]


def organize_cells_into_grid(
    cells: List[Cell],
    row_gap: int = 10,
    col_gap: int = 10,
) -> Tuple[List[int], List[int], dict]:
    """
    Seřadí buňky do logické mřížky.

    Vrátí:
        row_centers – seznam Y středů řádků (seřazeno)
        col_centers – seznam X středů sloupců (seřazeno)
        grid_dict   – {(row_idx, col_idx): Cell}
    """
    if not cells:
        return [], [], {}

    # Středy buněk
    centers = [(x + w // 2, y + h // 2) for (x, y, w, h) in cells]
    xs = [c[0] for c in centers]
    ys = [c[1] for c in centers]

    # Průměrná výška buňky – dynamický gap
    avg_h = int(np.median([h for (_, _, _, h) in cells]))
    avg_w = int(np.median([w for (_, _, w, _) in cells]))
    row_gap = max(row_gap, avg_h // 3)
    col_gap = max(col_gap, avg_w // 3)

    row_centers = _cluster(ys, gap=row_gap)
    col_centers = _cluster(xs, gap=col_gap)

    def nearest(val: int, centers_list: List[int]) -> int:
        return int(np.argmin([abs(val - c) for c in centers_list]))

    grid_dict: dict = {}
    for cell, (cx, cy) in zip(cells, centers):
        ri = nearest(cy, row_centers)
        ci = nearest(cx, col_centers)
        # Pokud na pozici již buňka je, ponechte větší
        if (ri, ci) not in grid_dict:
            grid_dict[(ri, ci)] = cell
        else:
            existing = grid_dict[(ri, ci)]
            if cell[2] * cell[3] > existing[2] * existing[3]:
                grid_dict[(ri, ci)] = cell

    return row_centers, col_centers, grid_dict


# ---------------------------------------------------------------------------
# Krok 4 – OCR každé buňky
# ---------------------------------------------------------------------------

def ocr_cell(image: np.ndarray, cell: Cell, lang: str = TESSERACT_LANG,
             psm: int = TESSERACT_PSM, padding: int = 4) -> str:
    """
    Přečte text z výřezu buňky pomocí pytesseract.

    Parametry:
        padding – počet pixelů, o které se výřez rozšíří na všech stranách
                  (pomáhá, když jsou čáry mřížky přímo na okraji textu)
    """
    x, y, w, h = cell
    h_img, w_img = image.shape[:2]
    x1 = max(0, x + padding)
    y1 = max(0, y + padding)
    x2 = min(w_img, x + w - padding)
    y2 = min(h_img, y + h - padding)
    roi = image[y1:y2, x1:x2]
    if roi.size == 0:
        return ""

    # Předzpracování pro lepší OCR
    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    # Zvětšení pro lepší rozpoznání malého textu
    scale = max(1, int(MIN_OCR_DIMENSION / min(roi.shape[:2]))) if min(roi.shape[:2]) < MIN_OCR_DIMENSION else 1
    if scale > 1:
        gray = cv2.resize(gray, None, fx=scale, fy=scale,
                          interpolation=cv2.INTER_CUBIC)
    # Binarizace
    _, gray = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)

    config = f"--psm {psm} -l {lang}"
    text = pytesseract.image_to_string(gray, config=config)
    return text.strip()


def ocr_grid(image: np.ndarray,
             grid_dict: dict,
             n_rows: int,
             n_cols: int) -> List[List[str]]:
    """Přečte text ze všech buněk mřížky a vrátí 2D seznam řetězců."""
    table: List[List[str]] = [[""] * n_cols for _ in range(n_rows)]
    total = len(grid_dict)
    for idx, ((ri, ci), cell) in enumerate(grid_dict.items()):
        if idx % 20 == 0:
            log.info("  OCR buňka %d/%d …", idx + 1, total)
        text = ocr_cell(image, cell)
        table[ri][ci] = text
    return table


# ---------------------------------------------------------------------------
# Krok 5 – prostorové mapování (spatial mapping)
# ---------------------------------------------------------------------------

def _clean(text: str) -> str:
    """Odstraní přebytečné bílé znaky a speciální znaky."""
    return re.sub(r"\s+", " ", text).strip()


def map_to_dataframe(raw_table: List[List[str]]) -> pd.DataFrame:
    """
    Namapuje 2D tabulku na strukturovaný DataFrame.

    Předpoklady o struktuře tabulky:
      - Řádek 0: záhlaví sloupců (data nebo jiné identifikátory)
      - Sloupec 0: jméno zaměstnance
      - Zbytek: hodnoty buněk (typ směny apod.)

    Pokud struktura neodpovídá, vrátí tabulku tak jak je.
    """
    if not raw_table or not raw_table[0]:
        return pd.DataFrame()

    # Záhlaví = první řádek
    headers = [_clean(h) for h in raw_table[0]]

    rows = []
    for row in raw_table[1:]:
        row_data = [_clean(cell) for cell in row]
        if any(row_data):  # přeskočit zcela prázdné řádky
            rows.append(row_data)

    if not rows:
        return pd.DataFrame(columns=headers)

    # Srovnání počtu sloupců
    max_cols = max(len(headers), max(len(r) for r in rows))
    headers += [""] * (max_cols - len(headers))
    padded_rows = [r + [""] * (max_cols - len(r)) for r in rows]

    df = pd.DataFrame(padded_rows, columns=headers)

    # Pokud první sloupec vypadá jako jména zaměstnanců, nastavíme jako index
    first_col = headers[0] if headers else ""
    if first_col == "" or first_col.lower() in ("jméno", "zaměstnanec", "sestra",
                                                  "jmeno", "name", "pracovník"):
        df = df.set_index(df.columns[0])
        df.index.name = "Zaměstnanec"

    return df


# ---------------------------------------------------------------------------
# Krok 6 – uložení do Excelu
# ---------------------------------------------------------------------------

def _autofit_columns(ws) -> None:
    """Nastaví automatickou šířku sloupců v listu openpyxl."""
    for col_cells in ws.columns:
        max_len = 0
        col_letter = col_cells[0].column_letter
        for cell in col_cells:
            try:
                cell_len = len(str(cell.value)) if cell.value else 0
                max_len = max(max_len, cell_len)
            except Exception:
                pass
        ws.column_dimensions[col_letter].width = min(max_len + 4, 40)


def save_to_excel(df: pd.DataFrame, output_path: str,
                  sheet_name: str = "Směny") -> None:
    """Uloží DataFrame do Excelu s jednoduchým formátováním."""
    log.info("Ukládám výsledek do: %s", output_path)
    with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name=sheet_name)
        _autofit_columns(writer.sheets[sheet_name])

    log.info("Hotovo! Excel uložen: %s", output_path)


# ---------------------------------------------------------------------------
# Hlavní funkce
# ---------------------------------------------------------------------------

def process_pdf(pdf_path: str,
                output_path: str = OUTPUT_XLSX,
                dpi: int = PDF_DPI,
                save_debug: bool = False) -> Optional[pd.DataFrame]:
    """
    Kompletní zpracování jednoho PDF souboru.

    Parametry:
        pdf_path    – cesta k PDF souboru
        output_path – cesta k výstupnímu Excelu
        dpi         – rozlišení pro převod PDF na obrázek
        save_debug  – pokud True, uloží obrázky s detekovanými buňkami
                      vedle výstupního Excelu (pro ladění)

    Vrátí DataFrame nebo None při chybě.
    """
    if not os.path.exists(pdf_path):
        log.error("Soubor nenalezen: %s", pdf_path)
        return None

    images = pdf_to_images(pdf_path, dpi=dpi)
    all_frames: List[pd.DataFrame] = []

    for page_num, image in enumerate(images, start=1):
        log.info("=== Stránka %d/%d ===", page_num, len(images))

        cells, debug_img = detect_cells(image)
        if not cells:
            log.warning("Na stránce %d nebyly nalezeny žádné buňky tabulky.", page_num)
            continue

        if save_debug:
            debug_path = str(Path(output_path).with_suffix("")) + f"_debug_p{page_num}.png"
            cv2.imwrite(debug_path, debug_img)
            log.info("Debug obrázek uložen: %s", debug_path)

        row_centers, col_centers, grid_dict = organize_cells_into_grid(cells)
        n_rows = len(row_centers)
        n_cols = len(col_centers)
        log.info("Tabulka: %d řádků × %d sloupců", n_rows, n_cols)

        if n_rows < 2 or n_cols < 2:
            log.warning("Stránka %d: mřížka má méně než 2 řádky nebo sloupce, přeskakuji.", page_num)
            continue

        raw_table = ocr_grid(image, grid_dict, n_rows, n_cols)
        df = map_to_dataframe(raw_table)
        if not df.empty:
            all_frames.append(df)

    if not all_frames:
        log.error("Z PDF se nepodařilo extrahovat žádná data.")
        return None

    # Více stránek – přidáme list pro každou stránku
    with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
        for i, df in enumerate(all_frames, start=1):
            sheet = f"Strana_{i}" if len(all_frames) > 1 else "Směny"
            df.to_excel(writer, sheet_name=sheet)
            _autofit_columns(writer.sheets[sheet])

    log.info("Hotovo! Výsledek uložen: %s", output_path)
    return all_frames[0] if len(all_frames) == 1 else all_frames[0]


# ---------------------------------------------------------------------------
# Spuštění skriptu
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # Pokud byl předán argument z příkazové řádky, použijeme ho jako cestu k PDF
    if len(sys.argv) > 1:
        pdf_file = sys.argv[1]
    else:
        pdf_file = os.path.join(INPUT_DIR, INPUT_PDF)

    # Volitelný druhý argument: výstupní soubor Excel
    if len(sys.argv) > 2:
        out_file = sys.argv[2]
    else:
        out_file = OUTPUT_XLSX

    log.info("Vstupní PDF : %s", pdf_file)
    log.info("Výstupní XLS: %s", out_file)

    result = process_pdf(pdf_file, output_path=out_file, save_debug=False)

    if result is None:
        log.error("Zpracování selhalo.")
        sys.exit(1)

    log.info("Extrakce úspěšně dokončena.")
    print("\nNáhled prvních 5 řádků výsledné tabulky:")
    print(result.head())
