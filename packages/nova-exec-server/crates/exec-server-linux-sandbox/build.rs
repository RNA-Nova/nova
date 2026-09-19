fn main() {
    println!("cargo:rerun-if-env-changed=NOVA_EXEC_SERVER_BWRAP_SHA256");
}
