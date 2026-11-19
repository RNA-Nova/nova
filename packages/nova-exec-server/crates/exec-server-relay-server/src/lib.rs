//! Nova 自有 Registry 与 Rendezvous 服务。
//!
//! exec 数据面只保存注册与授权代际、路由 Noise 密文，不解析 exec JSON-RPC。
//! 独立 dev-ssh 数据面只转发 OpenSSH 字节流，不向公网开放任意 TCP 代理。

pub mod dev_ssh;
pub mod registry;
pub mod rendezvous;
pub mod server;

pub use dev_ssh::DevSshClientConfig;
pub use dev_ssh::DevSshFrame;
pub use dev_ssh::DevSshNodeConfig;
pub use dev_ssh::DevSshRouter;
pub use dev_ssh::run_dev_ssh_node;
pub use dev_ssh::run_dev_ssh_stdio;
pub use dev_ssh::run_dev_ssh_tcp;
pub use dev_ssh::serve_dev_ssh_listener;
pub use registry::DevSshEnrollmentTokens;
pub use registry::Registry;
pub use registry::RegistryError;
pub use rendezvous::RendezvousRouter;
pub use server::RelayServerConfig;
pub use server::run_server;
