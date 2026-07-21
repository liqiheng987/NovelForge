use base64::{engine::general_purpose::STANDARD as BASE64, Engine as _};
use rusqlite::{params, Connection, OptionalExtension};
use serde::Serialize;
use std::env;
use std::fs;
use std::path::{Path, PathBuf};
use std::process::Command;
use tauri::AppHandle;
use tauri_plugin_dialog::DialogExt;
use tauri_plugin_store::StoreExt;

const SUPPORTED_EXTENSIONS: [&str; 4] = ["docx", "pdf", "txt", "epub"];
const API_PROFILES_KEY: &str = "api_profiles.v1";

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct FileInfo {
    path: String,
    name: String,
    extension: String,
    size: u64,
}

fn supported_extension(path: &Path) -> Option<String> {
    let extension = path.extension()?.to_str()?.to_lowercase();
    SUPPORTED_EXTENSIONS
        .contains(&extension.as_str())
        .then_some(extension)
}

pub(crate) fn database_path() -> Result<PathBuf, String> {
    if let Ok(configured) = env::var("NOVELFORGE_DB_PATH") {
        if !configured.trim().is_empty() {
            return Ok(PathBuf::from(configured));
        }
    }
    #[cfg(target_os = "windows")]
    let root = env::var("APPDATA").map(PathBuf::from).map_err(|error| error.to_string())?;
    #[cfg(target_os = "macos")]
    let root = PathBuf::from(env::var("HOME").map_err(|error| error.to_string())?)
        .join("Library")
        .join("Application Support");
    #[cfg(all(unix, not(target_os = "macos")))]
    let root = env::var("XDG_DATA_HOME")
        .map(PathBuf::from)
        .unwrap_or_else(|_| PathBuf::from(env::var("HOME").unwrap_or_default()).join(".local/share"));
    Ok(root
        .join("NovelForge")
        .join("storage")
        .join("novel_forge.db"))
}

#[tauri::command]
pub fn read_file_content(path: String) -> Result<String, String> {
    let path = Path::new(&path);
    if supported_extension(path).is_none() {
        return Err("unsupported file format".to_string());
    }
    fs::read(path)
        .map(|content| String::from_utf8_lossy(&content).into_owned())
        .map_err(|error| error.to_string())
}

#[tauri::command]
pub fn get_file_metadata(paths: Vec<String>) -> Vec<FileInfo> {
    paths
        .into_iter()
        .filter_map(|path| {
            let file_path = Path::new(&path);
            let extension = supported_extension(file_path)?;
            let metadata = fs::metadata(file_path).ok()?;
            if !metadata.is_file() {
                return None;
            }
            let name = file_path.file_name()?.to_string_lossy().into_owned();
            Some(FileInfo {
                name,
                path,
                extension,
                size: metadata.len(),
            })
        })
        .collect()
}

#[tauri::command]
pub fn open_files(app: AppHandle) -> Result<Vec<String>, String> {
    let selected = app
        .dialog()
        .file()
        .set_title("选择小说素材")
        .add_filter("小说素材", &SUPPORTED_EXTENSIONS)
        .blocking_pick_files()
        .unwrap_or_default();
    selected
        .into_iter()
        .map(|path| {
            path.into_path()
                .map(|value| value.to_string_lossy().into_owned())
                .map_err(|error| error.to_string())
        })
        .collect()
}

#[tauri::command]
pub fn open_dialog(app: AppHandle) -> Result<Option<String>, String> {
    app.dialog()
        .file()
        .set_title("选择小说导出目录")
        .blocking_pick_folder()
        .map(|path| {
            path.into_path()
                .map(|value| value.to_string_lossy().into_owned())
                .map_err(|error| error.to_string())
        })
        .transpose()
}

fn safe_file_name(file_name: &str, extension: &str) -> String {
    let stem = Path::new(file_name.trim())
        .file_stem()
        .and_then(|value| value.to_str())
        .unwrap_or("NovelForge-小说");
    let sanitized = stem
        .chars()
        .map(|character| {
            if character.is_control() || r#"<>:"/\|?*"#.contains(character) {
                '_'
            } else {
                character
            }
        })
        .collect::<String>();
    format!("{}.{}", sanitized.trim_matches([' ', '.']), extension)
}

fn open_folder(path: &Path) -> Result<(), String> {
    #[cfg(target_os = "windows")]
    Command::new("explorer.exe")
        .arg(path)
        .spawn()
        .map_err(|error| error.to_string())?;
    #[cfg(target_os = "macos")]
    Command::new("open")
        .arg(path)
        .spawn()
        .map_err(|error| error.to_string())?;
    #[cfg(all(unix, not(target_os = "macos")))]
    Command::new("xdg-open")
        .arg(path)
        .spawn()
        .map_err(|error| error.to_string())?;
    Ok(())
}

#[tauri::command]
pub fn write_file(
    target_directory: String,
    file_name: String,
    format: String,
    content_base64: String,
) -> Result<String, String> {
    let format = format.to_lowercase();
    if !matches!(format.as_str(), "epub" | "txt" | "pdf") {
        return Err("unsupported export format".to_string());
    }
    let directory = PathBuf::from(target_directory)
        .canonicalize()
        .map_err(|error| error.to_string())?;
    if !directory.is_dir() {
        return Err("target directory does not exist".to_string());
    }
    let bytes = BASE64
        .decode(content_base64)
        .map_err(|_| "invalid export data".to_string())?;
    let target = directory.join(safe_file_name(&file_name, &format));
    fs::write(&target, bytes).map_err(|error| error.to_string())?;
    open_folder(&directory)?;
    Ok(target.to_string_lossy().into_owned())
}

