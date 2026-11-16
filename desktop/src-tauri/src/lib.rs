//! The Auris desktop shell.
//!
//! A thin native window. It starts the Python engine (`auris app serve`),
//! waits for the engine's one-line JSON "ready" event, then opens the main
//! window on the engine's loopback URL. The window is an ordinary web page:
//! it gets no access to native APIs and cannot leave the engine's address.
//! Saving a file the page made opens the macOS Save panel, so nothing is
//! written anywhere the clinician did not choose. Quitting closes the engine's
//! standard input, which makes the engine shut down; it is killed if it does not.

use std::io::{BufRead, BufReader};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use std::thread;
use std::time::Duration;

use tauri::webview::DownloadEvent;
use tauri::{
    AppHandle, Manager, RunEvent, Runtime, Url, Webview, WebviewUrl, WebviewWindowBuilder,
};

struct Engine(Mutex<Option<Child>>);

/// The repository the development build runs the engine from.
fn repository() -> PathBuf {
    std::env::var_os("AURIS_REPO")
        .map(PathBuf::from)
        .unwrap_or_else(|| Path::new(env!("CARGO_MANIFEST_DIR")).join("..").join(".."))
}

/// Development: run the engine from the repository with uv. The packaged app
/// ships its own engine and replaces this command.
fn engine_command(data_dir: &Path) -> Command {
    let repo = repository();
    let mut command = Command::new("uv");
    command
        .arg("run")
        .arg("--project")
        .arg(&repo)
        .args([
            "auris",
            "app",
            "serve",
            "--json",
            "--exit-with-parent",
            "--data",
        ])
        .arg(data_dir)
        .arg("--ui")
        .arg(repo.join("ui").join("dist"))
        .current_dir(&repo);
    if std::env::var_os("AURIS_DEMO").is_some() {
        command.arg("--demo");
    }
    command
}

/// Reads the engine's first line: {"event": "ready", "url": "http://127.0.0.1:…/#token=…"}.
fn ready_url(line: &str) -> Option<Url> {
    let event: serde_json::Value = serde_json::from_str(line.trim()).ok()?;
    if event.get("event")?.as_str()? != "ready" {
        return None;
    }
    let url = Url::parse(event.get("url")?.as_str()?).ok()?;
    // Only ever open the engine on this machine's loopback address.
    (url.scheme() == "http" && url.host_str() == Some("127.0.0.1")).then_some(url)
}

/// The engine's own page: plain http on 127.0.0.1 at the engine's port.
fn is_engine_url(url: &Url, port: u16) -> bool {
    url.scheme() == "http" && url.host_str() == Some("127.0.0.1") && url.port() == Some(port)
}

/// A file the engine's page made in the window: blob:http://127.0.0.1:<port>/<id>.
fn is_engine_blob(url: &Url, port: u16) -> bool {
    url.scheme() == "blob" && Url::parse(url.path()).is_ok_and(|inner| is_engine_url(&inner, port))
}

/// Without this handler WebKit cancels every download, so the export could not
/// be saved. Each file goes through the Save panel; anything else is refused.
fn save_download<R: Runtime>(webview: Webview<R>, event: DownloadEvent<'_>, port: u16) -> bool {
    match event {
        DownloadEvent::Requested { url, destination } => {
            if !is_engine_blob(&url, port) {
                return false;
            }
            let name = destination
                .file_name()
                .map(|name| name.to_string_lossy().into_owned())
                .unwrap_or_default();
            let window = webview.window();
            let Some(path) = rfd::FileDialog::new()
                .set_title("Save")
                .set_file_name(name)
                .set_parent(&window)
                .save_file()
            else {
                return false; // Cancelled: nothing is written.
            };
            // The panel has already asked before replacing; WebKit never writes over a file.
            if path.exists() && std::fs::remove_file(&path).is_err() {
                return false;
            }
            *destination = path;
            true
        }
        DownloadEvent::Finished { success, .. } => {
            if !success {
                rfd::MessageDialog::new()
                    .set_level(rfd::MessageLevel::Warning)
                    .set_title("The file was not saved")
                    .set_description(
                        "Auris could not write the file. Try saving it again, somewhere else.",
                    )
                    .set_parent(&webview.window())
                    .show();
            }
            true
        }
        _ => false,
    }
}

fn open_main(app: &AppHandle, url: Url) -> tauri::Result<()> {
    let port = url.port().unwrap_or(0);
    WebviewWindowBuilder::new(app, "main", WebviewUrl::External(url))
        .on_navigation(move |to| is_engine_url(to, port))
        .on_download(move |webview, event| save_download(webview, event, port))
        .title("Auris")
        .inner_size(1280.0, 860.0)
        .min_inner_size(960.0, 640.0)
        .build()?;
    if let Some(splash) = app.get_webview_window("splash") {
        splash.close()?;
    }
    Ok(())
}

