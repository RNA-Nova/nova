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

    // Keep this scoped to the setup helper so Codex binaries that link the
    // library do not inherit any resource metadata from this package.
    match (
        env::var("CARGO_CFG_TARGET_ENV").as_deref(),
        env::var("CARGO_CFG_TARGET_ABI").as_deref(),
    ) {
        (Ok("msvc"), _) => {
            // 不走 /MANIFESTINPUT：VS18 工具链的 link.exe 会把清单路径规范化成
            // \\?\ verbatim 形式再交给 mt.exe，而 mt.exe 不认该形式（报
            // c1010070 "The system cannot find the file specified"）。改为把清单
            // 作为 RT_MANIFEST 资源写进 .rc → rc.exe 编成 .res 直接链接，全程
            // 不经 mt.exe；compile_for 只作用于 setup bin，作用域与原来一致。
            let out_dir = env::var_os("OUT_DIR")
                .ok_or_else(|| "OUT_DIR should be set for build scripts".to_string())?;
            let rc_path = PathBuf::from(out_dir).join("codex-windows-sandbox-setup.rc");
            // 1 = CREATEPROCESS_MANIFEST_RESOURCE_ID，24 = RT_MANIFEST；
            // rc 字符串里的路径用正斜杠（rc.exe 接受，免反斜杠转义）
            let manifest_arg = manifest_path.display().to_string().replace('\\', "/");
            std::fs::write(&rc_path, format!("1 24 \"{manifest_arg}\"\n"))
                .map_err(|err| format!("failed to write {}: {err}", rc_path.display()))?;
            embed_resource::compile_for(&rc_path, [SETUP_BIN], embed_resource::NONE)
                .manifest_required()
                .map_err(|err| format!("failed to compile manifest resource: {err}"))?;
        }
        (Ok("gnu"), Ok("llvm")) => {
            println!("cargo:rustc-link-arg-bin={SETUP_BIN}=-Wl,-Xlink=/manifest:embed");
            println!(
                "cargo:rustc-link-arg-bin={SETUP_BIN}=-Wl,-Xlink=/manifestinput:{}",
                manifest_path.display()
            );
        }
        _ => {}
    }

    Ok(())
}
