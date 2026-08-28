/// helper 二进制本体只做入口转发：cwd、env 与命令参数会原样保留到最终的
/// `execv` 调用，因此调用方负责保证这些值正确。
fn main() -> ! {
    nova_executor_linux_sandbox::run_main()
}
