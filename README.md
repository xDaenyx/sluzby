# MediShift AI Pro — KJ 2026

Desktopová aplikace pro správu a generování rozpisů směn. Zabalena pomocí [Tauri](https://tauri.app) — výsledkem je nativní `.exe` (Windows) nebo `.AppImage` (Linux).

## Rychlý start (spuštění v prohlížeči)

Otevřete soubor `index.html` přímo v prohlížeči — vše funguje bez instalace.

## Sestavení desktopové aplikace (.exe)

### Požadavky

- [Node.js](https://nodejs.org) ≥ 18
- [Rust](https://rustup.rs) (stable)
- Windows: automaticky nainstaluje WebView2 (součást Windows 10/11)
- Linux: `libwebkit2gtk-4.1-dev` a další (viz níže)

### Windows

```bash
npm install
npm run tauri build
```

Instalátor bude v `src-tauri/target/release/bundle/nsis/*.exe`.

### Linux

```bash
sudo apt-get install libwebkit2gtk-4.1-dev build-essential libssl-dev
npm install
npm run tauri build
```

AppImage bude v `src-tauri/target/release/bundle/appimage/*.AppImage`.

## CI/CD — GitHub Actions

Pushnutí tagu ve formátu `v*` (např. `v1.0.0`) spustí automatické sestavení pro Windows a Linux. Artefakty jsou ke stažení v záložce **Actions** na GitHubu.

```bash
git tag v1.0.0
git push origin v1.0.0
```

## Struktura projektu

```
index.html          # Hlavní frontend (HTML/CSS/JS — vše v jednom souboru)
src-tauri/
  tauri.conf.json   # Konfigurace okna a bundlu
  Cargo.toml        # Rust závislosti
  src/
    main.rs         # Vstupní bod aplikace
    lib.rs          # Rust příkazy (čtení souborů)
  capabilities/
    default.json    # Tauri v2 oprávnění
.github/workflows/
  build.yml         # CI/CD pro sestavení .exe a .AppImage
```