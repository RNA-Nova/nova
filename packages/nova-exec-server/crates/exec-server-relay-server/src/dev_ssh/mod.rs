//! 开发期 OpenSSH 反向隧道。
//!
//! 该数据面与 exec JSON-RPC / Noise Relay 完全分离：SSH 自身提供端到端
//! 加密，Relay 只在已登记节点与短期客户端授权之间转发二进制流。

mod client;
mod frame;
mod node;
mod router;
pub(crate) mod server;

pub use client::DevSshClientConfig;
pub use client::run_dev_ssh_stdio;
pub use client::run_dev_ssh_tcp;
pub use client::serve_dev_ssh_listener;
pub use frame::DevSshFrame;
pub use frame::DevSshFrameError;
pub use frame::MAX_DEV_SSH_DATA_BYTES;
pub use node::DevSshNodeConfig;
pub use node::run_dev_ssh_node;
pub use router::DevSshRouter;

pub(crate) use frame::dev_ssh_frame;
pub(crate) use frame::web_socket_config;

use serde::Deserialize;
use serde::Serialize;

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct DevSshConnectResponse {
    pub environment_id: String,
    pub url: String,
}
