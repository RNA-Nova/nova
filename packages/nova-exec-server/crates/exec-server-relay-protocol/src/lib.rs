//! Nova exec-server Relay 的独立线上契约。
//!
//! 本 crate 只承载 Registry DTO、Noise 公钥形状与 Protobuf 帧；
//! 加密、WebSocket、路由与执行行为均归各自实现 crate。

mod frame;
mod noise;
mod registry;

pub use frame::RelayFrameBodyKind;
pub use frame::RelayFrameError;

pub use noise::NOISE_CHANNEL_SUITE;
pub use noise::NoiseChannelPublicKey;
pub use registry::*;

/// RelayMessageFrame.version 的当前值。
pub const RELAY_FRAME_VERSION: u32 = 1;
/// 当前混合 Noise 握手的线上安全档位。
pub const NOISE_RELAY_SECURITY_PROFILE: &str = "noise_hybrid_ik_v1";

pub mod relay_proto {
    include!(concat!(env!("OUT_DIR"), "/nova.exec_server.relay.v1.rs"));
}

pub use relay_proto::RelayData;
pub use relay_proto::RelayHandshake;
pub use relay_proto::RelayMessageFrame;
pub use relay_proto::RelayReset;
pub use relay_proto::RelayResume;
pub use relay_proto::relay_message_frame;
