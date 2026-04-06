use tauri_plugin_dialog::DialogExt;

pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_fs::init())
        .invoke_handler(tauri::generate_handler![read_file_bytes])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}

/// Přečte soubor z disku a vrátí jeho obsah jako base64 string.
/// Volá se z JS: invoke('read_file_bytes', { path }) → string (base64)
#[tauri::command]
fn read_file_bytes(path: String) -> Result<String, String> {
    use base64::Engine;
    let data = std::fs::read(&path).map_err(|e| e.to_string())?;
    Ok(base64::engine::general_purpose::STANDARD.encode(data))
}
