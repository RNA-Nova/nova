fn main() {
    println!("cargo:rerun-if-env-changed=NOVA_EXECUTOR_BWRAP_SHA256");
}
