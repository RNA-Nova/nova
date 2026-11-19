fn main() -> Result<(), Box<dyn std::error::Error>> {
    let protoc = protoc_bin_vendored::protoc_bin_path()?;
    // 构建脚本单线程执行，且在任何 protoc 子进程启动前设置。
    unsafe { std::env::set_var("PROTOC", protoc) };
    prost_build::Config::new()
        .compile_protos(&["proto/nova.exec_server.relay.v1.proto"], &["proto"])?;
    println!("cargo:rerun-if-changed=proto/nova.exec_server.relay.v1.proto");
    Ok(())
}
