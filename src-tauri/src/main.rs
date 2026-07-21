#![cfg_attr(
    all(not(debug_assertions), target_os = "windows"),
    windows_subsystem = "windows"
)]

mod commands;

use commands::database_path;
use commands::{
    get_file_metadata, load_api_profiles, load_app_state, open_dialog, open_files,
    read_file_content, save_api_profiles, save_app_state, write_file,
};
use serde::Serialize;
use std::env;
use std::net::TcpListener;
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use std::thread;
use std::time::Duration;
use tauri::{Manager, State};
use uuid::Uuid;

#[derive(Clone, Serialize)]
#[serde(rename_all = "camelCase")]
struct AgentConnection {
    base_url: String,
    token: String,
    instance_id: String,
}

struct ManagedAgent {
    child: Child,
    connection: AgentConnection,
}

struct PythonAgent(Mutex<Option<ManagedAgent>>);

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
        executable_dir
            .join("..")
            .join("python-agent")
            .join("app.py"),
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

fn python_agent_binary(resource_dir: Option<&Path>) -> Option<PathBuf> {
    let executable = env::current_exe().ok()?;
    let executable_dir = executable.parent()?;
    let binary_name = if cfg!(target_os = "windows") {
        "novelforge-agent.exe"
    } else {
        "novelforge-agent"
    };
    let mut candidates = Vec::new();
    if let Some(resources) = resource_dir {
        candidates.push(resources.join("python-agent").join(binary_name));
        candidates.push(
            resources
                .join("_up_")
                .join("python-agent")
                .join(binary_name),
        );
    }
    candidates.extend([
        executable_dir.join(binary_name),
        executable_dir.join("python-agent").join(binary_name),
        Path::new(env!("CARGO_MANIFEST_DIR"))
            .parent()?
            .join("python-agent")
            .join("dist")
            .join(binary_name),
    ]);
    candidates.into_iter().find(|path| path.is_file())
}

fn available_agent_port() -> Option<u16> {
    TcpListener::bind(("127.0.0.1", 0))
        .ok()?
        .local_addr()
        .ok()
        .map(|address| address.port())
}

fn new_agent_connection(port: u16) -> AgentConnection {
    AgentConnection {
        base_url: format!("http://127.0.0.1:{port}"),
        token: format!("{}{}", Uuid::new_v4().simple(), Uuid::new_v4().simple()),
        instance_id: Uuid::new_v4().to_string(),
    }
}

fn configure_agent_command(
    mut command: Command,
    script: Option<&Path>,
    working_directory: &Path,
    database: &Path,
    port: u16,
    connection: &AgentConnection,
) -> Command {
    if let Some(script) = script {
        command.arg(script);
    }
    command
        .current_dir(working_directory)
        .env("NOVELFORGE_DB_PATH", database)
        .env("NOVELFORGE_AGENT_PORT", port.to_string())
        .env("NOVELFORGE_AGENT_TOKEN", &connection.token)
        .env("NOVELFORGE_AGENT_INSTANCE_ID", &connection.instance_id)
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null());
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        command.creation_flags(0x08000000);
    }
    command
}

fn child_survived_startup(mut child: Child) -> Option<Child> {
    thread::sleep(Duration::from_millis(150));
    match child.try_wait() {
        Ok(None) => Some(child),
        Ok(Some(_)) => None,
        Err(_) => {
            let _ = child.kill();
            let _ = child.wait();
            None
        }
    }
}

fn launch_agent(
    command: Command,
    script: Option<&Path>,
    working_directory: &Path,
    database: &Path,
) -> Option<ManagedAgent> {
    let port = available_agent_port()?;
    let connection = new_agent_connection(port);
    let mut command = configure_agent_command(
        command,
        script,
        working_directory,
        database,
        port,
        &connection,
    );
    let child = child_survived_startup(command.spawn().ok()?)?;
    Some(ManagedAgent { child, connection })
}

fn start_python_agent(resource_dir: Option<&Path>) -> Option<ManagedAgent> {
    let database = database_path().ok()?;
    if let Some(binary) = python_agent_binary(resource_dir) {
        let working_directory = binary.parent()?.to_path_buf();
        if let Some(agent) =
            launch_agent(Command::new(&binary), None, &working_directory, &database)
        {
            return Some(agent);
        }
    }
    let script = python_agent_script(resource_dir)?;
    let working_directory = script.parent()?;
    #[cfg(target_os = "windows")]
    let executables = ["pythonw.exe", "python.exe"];
    #[cfg(not(target_os = "windows"))]
    let executables = ["python3", "python"];
    for executable in executables {
        if let Some(agent) = launch_agent(
            Command::new(executable),
            Some(&script),
            working_directory,
            &database,
        ) {
            return Some(agent);
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

#[tauri::command]
fn get_agent_connection(state: State<'_, PythonAgent>) -> Result<AgentConnection, String> {
    let agent = state.0.lock().map_err(|_| "Agent 状态不可用".to_string())?;
    agent
        .as_ref()
        .map(|managed| managed.connection.clone())
        .ok_or_else(|| "Python Agent 启动失败，请检查运行环境".to_string())
}

fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_store::Builder::new().build())
        .plugin(tauri_plugin_dialog::init())
        .setup(|app| {
            let resource_dir = app.path().resource_dir().ok();
            app.manage(PythonAgent(Mutex::new(start_python_agent(
                resource_dir.as_deref(),
            ))));
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            ping,
            get_agent_connection,
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
                if let Ok(mut agent) = app_handle.state::<PythonAgent>().0.lock() {
                    if let Some(managed) = agent.take() {
                        stop_python_agent(managed.child);
                    }
                }
            }
        });
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn generated_connection_is_unique_and_loopback_only() {
        let first = new_agent_connection(12345);
        let second = new_agent_connection(12345);

        assert_eq!(first.base_url, "http://127.0.0.1:12345");
        assert_eq!(first.token.len(), 64);
        assert_ne!(first.token, second.token);
        assert!(Uuid::parse_str(&first.instance_id).is_ok());
    }

    #[test]
    fn available_port_is_nonzero() {
        assert!(available_agent_port().is_some_and(|port| port > 0));
    }

    #[test]
    fn immediately_exited_agent_is_rejected() {
        #[cfg(target_os = "windows")]
        let child = Command::new("cmd.exe")
            .args(["/C", "exit", "1"])
            .spawn()
            .expect("spawn immediate exit process");
        #[cfg(not(target_os = "windows"))]
        let child = Command::new("sh")
            .args(["-c", "exit 1"])
            .spawn()
            .expect("spawn immediate exit process");

        assert!(child_survived_startup(child).is_none());
    }
}
