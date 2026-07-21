#![cfg_attr(all(not(debug_assertions), target_os = "windows"), windows_subsystem = "windows")]

mod commands;

use commands::{
    get_file_metadata, load_api_profiles, load_app_state, open_dialog, open_files, read_file_content,
    save_api_profiles, save_app_state, write_file,
};
use commands::database_path;
use std::env;
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use tauri::Manager;

struct PythonAgent(Mutex<Option<Child>>);

fn python_agent_script(resource_dir: Option<&Path>) -> Option<PathBuf> {
    let executable = env::current_exe().ok()?;
    let executable_dir = executable.parent()?;
    let mut candidates = Vec::new();
    if let Some(resources) = resource_dir {
        candidates.push(resources.join("python-agent").join("app.py"));
        candidates.push(resources.join("_up_").join("python-agent").join("app.py"));
    }
    candidates.extend([
        executable_dir.join("python-agent").join("app.py"),
        executable_dir.join("..").join("python-agent").join("app.py"),
        executable_dir
            .join("..")
            .join("..")
            .join("..")
            .join("python-agent")
            .join("app.py"),
        Path::new(env!("CARGO_MANIFEST_DIR"))
            .parent()?
            .join("python-agent")
            .join("app.py"),
    ]);
    candidates.into_iter().find(|path| path.is_file())
}

fn start_python_agent(resource_dir: Option<&Path>) -> Option<Child> {
    let database = database_path().ok()?;
    let script = python_agent_script(resource_dir)?;
    let working_directory = script.parent()?;
    #[cfg(target_os = "windows")]
    let executables = ["pythonw.exe", "python.exe"];
    #[cfg(not(target_os = "windows"))]
    let executables = ["python3", "python"];
    for executable in executables {
        let mut command = Command::new(executable);
        command
            .arg(&script)
            .current_dir(working_directory)
            .env("NOVELFORGE_DB_PATH", &database)
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null());
        #[cfg(windows)]
        {
            use std::os::windows::process::CommandExt;
            command.creation_flags(0x08000000);
        }
        if let Ok(child) = command.spawn() {
            return Some(child);
        }
    }
    None
}

fn stop_python_agent(mut child: Child) {
    #[cfg(target_os = "windows")]
    {
        use std::os::windows::process::CommandExt;
        let mut command = Command::new("taskkill.exe");
        command
            .args(["/PID", &child.id().to_string(), "/T", "/F"])
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .creation_flags(0x08000000);
        let _ = command.status();
    }
    #[cfg(not(target_os = "windows"))]
    {
        let _ = child.kill();
    }
    let _ = child.wait();
}

#[tauri::command]
fn ping() -> &'static str {
    "pong"
}

fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_store::Builder::new().build())
        .plugin(tauri_plugin_dialog::init())
        .setup(|app| {
            let resource_dir = app.path().resource_dir().ok();
            app.manage(PythonAgent(Mutex::new(start_python_agent(resource_dir.as_deref()))));
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            ping,
            read_file_content,
            get_file_metadata,
            open_files,
            open_dialog,
            write_file,
            save_app_state,
            load_app_state,
            save_api_profiles,
            load_api_profiles
        ])
        .build(tauri::generate_context!())
        .expect("error while building NovelForge")
        .run(|app_handle, event| {
            if matches!(event, tauri::RunEvent::Exit) {
                if let Ok(mut child) = app_handle.state::<PythonAgent>().0.lock() {
                    if let Some(child) = child.take() {
                        stop_python_agent(child);
                    }
                }
            }
        });
}
