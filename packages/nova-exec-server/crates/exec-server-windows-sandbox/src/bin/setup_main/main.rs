#[cfg(target_os = "windows")]
fn main() -> anyhow::Result<()> {
    nova_exec_server_windows_sandbox::setup_helper_main()
}

#[cfg(not(target_os = "windows"))]
fn main() {
    panic!("nova-windows-sandbox-setup is Windows-only");
}
