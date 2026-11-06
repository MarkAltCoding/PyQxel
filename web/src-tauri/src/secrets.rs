//! Where the app keeps its secrets: the PyQxel API key it is signed in with, and the
//! provider keys (Anthropic, Tiingo, FMP, EODHD, Alpha Vantage, Finnhub) the person
//! added. The page reads and writes them through the three commands here instead of web
//! storage, which WKWebView writes to disk a moment after a change, so a write was lost
//! when iOS killed the app first (as when it was stopped from Xcode).
//!
//! - **iOS:** the Keychain, readable after the phone's first unlock since it started,
//!   on this device only: never synced to iCloud or restored to another phone.
//!   Keychain items outlive the app, so the first launch after installing it deletes
//!   any an earlier installation left, and deleting the app signs it out.
//! - **Everywhere else (macOS for now):** a file in the app's own data folder, readable
//!   by the person's account alone and written whole or not at all. On the Mac the folder
//!   is in the App Sandbox's container, which macOS keeps other apps out of. The macOS
//!   Keychain would ask for the account's password after each update, since its access
//!   is tied to the app's signature, which changes with every ad hoc signed build.
//!
//! The threat model, what this protects against and what it does not, is at the top of
//! `web/src/lib/secureStorage.ts`.

use std::sync::Mutex;

use tauri::{AppHandle, Manager, Runtime};

/// The names the page may keep; anything else is refused.
pub const NAMES: [&str; 2] = ["pyqxel.apiKey", "pyqxel.providerKeys"];

/// One write or read at a time, so two quick writes cannot interleave.
static LOCK: Mutex<()> = Mutex::new(());

fn check_name(name: &str) -> Result<(), String> {
  if NAMES.contains(&name) {
    Ok(())
  } else {
    Err(format!("{name} is not kept in secure storage."))
  }
}

#[cfg(target_os = "ios")]
mod store {
  //! The iOS Keychain.

  use super::{AppHandle, NAMES, Runtime};
  use security_framework::access_control::{ProtectionMode, SecAccessControl};
  use security_framework::passwords::{
    PasswordOptions, delete_generic_password_options, generic_password,
    set_generic_password_options,
  };

  const SERVICE: &str = "com.markaltcoding.pyqxel";
  /// `errSecItemNotFound`: nothing is kept under the name.
  const NOT_FOUND: i32 = -25300;

  fn options(name: &str) -> PasswordOptions {
    PasswordOptions::new_generic_password(SERVICE, name)
  }

  pub fn get<R: Runtime>(_app: &AppHandle<R>, name: &str) -> Result<Option<String>, String> {
    match generic_password(options(name)) {
      Ok(bytes) => String::from_utf8(bytes).map(Some).map_err(|error| error.to_string()),
      Err(error) if error.code() == NOT_FOUND => Ok(None),
      Err(error) => Err(error.to_string()),
    }
  }

  pub fn delete<R: Runtime>(_app: &AppHandle<R>, name: &str) -> Result<(), String> {
    match delete_generic_password_options(options(name)) {
      Err(error) if error.code() != NOT_FOUND => Err(error.to_string()),
      _ => Ok(()),
    }
  }

  pub fn set<R: Runtime>(app: &AppHandle<R>, name: &str, value: &str) -> Result<(), String> {
    // Replaced rather than updated, so the item always has the protection below.
    delete(app, name)?;
    let mut options = options(name);
    let protection = SecAccessControl::create_with_protection(
      Some(ProtectionMode::AccessibleAfterFirstUnlockThisDeviceOnly),
      0,
    )
    .map_err(|error| error.to_string())?;
    options.set_access_control(protection);
    options.set_access_synchronized(Some(false));
    set_generic_password_options(value.as_bytes(), options).map_err(|error| error.to_string())
  }

  /// Deletes what an earlier installation of the app left in the Keychain.
  pub fn forget_all<R: Runtime>(app: &AppHandle<R>) {
    for name in NAMES {
      let _ = delete(app, name);
    }
  }
}

#[cfg(not(target_os = "ios"))]
mod store {
  //! A file in the app's data folder, readable by the person's account alone.

