use serde::Deserialize;
use serde::Serialize;

use crate::NoiseChannelPublicKey;

#[derive(Clone, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct EnvironmentRegistryRegistrationRequest {
    pub security_profile: String,
    pub executor_public_key: NoiseChannelPublicKey,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct EnvironmentRegistryRegistrationResponse {
    pub environment_id: String,
    pub url: String,
    pub security_profile: String,
    pub executor_registration_id: String,
}

#[derive(Clone, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct EnvironmentRegistryConnectRequest {
    pub harness_public_key: NoiseChannelPublicKey,
}

#[derive(Clone, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct EnvironmentRegistryConnectResponse {
    pub environment_id: String,
    pub url: String,
    pub security_profile: String,
    pub executor_registration_id: String,
    pub executor_public_key: NoiseChannelPublicKey,
    pub harness_key_authorization: String,
}

impl std::fmt::Debug for EnvironmentRegistryConnectResponse {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("EnvironmentRegistryConnectResponse")
            .field("environment_id", &self.environment_id)
            .field("url", &"<redacted>")
            .field("security_profile", &self.security_profile)
            .field("executor_registration_id", &self.executor_registration_id)
            .field("executor_public_key", &self.executor_public_key)
            .field("harness_key_authorization", &"<redacted>")
            .finish()
    }
}

#[derive(Clone, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct EnvironmentRegistryHarnessKeyValidationRequest {
    pub executor_registration_id: String,
    pub harness_public_key: NoiseChannelPublicKey,
    pub harness_key_authorization: String,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct EnvironmentRegistryHarnessKeyValidationResponse {
    pub valid: bool,
}
