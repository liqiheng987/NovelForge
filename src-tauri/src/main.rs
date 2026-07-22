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
use std::fs::{self, File, OpenOptions};
use std::io::{Read, Write};
use std::net::{SocketAddr, TcpListener, TcpStream};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Mutex;
use std::thread;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};
use tauri::{AppHandle, Manager, State};
use tauri_plugin_dialog::DialogExt;
use uuid::Uuid;

const AGENT_STARTUP_TIMEOUT: Duration = Duration::from_secs(12);
const AGENT_HEALTH_TIMEOUT: Duration = Duration::from_millis(500);
const AGENT_MONITOR_INTERVAL: Duration = Duration::from_secs(2);
const AGENT_HEALTH_FAILURE_LIMIT: u8 = 3;
const AGENT_AUTO_RESTART_LIMIT: u8 = 3;
const AGENT_STABLE_UPTIME: Duration = Duration::from_secs(30);
const PROCESS_LOG_MAX_BYTES: u64 = 2 * 1024 * 1024;

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
    port: u16,
    started_at: u64,
    started: Instant,
}

#[derive(Clone)]
struct AgentLaunchConfig {
    resource_dir: Option<PathBuf>,
    database: PathBuf,
    log_dir: PathBuf,
    process_log: PathBuf,
}

struct AgentRuntime {
    agent: Option<ManagedAgent>,
    detail: String,
    last_error: Option<String>,
    restart_count: u32,
    consecutive_failures: u8,
    health_failures: u8,
}

struct PythonAgent {
    runtime: Mutex<AgentRuntime>,
    config: AgentLaunchConfig,
    shutting_down: AtomicBool,
}

#[derive(Clone, Serialize)]
#[serde(rename_all = "camelCase")]
struct AgentServiceStatus {
    status: &'static str,
    detail: String,
    restart_count: u32,
    started_at: Option<u64>,
    instance_id: Option<String>,
}

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

fn unix_timestamp() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs()
}

fn rotate_process_log(path: &Path) {
    let Ok(metadata) = fs::metadata(path) else {
        return;
    };
    if metadata.len() < PROCESS_LOG_MAX_BYTES {
        return;
    }
    let rotated = path.with_extension("previous.log");
    let _ = fs::remove_file(&rotated);
    let _ = fs::rename(path, rotated);
}

fn process_log_files(path: &Path) -> Option<(File, File)> {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent).ok()?;
    }
    rotate_process_log(path);
    let mut output = OpenOptions::new()
        .create(true)
        .append(true)
        .open(path)
        .ok()?;
    let _ = writeln!(output, "\n[{}] starting NovelForge Agent", unix_timestamp());
    let error = output.try_clone().ok()?;
    Some((output, error))
}

fn configure_agent_command(
    mut command: Command,
    script: Option<&Path>,
    working_directory: &Path,
    database: &Path,
    process_log: &Path,
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
        .stdin(Stdio::null());
    if let Some((output, error)) = process_log_files(process_log) {
        command
            .stdout(Stdio::from(output))
            .stderr(Stdio::from(error));
    } else {
        command.stdout(Stdio::null()).stderr(Stdio::null());
    }
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        command.creation_flags(0x08000000);
    }
    command
}

fn health_response_matches(response: &str, instance_id: &str) -> bool {
    let status_ok = response.starts_with("HTTP/1.1 200") || response.starts_with("HTTP/1.0 200");
    let body = response.split_once("\r\n\r\n").map_or("", |(_, body)| body);
    let payload = serde_json::from_str::<serde_json::Value>(body).ok();
    status_ok
        && payload
            .as_ref()
            .and_then(|value| value.get("status"))
            .and_then(serde_json::Value::as_str)
            == Some("ok")
        && payload
            .as_ref()
            .and_then(|value| value.get("instance_id"))
            .and_then(serde_json::Value::as_str)
            == Some(instance_id)
}

