use base64::Engine as _;
use base64::engine::general_purpose::STANDARD;
use serde::Deserialize;
use serde::Serialize;

/// Noise 算法族标签，防止不同协议的密钥仅因长度相同而被混用。
pub const NOISE_CHANNEL_SUITE: &str = "Noise_hybridIK_X25519+MLKEM768_AESGCM_SHA256";

/// executor 或 harness 的公开 Noise 密钥材料。
///
/// 私钥永不进入本 crate；字段读取只供算法实现编码/解码使用。
#[derive(Clone, Eq, Hash, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct NoiseChannelPublicKey {
    suite: String,
    x25519_public_key: String,
    mlkem768_public_key: String,
}

impl NoiseChannelPublicKey {
    pub fn from_components(
        suite: impl Into<String>,
        x25519_public_key: impl Into<String>,
        mlkem768_public_key: impl Into<String>,
    ) -> Self {
        Self {
            suite: suite.into(),
            x25519_public_key: x25519_public_key.into(),
            mlkem768_public_key: mlkem768_public_key.into(),
        }
    }

    pub fn suite(&self) -> &str {
        &self.suite
    }

    pub fn x25519_public_key(&self) -> &str {
        &self.x25519_public_key
    }

    pub fn mlkem768_public_key(&self) -> &str {
        &self.mlkem768_public_key
    }

    /// 拒绝未知算法族与显然畸形的 base64；具体长度仍由 Noise 实现校验。
    pub fn validate_shape(&self) -> Result<(), &'static str> {
        if self.suite != NOISE_CHANNEL_SUITE {
            return Err("unsupported Noise channel suite");
        }
        STANDARD
            .decode(&self.x25519_public_key)
            .map_err(|_| "invalid X25519 public key")?;
        STANDARD
            .decode(&self.mlkem768_public_key)
            .map_err(|_| "invalid ML-KEM-768 public key")?;
        Ok(())
    }
}

impl std::fmt::Debug for NoiseChannelPublicKey {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("NoiseChannelPublicKey")
            .field("suite", &self.suite)
            .field("x25519_public_key", &"<redacted>")
            .field("mlkem768_public_key", &"<redacted>")
            .finish()
    }
}