fn show_failure(app: &AppHandle) {
    let _ = WebviewWindowBuilder::new(app, "failure", WebviewUrl::App("failure.html".into()))
        .title("Auris")
        .inner_size(520.0, 360.0)
        .resizable(false)
        .build();
    if let Some(splash) = app.get_webview_window("splash") {
        let _ = splash.close();
    }
}

fn stop_engine(app: &AppHandle) {
    let Some(engine) = app.try_state::<Engine>() else {
        return;
    };
    let Some(mut child) = engine.0.lock().ok().and_then(|mut slot| slot.take()) else {
        return;
    };
    drop(child.stdin.take()); // EOF: the engine shuts its server down.
    for _ in 0..30 {
        if matches!(child.try_wait(), Ok(Some(_))) {
            return;
        }
        thread::sleep(Duration::from_millis(100));
    }
    let _ = child.kill();
    let _ = child.wait();
}

pub fn run() {
    let app = tauri::Builder::default()
        .setup(|app| {
            // AURIS_DATA points a development or demo run at a throwaway folder.
            let data_dir = match std::env::var_os("AURIS_DATA") {
                Some(dir) => PathBuf::from(dir),
                None => app.path().app_data_dir()?,
            };
            std::fs::create_dir_all(&data_dir)?;
            let mut child = engine_command(&data_dir)
                .stdin(Stdio::piped())
                .stdout(Stdio::piped())
                .stderr(Stdio::inherit())
                .spawn()
                .map_err(|err| format!("could not start the Auris engine: {err}"))?;
            let stdout = child.stdout.take().ok_or("the engine has no output")?;
            app.manage(Engine(Mutex::new(Some(child))));

            let handle = app.handle().clone();
            thread::spawn(move || {
                let mut reader = BufReader::new(stdout);
                let mut line = String::new();
                let opened = match reader.read_line(&mut line) {
                    Ok(n) if n > 0 => ready_url(&line).map(|url| open_main(&handle, url).is_ok()),
                    _ => None,
                };
                if opened != Some(true) {
                    show_failure(&handle);
                }
                // Keep reading so the engine never blocks on a full pipe.
                for _ in reader.lines() {}
            });
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("Auris could not start");

    app.run(|handle, event| {
        if let RunEvent::Exit = event {
            stop_engine(handle);
        }
    });
}

#[cfg(test)]
mod tests {
    use super::{is_engine_blob, is_engine_url, ready_url};
    use tauri::Url;

    #[test]
    fn the_window_stays_on_the_engine_and_saves_only_its_files() {
        let url = |s: &str| Url::parse(s).unwrap();
        assert!(is_engine_url(
            &url("http://127.0.0.1:5123/#token=abc"),
            5123
        ));
        assert!(!is_engine_url(&url("http://127.0.0.1:5124/"), 5123));
        assert!(!is_engine_url(&url("https://127.0.0.1:5123/"), 5123));
        assert!(!is_engine_url(&url("http://localhost:5123/"), 5123));
        assert!(!is_engine_url(&url("https://evil.example/"), 5123));
        assert!(is_engine_blob(
            &url("blob:http://127.0.0.1:5123/0b1c-2d3e"),
            5123
        ));
        assert!(!is_engine_blob(
            &url("blob:http://127.0.0.1:9999/0b1c"),
            5123
        ));
        assert!(!is_engine_blob(
            &url("blob:https://evil.example/0b1c"),
            5123
        ));
        assert!(!is_engine_blob(
            &url("http://127.0.0.1:5123/file.json"),
            5123
        ));
        assert!(!is_engine_blob(&url("data:text/plain,hi"), 5123));
    }

    #[test]
    fn accepts_only_a_loopback_ready_event() {
        let ok = ready_url(r#"{"event":"ready","url":"http://127.0.0.1:5123/#token=abc"}"#);
        assert_eq!(ok.unwrap().port(), Some(5123));
        for bad in [
            r#"{"event":"ready","url":"http://evil.example/#token=abc"}"#,
            r#"{"event":"ready","url":"https://127.0.0.1:5123/"}"#,
            r#"{"event":"ready","url":"http://localhost:5123/"}"#,
            r#"{"event":"started","url":"http://127.0.0.1:5123/"}"#,
            r#"{"event":"ready"}"#,
            "Open http://127.0.0.1:5123/",
            "",
        ] {
            assert!(ready_url(bad).is_none(), "{bad}");
        }
    }
}