fn agent_health_ready(port: u16, instance_id: &str, timeout: Duration) -> bool {
    let address = SocketAddr::from(([127, 0, 0, 1], port));
    let Ok(mut stream) = TcpStream::connect_timeout(&address, timeout) else {
        return false;
    };
    let _ = stream.set_read_timeout(Some(timeout));
    let _ = stream.set_write_timeout(Some(timeout));
    if stream
        .write_all(b"GET /health HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n")
        .is_err()
    {
        return false;
    }
    let mut response = String::new();
    if stream
        .take(16 * 1024)
        .read_to_string(&mut response)
        .is_err()
    {
        return false;
    }
    health_response_matches(&response, instance_id)
}

fn wait_for_agent_ready(
    child: &mut Child,
    port: u16,
    instance_id: &str,
    timeout: Duration,
) -> Result<(), String> {
    let deadline = Instant::now() + timeout;
    loop {
        match child.try_wait() {
            Ok(Some(status)) => {
                return Err(format!(
                    "Agent 进程提前退出（状态码 {}）",
                    status
                        .code()
                        .map_or_else(|| "未知".to_string(), |code| code.to_string())
                ));
            }
            Ok(None) => {}
            Err(error) => return Err(format!("无法读取 Agent 进程状态：{error}")),
        }
        if agent_health_ready(port, instance_id, AGENT_HEALTH_TIMEOUT) {
            return Ok(());
        }
        if Instant::now() >= deadline {
            return Err("Agent 启动超时，健康检查未通过".to_string());
        }
        thread::sleep(Duration::from_millis(100));
    }
}

fn launch_agent(
    command: Command,
    script: Option<&Path>,
    working_directory: &Path,
    database: &Path,
    process_log: &Path,
) -> Result<ManagedAgent, String> {
    let port = available_agent_port().ok_or_else(|| "无法分配本地 Agent 端口".to_string())?;
    let connection = new_agent_connection(port);
    let mut command = configure_agent_command(
        command,
        script,
        working_directory,
        database,
        process_log,
        port,
        &connection,
    );
    let mut child = command
        .spawn()
        .map_err(|error| format!("无法启动 Agent 进程：{error}"))?;
    if let Err(error) = wait_for_agent_ready(
        &mut child,
        port,
        &connection.instance_id,
        AGENT_STARTUP_TIMEOUT,
    ) {
        stop_python_agent(child);
        return Err(error);
    }
    Ok(ManagedAgent {
        child,
        connection,
        port,
        started_at: unix_timestamp(),
        started: Instant::now(),
    })
}

