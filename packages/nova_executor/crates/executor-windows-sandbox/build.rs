use std::env;
use std::path::PathBuf;

const SETUP_BIN: &str = "codex-windows-sandbox-setup";
const SETUP_MANIFEST: &str = "codex-windows-sandbox-setup.manifest";

fn main() -> Result<(), String> {
    println!("cargo:rerun-if-changed={SETUP_MANIFEST}");

    if env::var("CARGO_CFG_TARGET_OS").as_deref() != Ok("windows") {
        return Ok(());
    }

    let manifest_dir = env::var_os("CARGO_MANIFEST_DIR")
        .ok_or_else(|| "CARGO_MANIFEST_DIR should be set for build scripts".to_string())?;
    let manifest_path = PathBuf::from(manifest_dir).join(SETUP_MANIFEST);
    let manifest_path = manifest_path.display().to_string();
    // mt.exe 不认 \\?\ 扩展长度路径前缀（报 c1010070 "Failed to load and parse
    // the manifest"）；链接器把 verbatim 形式透传给 mt 时会踩中，剥回常规形式。
    // 工作区普通路径本来就不需要 verbatim 形式（无超长/保留名成分）。
    let manifest_path = strip_verbatim_prefix(&manifest_path);

    // Keep this scoped to the setup helper so Codex binaries that link the
    // library do not inherit any resource metadata from this package.
    match (
        env::var("CARGO_CFG_TARGET_ENV").as_deref(),
        env::var("CARGO_CFG_TARGET_ABI").as_deref(),
    ) {
        (Ok("msvc"), _) => {
            println!("cargo:rustc-link-arg-bin={SETUP_BIN}=/MANIFEST:EMBED");
            println!("cargo:rustc-link-arg-bin={SETUP_BIN}=/MANIFESTINPUT:{manifest_path}");
        }
        (Ok("gnu"), Ok("llvm")) => {
            println!("cargo:rustc-link-arg-bin={SETUP_BIN}=-Wl,-Xlink=/manifest:embed");
            println!(
                "cargo:rustc-link-arg-bin={SETUP_BIN}=-Wl,-Xlink=/manifestinput:{manifest_path}"
            );
        }
        _ => {}
    }

    Ok(())
}

/// 剥掉 Windows verbatim 路径前缀：`\\?\C:\x` → `C:\x`，`\\?\UNC\host\share`
/// → `\\host\share`；非 verbatim 路径原样借用返回。
fn strip_verbatim_prefix(path: &str) -> std::borrow::Cow<'_, str> {
    if let Some(rest) = path.strip_prefix(r"\\?\UNC\") {
        return std::borrow::Cow::Owned(format!(r"\\{rest}"));
    }
    std::borrow::Cow::Borrowed(path.strip_prefix(r"\\?\").unwrap_or(path))
}
