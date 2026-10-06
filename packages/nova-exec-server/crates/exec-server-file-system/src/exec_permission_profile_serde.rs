//! Uses executor file URIs for sandbox permissions instead of the profile's legacy native paths.
//!
//! 对位 codex 841b5490b2 `file-system/src/exec_permission_profile_serde.rs`。

use crate::ExecPermissionProfile;
use nova_exec_server_protocol_core::models::PermissionProfile;
use serde::Deserialize;
use serde::Deserializer;
use serde::Serialize;
use serde::Serializer;

pub(crate) fn serialize<S>(value: &PermissionProfile, serializer: S) -> Result<S::Ok, S::Error>
where
    S: Serializer,
{
    ExecPermissionProfile::from(value.clone()).serialize(serializer)
}

pub(crate) fn deserialize<'de, D>(deserializer: D) -> Result<PermissionProfile, D::Error>
where
    D: Deserializer<'de>,
{
    ExecPermissionProfile::deserialize(deserializer).map(PermissionProfile::from)
}