fn start_python_agent(config: &AgentLaunchConfig) -> Result<ManagedAgent, String> {
    let mut errors = Vec::new();
    if let Some(binary) = python_agent_binary(config.resource_dir.as_deref()) {
        if let Some(working_directory) = binary.parent() {
            match launch_agent(
                Command::new(&binary),
                None,
                working_directory,
                &config.database,
                &config.process_log,
            ) {
                Ok(agent) => return Ok(agent),
                Err(error) => errors.push(format!("内置 Agent：{error}")),
            }
        }
    }
    let script = python_agent_script(config.resource_dir.as_deref())
        .ok_or_else(|| "未找到内置 Agent 或开发版 Agent 脚本".to_string())?;
    let working_directory = script
        .parent()
        .ok_or_else(|| "Agent 脚本目录无效".to_string())?;
    #[cfg(target_os = "windows")]
    let executables = ["pythonw.exe", "python.exe"];
    #[cfg(not(target_os = "windows"))]
    let executables = ["python3", "python"];
    for executable in executables {
        match launch_agent(
            Command::new(executable),
            Some(&script),
            working_directory,
            &config.database,
            &config.process_log,
        ) {
            Ok(agent) => return Ok(agent),
            Err(error) => errors.push(format!("{executable}：{error}")),
        }
    }
    Err(if errors.is_empty() {
        "没有可用的 Agent 运行环境".to_string()
    } else {
        errors.join("；")
    })
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

fn service_status(runtime: &AgentRuntime) -> AgentServiceStatus {
    let (status, started_at, instance_id) = if let Some(agent) = &runtime.agent {
        (
            "online",
            Some(agent.started_at),
            Some(agent.connection.instance_id.clone()),
        )
    } else if runtime.detail.contains("恢复") || runtime.detail.contains("启动") {
        ("recovering", None, None)
    } else {
        ("offline", None, None)
    };
    AgentServiceStatus {
        status,
        detail: runtime.detail.clone(),
        restart_count: runtime.restart_count,
        started_at,
        instance_id,
    }
}

fn replace_agent(runtime: &mut AgentRuntime, config: &AgentLaunchConfig) -> Result<(), String> {
    if let Some(agent) = runtime.agent.take() {
        stop_python_agent(agent.child);
    }
    runtime.detail = "Agent 正在启动".to_string();
    match start_python_agent(config) {
        Ok(agent) => {
            runtime.agent = Some(agent);
            runtime.detail = "Agent 运行正常".to_string();
            runtime.last_error = None;
            runtime.health_failures = 0;
            Ok(())
        }
        Err(error) => {
            runtime.detail = error.clone();
            runtime.last_error = Some(error.clone());
            Err(error)
        }
    }
}

fn monitor_agent(app_handle: AppHandle) {
    thread::spawn(move || loop {
        thread::sleep(AGENT_MONITOR_INTERVAL);
        let state = app_handle.state::<PythonAgent>();
        if state.shutting_down.load(Ordering::Relaxed) {
            break;
        }
        let Ok(mut runtime) = state.runtime.lock() else {
            continue;
        };

        let mut failure = None;
        let mut health_checked = false;
        let mut health_succeeded = false;
        let mut stable = false;
        if let Some(agent) = runtime.agent.as_mut() {
            match agent.child.try_wait() {
                Ok(Some(status)) => {
                    failure = Some(format!(
                        "Agent 意外退出（状态码 {}）",
                        status
                            .code()
                            .map_or_else(|| "未知".to_string(), |code| code.to_string())
                    ));
                }
                Err(error) => failure = Some(format!("无法读取 Agent 进程状态：{error}")),
                Ok(None) => {
                    health_checked = true;
                    health_succeeded = agent_health_ready(
                        agent.port,
                        &agent.connection.instance_id,
                        AGENT_HEALTH_TIMEOUT,
                    );
                    stable = agent.started.elapsed() >= AGENT_STABLE_UPTIME;
                }
            }
        }

        if health_checked && health_succeeded {
            runtime.health_failures = 0;
            if stable {
                runtime.consecutive_failures = 0;
            }
        } else if health_checked {
            runtime.health_failures = runtime.health_failures.saturating_add(1);
            if runtime.health_failures >= AGENT_HEALTH_FAILURE_LIMIT {
                failure = Some("Agent 连续健康检查失败".to_string());
            }
        }

        if let Some(error) = failure {
            if let Some(agent) = runtime.agent.take() {
                stop_python_agent(agent.child);
            }
            runtime.last_error = Some(error);
            runtime.health_failures = 0;
            runtime.consecutive_failures = runtime.consecutive_failures.saturating_add(1);
        }

        if runtime.agent.is_none()
            && runtime.consecutive_failures < AGENT_AUTO_RESTART_LIMIT
            && !state.shutting_down.load(Ordering::Relaxed)
        {
            runtime.detail = "Agent 正在自动恢复".to_string();
            match start_python_agent(&state.config) {
                Ok(agent) => {
                    runtime.agent = Some(agent);
                    runtime.detail = "Agent 已自动恢复".to_string();
                    runtime.restart_count = runtime.restart_count.saturating_add(1);
                    runtime.health_failures = 0;
                }
                Err(error) => {
                    runtime.detail = error.clone();
                    runtime.last_error = Some(error);
                    runtime.consecutive_failures = runtime.consecutive_failures.saturating_add(1);
                }
            }
        } else if runtime.agent.is_none() {
            runtime.detail = "Agent 自动恢复失败，请在设置中手动重启".to_string();
        }
    });
}

#[tauri::command]
fn agent_service_status(state: State<'_, PythonAgent>) -> Result<AgentServiceStatus, String> {
    let runtime = state
        .runtime
        .lock()
        .map_err(|_| "Agent 状态不可用".to_string())?;
    Ok(service_status(&runtime))
}

#[tauri::command]
fn restart_agent(state: State<'_, PythonAgent>) -> Result<AgentServiceStatus, String> {
    let mut runtime = state
        .runtime
        .lock()
        .map_err(|_| "Agent 状态不可用".to_string())?;
    runtime.consecutive_failures = 0;
    runtime.health_failures = 0;
    replace_agent(&mut runtime, &state.config)?;
    runtime.restart_count = runtime.restart_count.saturating_add(1);
    Ok(service_status(&runtime))
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct DiagnosticLogMetadata {
    name: &'static str,
    exists: bool,
    size: u64,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct DiagnosticDatabaseMetadata {
    exists: bool,
    size: u64,
    integrity: String,
    backup_count: usize,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct DiagnosticReport {
    generated_at: u64,
    app_version: String,
    operating_system: &'static str,
    architecture: &'static str,
    agent: AgentServiceStatus,
    last_error: Option<String>,
    database: DiagnosticDatabaseMetadata,
    logs: Vec<DiagnosticLogMetadata>,
    privacy_notice: &'static str,
}

fn log_metadata(name: &'static str, path: &Path) -> DiagnosticLogMetadata {
    let metadata = fs::metadata(path).ok();
    DiagnosticLogMetadata {
        name,
        exists: metadata.is_some(),
        size: metadata.map_or(0, |value| value.len()),
    }
}

fn database_metadata(path: &Path) -> DiagnosticDatabaseMetadata {
    let metadata = fs::metadata(path).ok();
    let integrity = if metadata.is_none() {
        "not_created".to_string()
    } else {
        rusqlite::Connection::open_with_flags(path, rusqlite::OpenFlags::SQLITE_OPEN_READ_ONLY)
            .and_then(|connection| {
                connection.query_row("PRAGMA quick_check", [], |row| row.get::<_, String>(0))
            })
            .unwrap_or_else(|error| format!("error: {error}"))
    };
    let backup_count = path
        .parent()
        .map(|parent| parent.join("backups"))
        .and_then(|directory| fs::read_dir(directory).ok())
        .map(|entries| {
            entries
                .filter_map(Result::ok)
                .filter(|entry| {
                    entry.file_type().is_ok_and(|kind| kind.is_file())
                        && entry.file_name().to_str().is_some_and(|name| {
                            name.starts_with("novel_forge-") && name.ends_with(".db")
                        })
                })
                .count()
        })
        .unwrap_or(0);
    DiagnosticDatabaseMetadata {
        exists: metadata.is_some(),
        size: metadata.map_or(0, |value| value.len()),
        integrity,
        backup_count,
    }
}

fn redact_diagnostic_text(value: &str, config: &AgentLaunchConfig) -> String {
    let mut redacted = value.to_string();
    let mut sensitive_paths = vec![config.database.to_string_lossy().into_owned()];
    for key in ["USERPROFILE", "HOME", "APPDATA"] {
        if let Ok(path) = env::var(key) {
            if !path.trim().is_empty() {
                sensitive_paths.push(path);
            }
        }
    }
    for path in sensitive_paths {
        redacted = redacted.replace(&path, "<redacted-path>");
    }
    redacted
}

fn diagnostic_report(app: &AppHandle, state: &PythonAgent) -> Result<DiagnosticReport, String> {
    let runtime = state
        .runtime
        .lock()
        .map_err(|_| "Agent 状态不可用".to_string())?;
    let application_log = state.config.log_dir.join("agent.log");
    let mut database = database_metadata(&state.config.database);
    database.integrity = redact_diagnostic_text(&database.integrity, &state.config);
    Ok(DiagnosticReport {
        generated_at: unix_timestamp(),
        app_version: app.package_info().version.to_string(),
        operating_system: env::consts::OS,
        architecture: env::consts::ARCH,
        agent: service_status(&runtime),
        last_error: runtime
            .last_error
            .as_deref()
            .map(|error| redact_diagnostic_text(error, &state.config)),
        database,
        logs: vec![
            log_metadata("agent-process.log", &state.config.process_log),
            log_metadata("agent.log", &application_log),
        ],
        privacy_notice: "This report contains status metadata only. API keys, prompts, novel text, user file names, and log contents are excluded.",
    })
}

#[tauri::command]
fn open_agent_logs(state: State<'_, PythonAgent>) -> Result<String, String> {
    fs::create_dir_all(&state.config.log_dir).map_err(|error| error.to_string())?;
    commands::open_folder(&state.config.log_dir)?;
    Ok(state.config.log_dir.to_string_lossy().into_owned())
}

#[tauri::command]
fn export_diagnostics(
    app: AppHandle,
    state: State<'_, PythonAgent>,
) -> Result<Option<String>, String> {
    let Some(directory) = app
        .dialog()
        .file()
        .set_title("选择诊断报告保存目录")
        .blocking_pick_folder()
    else {
        return Ok(None);
    };
    let directory = directory.into_path().map_err(|error| error.to_string())?;
    let report = diagnostic_report(&app, &state)?;
    let bytes = serde_json::to_vec_pretty(&report).map_err(|error| error.to_string())?;
    let path = commands::write_export_file(
        &directory,
        &format!("NovelForge-diagnostics-{}", report.generated_at),
        "json",
        &bytes,
    )?;
    commands::open_folder(&directory)?;
    Ok(Some(path.to_string_lossy().into_owned()))
}

#[tauri::command]
fn ping() -> &'static str {
    "pong"
}

#[tauri::command]
fn get_agent_connection(state: State<'_, PythonAgent>) -> Result<AgentConnection, String> {
    let mut runtime = state
        .runtime
        .lock()
        .map_err(|_| "Agent 状态不可用".to_string())?;
    let exited = if let Some(agent) = runtime.agent.as_mut() {
        agent
            .child
            .try_wait()
            .map_err(|error| error.to_string())?
            .is_some()
    } else {
        false
    };
    if exited {
        if let Some(agent) = runtime.agent.take() {
            stop_python_agent(agent.child);
        }
        runtime.detail = "Agent 意外退出，正在重新启动".to_string();
        runtime.consecutive_failures = runtime.consecutive_failures.saturating_add(1);
    }
    if runtime.agent.is_none() {
        if runtime.consecutive_failures >= AGENT_AUTO_RESTART_LIMIT {
            return Err("Agent 连续恢复失败，请在设置中手动重启".to_string());
        }
        if let Err(error) = replace_agent(&mut runtime, &state.config) {
            runtime.consecutive_failures = runtime.consecutive_failures.saturating_add(1);
            return Err(error);
        }
        runtime.restart_count = runtime.restart_count.saturating_add(1);
    }
    runtime
        .agent
        .as_ref()
        .map(|managed| managed.connection.clone())
        .ok_or_else(|| runtime.detail.clone())
}

fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_store::Builder::new().build())
        .plugin(tauri_plugin_dialog::init())
        .setup(|app| {
            let resource_dir = app.path().resource_dir().ok();
            let database = database_path().map_err(std::io::Error::other)?;
            let log_dir = database
                .parent()
                .and_then(Path::parent)
                .map(|root| root.join("logs"))
                .ok_or_else(|| std::io::Error::other("数据库目录无效"))?;
            let config = AgentLaunchConfig {
                resource_dir,
                database,
                process_log: log_dir.join("agent-process.log"),
                log_dir,
            };
            let (agent, detail, last_error, consecutive_failures) =
                match start_python_agent(&config) {
                    Ok(agent) => (Some(agent), "Agent 运行正常".to_string(), None, 0),
                    Err(error) => (None, error.clone(), Some(error), 1),
                };
            app.manage(PythonAgent {
                runtime: Mutex::new(AgentRuntime {
                    agent,
                    detail,
                    last_error,
                    restart_count: 0,
                    consecutive_failures,
                    health_failures: 0,
                }),
                config,
                shutting_down: AtomicBool::new(false),
            });
            monitor_agent(app.handle().clone());
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            ping,
            get_agent_connection,
            agent_service_status,
            restart_agent,
            open_agent_logs,
            export_diagnostics,
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
                let state = app_handle.state::<PythonAgent>();
                state.shutting_down.store(true, Ordering::Relaxed);
                if let Ok(mut runtime) = state.runtime.lock() {
                    if let Some(managed) = runtime.agent.take() {
                        stop_python_agent(managed.child);
                    }
                };
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
    fn health_response_requires_status_and_matching_instance() {
        let valid = "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n\r\n{\"status\":\"ok\",\"instance_id\":\"expected\"}";
        assert!(health_response_matches(valid, "expected"));
        assert!(!health_response_matches(valid, "different"));
        assert!(!health_response_matches(
            "HTTP/1.1 500 Internal Server Error\r\n\r\n{\"status\":\"ok\",\"instance_id\":\"expected\"}",
            "expected"
        ));
    }

    #[test]
    fn immediately_exited_agent_is_rejected() {
        #[cfg(target_os = "windows")]
        let mut child = Command::new("cmd.exe")
            .args(["/C", "exit", "1"])
            .spawn()
            .expect("spawn immediate exit process");
        #[cfg(not(target_os = "windows"))]
        let mut child = Command::new("sh")
            .args(["-c", "exit 1"])
            .spawn()
            .expect("spawn immediate exit process");

        assert!(
            wait_for_agent_ready(&mut child, 9, "unreachable", Duration::from_secs(1)).is_err()
        );
    }

    #[test]
    fn diagnostic_redaction_removes_local_paths() {
        let database = std::env::temp_dir()
            .join("private-user")
            .join("novel_forge.db");
        let config = AgentLaunchConfig {
            resource_dir: None,
            log_dir: database.parent().expect("database parent").join("logs"),
            process_log: database
                .parent()
                .expect("database parent")
                .join("agent-process.log"),
            database: database.clone(),
        };
        let message = format!("database failed at {}", database.display());
        let redacted = redact_diagnostic_text(&message, &config);
        assert!(!redacted.contains("private-user"));
        assert!(redacted.contains("<redacted-path>"));
    }

    #[test]
    fn diagnostic_metadata_does_not_read_database_or_log_contents() {
        let directory =
            std::env::temp_dir().join(format!("novelforge-diagnostic-test-{}", Uuid::new_v4()));
        fs::create_dir_all(&directory).expect("create diagnostic test directory");
        let database = directory.join("novel_forge.db");
        let connection = rusqlite::Connection::open(&database).expect("create diagnostic database");
        connection
            .execute("CREATE TABLE private_content (value TEXT NOT NULL)", [])
            .expect("create private table");
        connection
            .execute(
                "INSERT INTO private_content (value) VALUES (?1)",
                ["SECRET_NOVEL_CONTENT"],
            )
            .expect("insert private content");
        drop(connection);
        let log = directory.join("agent.log");
        fs::write(&log, "SECRET_API_KEY").expect("write private log content");

        let database_json = serde_json::to_string(&database_metadata(&database))
            .expect("serialize database metadata");
        let log_json = serde_json::to_string(&log_metadata("agent.log", &log))
            .expect("serialize log metadata");

        assert!(!database_json.contains("SECRET_NOVEL_CONTENT"));
        assert!(!log_json.contains("SECRET_API_KEY"));
        assert!(database_json.contains("\"integrity\":\"ok\""));
        fs::remove_dir_all(directory).expect("remove diagnostic test directory");
    }

    #[test]
    fn real_agent_launch_waits_for_matching_health_endpoint() {
        let directory =
            std::env::temp_dir().join(format!("novelforge-agent-test-{}", Uuid::new_v4()));
        fs::create_dir_all(&directory).expect("create agent test directory");
        let config = AgentLaunchConfig {
            resource_dir: None,
            database: directory.join("novel_forge.db"),
            log_dir: directory.join("logs"),
            process_log: directory.join("logs").join("agent-process.log"),
        };

        let agent = start_python_agent(&config).expect("launch real agent");
        assert!(agent_health_ready(
            agent.port,
            &agent.connection.instance_id,
            AGENT_HEALTH_TIMEOUT
        ));
        assert!(config.process_log.is_file());

        stop_python_agent(agent.child);
        fs::remove_dir_all(directory).expect("remove agent test directory");
    }
}
