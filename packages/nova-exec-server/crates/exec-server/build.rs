//! 注入编译 target（含架构与 ABI）。（对位 codex 39f4277087 build-info build.rs）

fn main() -> Result<(), std::env::VarError> {
    let target = std::env::var("TARGET")?;
    println!("cargo:rustc-env=NOVA_EXEC_SERVER_BUILD_TARGET={target}");
    println!("cargo:rerun-if-changed=build.rs");
    Ok(())
}