#[tauri::command]
pub fn save_app_state(app: AppHandle, key: String, value: String) -> Result<(), String> {
    let store = app.store("window-state.json").map_err(|error| error.to_string())?;
    store.set(key, value);
    store.save().map_err(|error| error.to_string())
}

#[tauri::command]
pub fn load_app_state(app: AppHandle, key: String) -> Result<Option<String>, String> {
    let store = app.store("window-state.json").map_err(|error| error.to_string())?;
    Ok(store.get(key).and_then(|value| value.as_str().map(str::to_string)))
}

#[cfg(windows)]
fn protect_value(value: &str) -> Result<String, String> {
    use std::ptr;
    use std::slice;
    use windows_sys::Win32::Foundation::LocalFree;
    use windows_sys::Win32::Security::Cryptography::{CryptProtectData, CRYPTPROTECT_UI_FORBIDDEN, CRYPT_INTEGER_BLOB};
    let bytes = value.as_bytes();
    let mut input = CRYPT_INTEGER_BLOB { cbData: bytes.len().try_into().map_err(|_| "configuration is too large")?, pbData: bytes.as_ptr() as *mut u8 };
    let mut output = CRYPT_INTEGER_BLOB { cbData: 0, pbData: ptr::null_mut() };
    let success = unsafe { CryptProtectData(&mut input, ptr::null(), ptr::null(), ptr::null(), ptr::null(), CRYPTPROTECT_UI_FORBIDDEN, &mut output) };
    if success == 0 {
        return Err(std::io::Error::last_os_error().to_string());
    }
    let protected = unsafe { slice::from_raw_parts(output.pbData, output.cbData as usize) };
    let encoded = BASE64.encode(protected);
    unsafe { LocalFree(output.pbData.cast()); }
    Ok(encoded)
}

#[cfg(windows)]
fn unprotect_value(value: &str) -> Result<String, String> {
    use std::ptr;
    use std::slice;
    use windows_sys::Win32::Foundation::LocalFree;
    use windows_sys::Win32::Security::Cryptography::{CryptUnprotectData, CRYPTPROTECT_UI_FORBIDDEN, CRYPT_INTEGER_BLOB};
    let mut encrypted = BASE64.decode(value).map_err(|_| "invalid encrypted configuration")?;
    let mut input = CRYPT_INTEGER_BLOB { cbData: encrypted.len().try_into().map_err(|_| "configuration is too large")?, pbData: encrypted.as_mut_ptr() };
    let mut output = CRYPT_INTEGER_BLOB { cbData: 0, pbData: ptr::null_mut() };
    let success = unsafe { CryptUnprotectData(&mut input, ptr::null_mut(), ptr::null(), ptr::null(), ptr::null(), CRYPTPROTECT_UI_FORBIDDEN, &mut output) };
    if success == 0 {
        return Err(std::io::Error::last_os_error().to_string());
    }
    let decrypted = unsafe { slice::from_raw_parts(output.pbData, output.cbData as usize) };
    let result = String::from_utf8(decrypted.to_vec()).map_err(|error| error.to_string());
    unsafe { LocalFree(output.pbData.cast()); }
    result
}

#[cfg(not(windows))]
fn protect_value(value: &str) -> Result<String, String> {
    Ok(BASE64.encode(value.as_bytes()))
}

#[cfg(not(windows))]
fn unprotect_value(value: &str) -> Result<String, String> {
    String::from_utf8(BASE64.decode(value).map_err(|error| error.to_string())?).map_err(|error| error.to_string())
}

#[tauri::command]
pub fn save_api_profiles(app: AppHandle, value: String) -> Result<(), String> {
    let encrypted = protect_value(&value)?;
    let store = app.store("settings.json").map_err(|error| error.to_string())?;
    store.set(API_PROFILES_KEY, encrypted);
    store.save().map_err(|error| error.to_string())
}

#[tauri::command]
pub fn load_api_profiles(app: AppHandle) -> Result<Option<String>, String> {
    let store = app.store("settings.json").map_err(|error| error.to_string())?;
    let stored = store
        .get(API_PROFILES_KEY)
        .and_then(|value| value.as_str().map(str::to_string))
        .map(|value| unprotect_value(&value))
        .transpose()?;
    if stored.is_some() {
        return Ok(stored);
    }
    let path = database_path()?;
    if !path.is_file() {
        return Ok(None);
    }
    let connection = Connection::open(path).map_err(|error| error.to_string())?;
    let has_configs: bool = connection
        .query_row(
            "SELECT EXISTS(SELECT 1 FROM sqlite_master WHERE type='table' AND name='configs')",
            [],
            |row| row.get(0),
        )
        .map_err(|error| error.to_string())?;
    if !has_configs {
        return Ok(None);
    }
    let legacy: Option<String> = connection
        .query_row(
            "SELECT value FROM configs WHERE key=?1",
            params![API_PROFILES_KEY],
            |row| row.get(0),
        )
        .optional()
        .map_err(|error| error.to_string())?;
    let decrypted = legacy.map(|value| unprotect_value(&value)).transpose()?;
    if let Some(value) = &decrypted {
        store.set(API_PROFILES_KEY, protect_value(value)?);
        store.save().map_err(|error| error.to_string())?;
        connection.execute("DROP TABLE configs", []).map_err(|error| error.to_string())?;
    }
    Ok(decrypted)
}