  use super::{AppHandle, Manager, Runtime};
  use std::collections::BTreeMap;
  use std::fs;
  use std::io::Write;
  use std::path::PathBuf;

  fn path<R: Runtime>(app: &AppHandle<R>) -> Result<PathBuf, String> {
    let folder = app.path().app_data_dir().map_err(|error| error.to_string())?;
    fs::create_dir_all(&folder).map_err(|error| error.to_string())?;
    Ok(folder.join("secrets.json"))
  }

  fn read_all<R: Runtime>(app: &AppHandle<R>) -> Result<BTreeMap<String, String>, String> {
    match fs::read(path(app)?) {
      Ok(bytes) => serde_json::from_slice(&bytes).map_err(|error| error.to_string()),
      Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(BTreeMap::new()),
      Err(error) => Err(error.to_string()),
    }
  }

  /// Writes a new file beside the old one, flushes it to disk, then puts it in the old
  /// one's place, so a crash leaves either file whole.
  fn write_all<R: Runtime>(app: &AppHandle<R>, all: &BTreeMap<String, String>) -> Result<(), String> {
    let target = path(app)?;
    let temporary = target.with_extension("json.tmp");
    let bytes = serde_json::to_vec(all).map_err(|error| error.to_string())?;
    let mut options = fs::OpenOptions::new();
    options.write(true).create(true).truncate(true);
    #[cfg(unix)]
    {
      use std::os::unix::fs::OpenOptionsExt;
      options.mode(0o600);
    }
    let mut file = options.open(&temporary).map_err(|error| error.to_string())?;
    file.write_all(&bytes).map_err(|error| error.to_string())?;
    file.sync_all().map_err(|error| error.to_string())?;
    fs::rename(&temporary, &target).map_err(|error| error.to_string())
  }

  pub fn get<R: Runtime>(app: &AppHandle<R>, name: &str) -> Result<Option<String>, String> {
    Ok(read_all(app)?.remove(name))
  }

  pub fn set<R: Runtime>(app: &AppHandle<R>, name: &str, value: &str) -> Result<(), String> {
    let mut all = read_all(app)?;
    all.insert(name.to_string(), value.to_string());
    write_all(app, &all)
  }

  pub fn delete<R: Runtime>(app: &AppHandle<R>, name: &str) -> Result<(), String> {
    let mut all = read_all(app)?;
    if all.remove(name).is_some() {
      write_all(app, &all)?;
    }
    Ok(())
  }

  /// Nothing outlives the app here: its data folder goes with it.
  pub fn forget_all<R: Runtime>(_app: &AppHandle<R>) {}
}

/// On the first launch after installing, deletes secrets an earlier installation left
/// behind (the iOS Keychain keeps them after the app is deleted). The data folder, which
/// goes with the app, marks that this installation has launched before.
pub fn forget_earlier_installations<R: Runtime>(app: &AppHandle<R>) {
  let Ok(folder) = app.path().app_data_dir() else {
    return;
  };
  let marker = folder.join("installed");
  if marker.exists() {
    return;
  }
  store::forget_all(app);
  let _ = std::fs::create_dir_all(&folder);
  let _ = std::fs::write(marker, b"");
}

/// The secret kept under `name`, if any.
#[tauri::command]
pub fn secret_get<R: Runtime>(app: AppHandle<R>, name: String) -> Result<Option<String>, String> {
  check_name(&name)?;
  let _guard = LOCK.lock().map_err(|error| error.to_string())?;
  store::get(&app, &name)
}

/// Keeps `value` under `name`, on disk before this returns.
#[tauri::command]
pub fn secret_set<R: Runtime>(app: AppHandle<R>, name: String, value: String) -> Result<(), String> {
  check_name(&name)?;
  let _guard = LOCK.lock().map_err(|error| error.to_string())?;
  store::set(&app, &name, &value)
}

/// Forgets what is kept under `name`.
#[tauri::command]
pub fn secret_delete<R: Runtime>(app: AppHandle<R>, name: String) -> Result<(), String> {
  check_name(&name)?;
  let _guard = LOCK.lock().map_err(|error| error.to_string())?;
  store::delete(&app, &name)
}
