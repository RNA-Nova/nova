use std::collections::HashMap;
use std::path::Path;
use std::sync::Arc;
use std::time::SystemTime;
use std::time::UNIX_EPOCH;

use http::StatusCode;
use nova_exec_server_relay_protocol::EnvironmentRegistryConnectRequest;
use nova_exec_server_relay_protocol::EnvironmentRegistryHarnessKeyValidationRequest;
use nova_exec_server_relay_protocol::EnvironmentRegistryRegistrationRequest;
use nova_exec_server_relay_protocol::NOISE_RELAY_SECURITY_PROFILE;
use nova_exec_server_relay_protocol::NoiseChannelPublicKey;
use rusqlite::Connection;
use rusqlite::OptionalExtension;
use rusqlite::params;
use sha2::Digest as _;
use sha2::Sha256;
use uuid::Uuid;

const HARNESS_AUTHORIZATION_TTL_SECONDS: i64 = 60;
const DEV_SSH_AUTHORIZATION_TTL_SECONDS: i64 = 60;

#[derive(Debug, thiserror::Error)]
#[error("{message}")]
pub struct RegistryError {
    pub status: StatusCode,
    pub kind: &'static str,
    pub message: String,
    pub internal: bool,
}

impl RegistryError {
    pub(crate) fn new(status: StatusCode, kind: &'static str, message: impl Into<String>) -> Self {
        Self {
            status,
            kind,
            message: message.into(),
            internal: false,
        }
    }

    fn unauthorized(message: &'static str) -> Self {
        Self::new(StatusCode::FORBIDDEN, "unauthorized", message)
    }

    fn offline() -> Self {
        Self::new(
            StatusCode::CONFLICT,
            "environment_offline",
            "Executor is not connected",
        )
    }

    fn registration_conflict() -> Self {
        Self::new(
            StatusCode::SERVICE_UNAVAILABLE,
            "registration_conflict",
            "Environment already has an active executor",
        )
    }

    pub(crate) fn internal(error: impl std::fmt::Display) -> Self {
        Self {
            status: StatusCode::INTERNAL_SERVER_ERROR,
            kind: "internal",
            message: error.to_string(),
            internal: true,
        }
    }
}

impl From<rusqlite::Error> for RegistryError {
    fn from(error: rusqlite::Error) -> Self {
        Self::internal(error)
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct EnrollmentTokens {
    pub node_token: String,
    pub harness_token: String,
}

/// 独立 dev-ssh 数据面的凭证；与 exec node/harness token 不互相通用。
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct DevSshEnrollmentTokens {
    pub node_token: String,
    pub client_token: String,
}

#[derive(Clone, Debug)]
pub struct RegistrationGrant {
    pub environment_id: String,
    pub registration_id: String,
    pub executor_token: String,
}

#[derive(Clone, Debug)]
pub struct ConnectionGrant {
    pub environment_id: String,
    pub registration_id: String,
    pub executor_public_key: NoiseChannelPublicKey,
    pub harness_key_authorization: String,
}

#[derive(Clone)]
pub struct Registry {
    inner: Arc<std::sync::Mutex<RegistryStore>>,
}

impl Registry {
    pub fn open(path: impl AsRef<Path>) -> Result<Self, RegistryError> {
        if let Some(parent) = path.as_ref().parent()
            && !parent.as_os_str().is_empty()
        {
            std::fs::create_dir_all(parent).map_err(RegistryError::internal)?;
        }
        let store = RegistryStore::open(path.as_ref())?;
        Ok(Self {
            inner: Arc::new(std::sync::Mutex::new(store)),
        })
    }

    async fn with<T, F>(&self, operation: F) -> Result<T, RegistryError>
    where
        T: Send + 'static,
        F: FnOnce(&mut RegistryStore) -> Result<T, RegistryError> + Send + 'static,
    {
        let inner = Arc::clone(&self.inner);
        tokio::task::spawn_blocking(move || {
            let mut store = inner
                .lock()
                .unwrap_or_else(std::sync::PoisonError::into_inner);
            operation(&mut store)
        })
        .await
        .map_err(RegistryError::internal)?
    }

    pub async fn enroll(
        &self,
        environment_id: String,
        tenant_id: String,
    ) -> Result<EnrollmentTokens, RegistryError> {
        validate_identifier(&environment_id, "environment")?;
        validate_identifier(&tenant_id, "tenant")?;
        let tokens = EnrollmentTokens {
            node_token: generate_token(),
            harness_token: generate_token(),
        };
        let stored = tokens.clone();
        self.with(move |store| {
            store.enroll(&environment_id, &tenant_id, &stored)?;
            Ok(tokens)
        })
        .await
    }

    pub async fn revoke(&self, environment_id: String) -> Result<bool, RegistryError> {
        self.with(move |store| store.revoke(&environment_id)).await
    }

    pub async fn register(
        &self,
        environment_id: String,
        node_token: String,
        request: EnvironmentRegistryRegistrationRequest,
    ) -> Result<RegistrationGrant, RegistryError> {
        if request.security_profile != NOISE_RELAY_SECURITY_PROFILE {
            return Err(RegistryError::new(
                StatusCode::BAD_REQUEST,
                "unsupported_security_profile",
                "Unsupported Noise security profile",
            ));
        }
        request.executor_public_key.validate_shape().map_err(|_| {
            RegistryError::new(
                StatusCode::BAD_REQUEST,
                "bad_request",
                "Invalid executor key",
            )
        })?;
        self.with(move |store| store.register(&environment_id, &node_token, request))
            .await
    }

    pub async fn connect(
        &self,
        environment_id: String,
        harness_token: String,
        request: EnvironmentRegistryConnectRequest,
    ) -> Result<ConnectionGrant, RegistryError> {
        request.harness_public_key.validate_shape().map_err(|_| {
            RegistryError::new(
                StatusCode::BAD_REQUEST,
                "bad_request",
                "Invalid harness key",
            )
        })?;
        self.with(move |store| store.connect(&environment_id, &harness_token, request))
            .await
    }

    pub async fn validate_harness(
        &self,
        environment_id: String,
        node_token: String,
        request: EnvironmentRegistryHarnessKeyValidationRequest,
    ) -> Result<bool, RegistryError> {
        self.with(move |store| store.validate_harness(&environment_id, &node_token, request))
            .await
    }

    pub async fn executor_authorized(
        &self,
        environment_id: String,
        registration_id: String,
        token: String,
    ) -> Result<bool, RegistryError> {
        self.with(move |store| store.executor_authorized(&environment_id, &registration_id, &token))
            .await
    }

    pub async fn harness_authorized(
        &self,
        environment_id: String,
        registration_id: String,
        authorization: String,
    ) -> Result<bool, RegistryError> {
        self.with(move |store| {
            store.harness_authorized(&environment_id, &registration_id, &authorization)
        })
        .await
    }

    pub async fn registration_active(
        &self,
        environment_id: String,
        registration_id: String,
    ) -> Result<bool, RegistryError> {
        self.with(move |store| store.registration_active(&environment_id, &registration_id))
            .await
    }

    pub async fn physical_connected(
        &self,
        environment_id: String,
        registration_id: String,
    ) -> Result<String, RegistryError> {
        self.with(move |store| store.physical_connected(&environment_id, &registration_id))
            .await
    }

    pub async fn physical_disconnected(
        &self,
        environment_id: String,
        registration_id: String,
        physical_id: String,
    ) -> Result<(), RegistryError> {
        self.with(move |store| {
            store.physical_disconnected(&environment_id, &registration_id, &physical_id);
            Ok(())
        })
        .await
    }

    pub async fn enroll_dev_ssh(
        &self,
        environment_id: String,
        tenant_id: String,
    ) -> Result<DevSshEnrollmentTokens, RegistryError> {
        validate_identifier(&environment_id, "environment")?;
        validate_identifier(&tenant_id, "tenant")?;
        let tokens = DevSshEnrollmentTokens {
            node_token: generate_token(),
            client_token: generate_token(),
        };
        let stored = tokens.clone();
        self.with(move |store| {
            store.enroll_dev_ssh(&environment_id, &tenant_id, &stored)?;
            Ok(tokens)
        })
        .await
    }

    pub async fn revoke_dev_ssh(&self, environment_id: String) -> Result<bool, RegistryError> {
        self.with(move |store| store.revoke_dev_ssh(&environment_id))
            .await
    }

    pub async fn dev_ssh_node_authorized(
        &self,
        environment_id: String,
        node_token: String,
    ) -> Result<bool, RegistryError> {
        self.with(move |store| {
            store.dev_ssh_enrollment_authorized(&environment_id, &node_token, true)
        })
        .await
    }

    /// 为一次 OpenSSH 客户端会话签发短期授权；授权只进入 WS URL，不落明文 DB。
    pub async fn connect_dev_ssh(
        &self,
        environment_id: String,
        client_token: String,
    ) -> Result<String, RegistryError> {
        self.with(move |store| store.connect_dev_ssh(&environment_id, &client_token))
            .await
    }

    pub async fn dev_ssh_client_authorized(
        &self,
        environment_id: String,
        authorization: String,
    ) -> Result<bool, RegistryError> {
        self.with(move |store| store.dev_ssh_client_authorized(&environment_id, &authorization))
            .await
    }
}

struct RegistryStore {
    db: Connection,
    physical: HashMap<(String, String), String>,
}

impl RegistryStore {
    fn open(path: &Path) -> Result<Self, RegistryError> {
        let db = Connection::open(path)?;
        db.execute_batch(
            "PRAGMA journal_mode=WAL;
             PRAGMA foreign_keys=ON;
             CREATE TABLE IF NOT EXISTS enrollments (
                 environment_id TEXT PRIMARY KEY,
                 tenant_id TEXT NOT NULL,
                 node_token_hash BLOB NOT NULL,
                 harness_token_hash BLOB NOT NULL,
                 enabled INTEGER NOT NULL DEFAULT 1
             );
             CREATE TABLE IF NOT EXISTS registrations (
                 registration_id TEXT PRIMARY KEY,
                 environment_id TEXT NOT NULL,
                 executor_public_key TEXT NOT NULL,
                 executor_token_hash BLOB NOT NULL,
                 created_at INTEGER NOT NULL,
                 active INTEGER NOT NULL DEFAULT 1,
                 FOREIGN KEY(environment_id) REFERENCES enrollments(environment_id)
             );
             CREATE TABLE IF NOT EXISTS harness_authorizations (
                 token_hash BLOB PRIMARY KEY,
                 registration_id TEXT NOT NULL,
                 harness_public_key TEXT NOT NULL,
                 expires_at INTEGER NOT NULL,
                 FOREIGN KEY(registration_id) REFERENCES registrations(registration_id)
             );
             CREATE TABLE IF NOT EXISTS dev_ssh_enrollments (
                 environment_id TEXT PRIMARY KEY,
                 tenant_id TEXT NOT NULL,
                 node_token_hash BLOB NOT NULL,
                 client_token_hash BLOB NOT NULL,
                 enabled INTEGER NOT NULL DEFAULT 1
             );
             CREATE TABLE IF NOT EXISTS dev_ssh_authorizations (
                 token_hash BLOB PRIMARY KEY,
                 environment_id TEXT NOT NULL,
                 expires_at INTEGER NOT NULL,
                 FOREIGN KEY(environment_id) REFERENCES dev_ssh_enrollments(environment_id)
             );",
        )?;
        Ok(Self {
            db,
            physical: HashMap::new(),
        })
    }

    fn enroll(
        &mut self,
        environment_id: &str,
        tenant_id: &str,
        tokens: &EnrollmentTokens,
    ) -> Result<(), RegistryError> {
        let tx = self.db.transaction()?;
        tx.execute(
            "INSERT INTO enrollments VALUES (?1, ?2, ?3, ?4, 1)
             ON CONFLICT(environment_id) DO UPDATE SET
                 tenant_id=excluded.tenant_id,
                 node_token_hash=excluded.node_token_hash,
                 harness_token_hash=excluded.harness_token_hash,
                 enabled=1",
            params![
                environment_id,
                tenant_id,
                token_hash(&tokens.node_token).as_slice(),
                token_hash(&tokens.harness_token).as_slice()
            ],
        )?;
        tx.execute(
            "DELETE FROM harness_authorizations WHERE registration_id IN
             (SELECT registration_id FROM registrations WHERE environment_id=?1)",
            params![environment_id],
        )?;
        tx.execute(
            "UPDATE registrations SET active=0 WHERE environment_id=?1",
            params![environment_id],
        )?;
        tx.commit()?;
        self.physical
            .retain(|(environment, _), _| environment != environment_id);
        Ok(())
    }

    fn revoke(&mut self, environment_id: &str) -> Result<bool, RegistryError> {
        let tx = self.db.transaction()?;
        let changed = tx.execute(
            "UPDATE enrollments SET enabled=0 WHERE environment_id=?1 AND enabled=1",
            params![environment_id],
        )?;
        tx.execute(
            "DELETE FROM harness_authorizations WHERE registration_id IN
             (SELECT registration_id FROM registrations WHERE environment_id=?1)",
            params![environment_id],
        )?;
        tx.execute(
            "UPDATE registrations SET active=0 WHERE environment_id=?1",
            params![environment_id],
        )?;
        tx.commit()?;
        self.physical
            .retain(|(environment, _), _| environment != environment_id);
        Ok(changed > 0)
    }

    fn register(
        &mut self,
        environment_id: &str,
        node_token: &str,
        request: EnvironmentRegistryRegistrationRequest,
    ) -> Result<RegistrationGrant, RegistryError> {
        if !self.enrollment_authorized(environment_id, node_token, true)? {
            return Err(RegistryError::unauthorized("Invalid node enrollment token"));
        }
        if self
            .physical
            .keys()
            .any(|(environment, _)| environment == environment_id)
        {
            return Err(RegistryError::registration_conflict());
        }
        let registration_id = Uuid::new_v4().simple().to_string();
        let executor_token = generate_token();
        let tx = self.db.transaction()?;
        tx.execute(
            "DELETE FROM harness_authorizations WHERE registration_id IN
             (SELECT registration_id FROM registrations WHERE environment_id=?1)",
            params![environment_id],
        )?;
        tx.execute(
            "UPDATE registrations SET active=0 WHERE environment_id=?1",
            params![environment_id],
        )?;
        tx.execute(
            "INSERT INTO registrations VALUES (?1, ?2, ?3, ?4, ?5, 1)",
            params![
                registration_id,
                environment_id,
                public_key_json(&request.executor_public_key)?,
                token_hash(&executor_token).as_slice(),
                now_seconds()
            ],
        )?;
        tx.commit()?;
        Ok(RegistrationGrant {
            environment_id: environment_id.to_string(),
            registration_id,
            executor_token,
        })
    }

    fn connect(
        &mut self,
        environment_id: &str,
        harness_token: &str,
        request: EnvironmentRegistryConnectRequest,
    ) -> Result<ConnectionGrant, RegistryError> {
        if !self.enrollment_authorized(environment_id, harness_token, false)? {
            return Err(RegistryError::unauthorized("Invalid harness token"));
        }
        let registration = self
            .db
            .query_row(
                "SELECT registration_id, executor_public_key FROM registrations
                 WHERE environment_id=?1 AND active=1 ORDER BY created_at DESC LIMIT 1",
                params![environment_id],
                |row| Ok((row.get::<_, String>(0)?, row.get::<_, String>(1)?)),
            )
            .optional()?;
        let Some((registration_id, executor_public_key)) = registration else {
            return Err(RegistryError::offline());
        };
        if !self
            .physical
            .contains_key(&(environment_id.to_string(), registration_id.clone()))
        {
            return Err(RegistryError::offline());
        }
        let authorization = generate_token();
        self.db.execute(
            "INSERT INTO harness_authorizations VALUES (?1, ?2, ?3, ?4)",
            params![
                token_hash(&authorization).as_slice(),
                registration_id,
                public_key_json(&request.harness_public_key)?,
                now_seconds() + HARNESS_AUTHORIZATION_TTL_SECONDS
            ],
        )?;
        Ok(ConnectionGrant {
            environment_id: environment_id.to_string(),
            registration_id,
            executor_public_key: serde_json::from_str(&executor_public_key)
                .map_err(RegistryError::internal)?,
            harness_key_authorization: authorization,
        })
    }

    fn validate_harness(
        &self,
        environment_id: &str,
        node_token: &str,
        request: EnvironmentRegistryHarnessKeyValidationRequest,
    ) -> Result<bool, RegistryError> {
        if !self.enrollment_authorized(environment_id, node_token, true)? {
            return Err(RegistryError::unauthorized("Invalid node enrollment token"));
        }
        let row = self
            .db
            .query_row(
                "SELECT h.harness_public_key, h.expires_at FROM harness_authorizations h
                 JOIN registrations r ON r.registration_id=h.registration_id
                 JOIN enrollments e ON e.environment_id=r.environment_id
                 WHERE h.token_hash=?1 AND h.registration_id=?2
                   AND r.environment_id=?3 AND r.active=1 AND e.enabled=1",
                params![
                    token_hash(&request.harness_key_authorization).as_slice(),
                    request.executor_registration_id,
                    environment_id
                ],
                |row| Ok((row.get::<_, String>(0)?, row.get::<_, i64>(1)?)),
            )
            .optional()?;
        let Some((expected_key, expires_at)) = row else {
            return Ok(false);
        };
        let expected_key: NoiseChannelPublicKey =
            serde_json::from_str(&expected_key).map_err(RegistryError::internal)?;
        Ok(expires_at >= now_seconds() && expected_key == request.harness_public_key)
    }

    fn executor_authorized(
        &self,
        environment_id: &str,
        registration_id: &str,
        token: &str,
    ) -> Result<bool, RegistryError> {
        let expected = self
            .db
            .query_row(
                "SELECT r.executor_token_hash FROM registrations r
                 JOIN enrollments e ON e.environment_id=r.environment_id
                 WHERE r.registration_id=?1 AND r.environment_id=?2
                   AND r.active=1 AND e.enabled=1",
                params![registration_id, environment_id],
                |row| row.get::<_, Vec<u8>>(0),
            )
            .optional()?;
        Ok(expected.is_some_and(|expected| same_token(&expected, token)))
    }

    fn harness_authorized(
        &self,
        environment_id: &str,
        registration_id: &str,
        authorization: &str,
    ) -> Result<bool, RegistryError> {
        let expires_at = self
            .db
            .query_row(
                "SELECT h.expires_at FROM harness_authorizations h
                 JOIN registrations r ON r.registration_id=h.registration_id
                 JOIN enrollments e ON e.environment_id=r.environment_id
                 WHERE h.token_hash=?1 AND h.registration_id=?2
                   AND r.environment_id=?3 AND r.active=1 AND e.enabled=1",
                params![
                    token_hash(authorization).as_slice(),
                    registration_id,
                    environment_id
                ],
                |row| row.get::<_, i64>(0),
            )
            .optional()?;
        Ok(expires_at.is_some_and(|expires_at| expires_at >= now_seconds()))
    }

    fn registration_active(
        &self,
        environment_id: &str,
        registration_id: &str,
    ) -> Result<bool, RegistryError> {
        let active = self
            .db
            .query_row(
                "SELECT 1 FROM registrations r
                 JOIN enrollments e ON e.environment_id=r.environment_id
                 WHERE r.environment_id=?1 AND r.registration_id=?2
                   AND r.active=1 AND e.enabled=1",
                params![environment_id, registration_id],
                |_| Ok(()),
            )
            .optional()?
            .is_some();
        Ok(active)
    }

    fn physical_connected(
        &mut self,
        environment_id: &str,
        registration_id: &str,
    ) -> Result<String, RegistryError> {
        if self
            .physical
            .keys()
            .any(|(environment, _)| environment == environment_id)
        {
            return Err(RegistryError::registration_conflict());
        }
        let physical_id = Uuid::new_v4().simple().to_string();
        self.physical.insert(
            (environment_id.to_string(), registration_id.to_string()),
            physical_id.clone(),
        );
        Ok(physical_id)
    }

    fn physical_disconnected(
        &mut self,
        environment_id: &str,
        registration_id: &str,
        physical_id: &str,
    ) {
        let key = (environment_id.to_string(), registration_id.to_string());
        if self
            .physical
            .get(&key)
            .is_some_and(|current| current == physical_id)
        {
            self.physical.remove(&key);
        }
    }

    fn dev_ssh_enrollment_authorized(
        &self,
        environment_id: &str,
        token: &str,
        node: bool,
    ) -> Result<bool, RegistryError> {
        let row = self
            .db
            .query_row(
                "SELECT node_token_hash, client_token_hash, enabled FROM dev_ssh_enrollments
                 WHERE environment_id=?1",
                params![environment_id],
                |row| {
                    Ok((
                        row.get::<_, Vec<u8>>(0)?,
                        row.get::<_, Vec<u8>>(1)?,
                        row.get::<_, i64>(2)?,
                    ))
                },
            )
            .optional()?;
        Ok(row.is_some_and(|(node_hash, client_hash, enabled)| {
            enabled == 1 && same_token(if node { &node_hash } else { &client_hash }, token)
        }))
    }

    fn enroll_dev_ssh(
        &mut self,
        environment_id: &str,
        tenant_id: &str,
        tokens: &DevSshEnrollmentTokens,
    ) -> Result<(), RegistryError> {
        let tx = self.db.transaction()?;
        tx.execute(
            "INSERT INTO dev_ssh_enrollments VALUES (?1, ?2, ?3, ?4, 1)
             ON CONFLICT(environment_id) DO UPDATE SET
                 tenant_id=excluded.tenant_id,
                 node_token_hash=excluded.node_token_hash,
                 client_token_hash=excluded.client_token_hash,
                 enabled=1",
            params![
                environment_id,
                tenant_id,
                token_hash(&tokens.node_token).as_slice(),
                token_hash(&tokens.client_token).as_slice()
            ],
        )?;
        tx.execute(
            "DELETE FROM dev_ssh_authorizations WHERE environment_id=?1",
            params![environment_id],
        )?;
        tx.commit()?;
        Ok(())
    }

    fn revoke_dev_ssh(&mut self, environment_id: &str) -> Result<bool, RegistryError> {
        let tx = self.db.transaction()?;
        let changed = tx.execute(
            "UPDATE dev_ssh_enrollments SET enabled=0
             WHERE environment_id=?1 AND enabled=1",
            params![environment_id],
        )?;
        tx.execute(
            "DELETE FROM dev_ssh_authorizations WHERE environment_id=?1",
            params![environment_id],
        )?;
        tx.commit()?;
        Ok(changed > 0)
    }

    fn connect_dev_ssh(
        &mut self,
        environment_id: &str,
        client_token: &str,
    ) -> Result<String, RegistryError> {
        if !self.dev_ssh_enrollment_authorized(environment_id, client_token, false)? {
            return Err(RegistryError::unauthorized("Invalid dev-ssh client token"));
        }
        let authorization = generate_token();
        self.db.execute(
            "DELETE FROM dev_ssh_authorizations WHERE expires_at<?1",
            params![now_seconds()],
        )?;
        self.db.execute(
            "INSERT INTO dev_ssh_authorizations VALUES (?1, ?2, ?3)",
            params![
                token_hash(&authorization).as_slice(),
                environment_id,
                now_seconds() + DEV_SSH_AUTHORIZATION_TTL_SECONDS
            ],
        )?;
        Ok(authorization)
    }

    fn dev_ssh_client_authorized(
        &self,
        environment_id: &str,
        authorization: &str,
    ) -> Result<bool, RegistryError> {
        let expires_at = self
            .db
            .query_row(
                "SELECT a.expires_at FROM dev_ssh_authorizations a
                 JOIN dev_ssh_enrollments e ON e.environment_id=a.environment_id
                 WHERE a.token_hash=?1 AND a.environment_id=?2 AND e.enabled=1",
                params![token_hash(authorization).as_slice(), environment_id],
                |row| row.get::<_, i64>(0),
            )
            .optional()?;
        Ok(expires_at.is_some_and(|expires_at| expires_at >= now_seconds()))
    }

    fn enrollment_authorized(
        &self,
        environment_id: &str,
        token: &str,
        node: bool,
    ) -> Result<bool, RegistryError> {
        let row = self
            .db
            .query_row(
                "SELECT node_token_hash, harness_token_hash, enabled FROM enrollments
                 WHERE environment_id=?1",
                params![environment_id],
                |row| {
                    Ok((
                        row.get::<_, Vec<u8>>(0)?,
                        row.get::<_, Vec<u8>>(1)?,
                        row.get::<_, i64>(2)?,
                    ))
                },
            )
            .optional()?;
        Ok(row.is_some_and(|(node_hash, harness_hash, enabled)| {
            enabled == 1 && same_token(if node { &node_hash } else { &harness_hash }, token)
        }))
    }
}

fn validate_identifier(value: &str, name: &'static str) -> Result<(), RegistryError> {
    if value.trim().is_empty()
        || value.len() > 256
        || value.chars().any(char::is_control)
        || value != value.trim()
    {
        return Err(RegistryError::new(
            StatusCode::BAD_REQUEST,
            "bad_request",
            format!("Invalid {name} id"),
        ));
    }
    Ok(())
}

fn generate_token() -> String {
    format!("{}{}", Uuid::new_v4().simple(), Uuid::new_v4().simple())
}

fn token_hash(token: &str) -> [u8; 32] {
    Sha256::digest(token.as_bytes()).into()
}

fn same_token(expected_hash: &[u8], token: &str) -> bool {
    constant_time_eq::constant_time_eq(expected_hash, token_hash(token).as_slice())
}

fn public_key_json(key: &NoiseChannelPublicKey) -> Result<String, RegistryError> {
    serde_json::to_string(key).map_err(RegistryError::internal)
}

fn now_seconds() -> i64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs() as i64
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn enrollment_registration_connection_and_revocation_form_one_generation() {
        let directory = tempfile::tempdir().expect("tempdir");
        let registry = Registry::open(directory.path().join("registry.sqlite3")).expect("registry");
        let tokens = registry
            .enroll("machine-a".to_string(), "tenant-a".to_string())
            .await
            .expect("enroll");
        let executor = NoiseChannelPublicKey::from_components(
            nova_exec_server_relay_protocol::NOISE_CHANNEL_SUITE,
            "eA==",
            "eA==",
        );
        let registration = registry
            .register(
                "machine-a".to_string(),
                tokens.node_token.clone(),
                EnvironmentRegistryRegistrationRequest {
                    security_profile: NOISE_RELAY_SECURITY_PROFILE.to_string(),
                    executor_public_key: executor,
                },
            )
            .await
            .expect("register");
        assert!(
            registry
                .connect(
                    "machine-a".to_string(),
                    tokens.harness_token.clone(),
                    EnvironmentRegistryConnectRequest {
                        harness_public_key: NoiseChannelPublicKey::from_components(
                            nova_exec_server_relay_protocol::NOISE_CHANNEL_SUITE,
                            "eA==",
                            "eA==",
                        ),
                    },
                )
                .await
                .is_err(),
            "offline executor must not issue harness authorization"
        );
        let physical = registry
            .physical_connected(
                registration.environment_id.clone(),
                registration.registration_id.clone(),
            )
            .await
            .expect("physical connection");
        let harness_key = NoiseChannelPublicKey::from_components(
            nova_exec_server_relay_protocol::NOISE_CHANNEL_SUITE,
            "eA==",
            "eA==",
        );
        let connection = registry
            .connect(
                "machine-a".to_string(),
                tokens.harness_token,
                EnvironmentRegistryConnectRequest {
                    harness_public_key: harness_key.clone(),
                },
            )
            .await
            .expect("connect");
        assert!(
            registry
                .validate_harness(
                    "machine-a".to_string(),
                    tokens.node_token,
                    EnvironmentRegistryHarnessKeyValidationRequest {
                        executor_registration_id: registration.registration_id,
                        harness_public_key: harness_key,
                        harness_key_authorization: connection.harness_key_authorization,
                    },
                )
                .await
                .expect("validate")
        );
        registry
            .physical_disconnected(
                "machine-a".to_string(),
                connection.registration_id,
                physical,
            )
            .await
            .expect("disconnect");
        assert!(
            registry
                .revoke("machine-a".to_string())
                .await
                .expect("revoke")
        );
    }

    struct OnlineFixture {
        _directory: tempfile::TempDir,
        registry: Registry,
        tokens: EnrollmentTokens,
        registration: RegistrationGrant,
        connection: ConnectionGrant,
        harness_key: NoiseChannelPublicKey,
    }

    fn test_key(encoded: &str) -> NoiseChannelPublicKey {
        NoiseChannelPublicKey::from_components(
            nova_exec_server_relay_protocol::NOISE_CHANNEL_SUITE,
            encoded,
            encoded,
        )
    }

    fn registration_request() -> EnvironmentRegistryRegistrationRequest {
        EnvironmentRegistryRegistrationRequest {
            security_profile: NOISE_RELAY_SECURITY_PROFILE.to_string(),
            executor_public_key: test_key("eA=="),
        }
    }

    async fn online_fixture() -> OnlineFixture {
        let directory = tempfile::tempdir().expect("tempdir");
        let registry = Registry::open(directory.path().join("registry.sqlite3")).expect("registry");
        let tokens = registry
            .enroll("machine-a".to_string(), "tenant-a".to_string())
            .await
            .expect("enroll");
        let registration = registry
            .register(
                "machine-a".to_string(),
                tokens.node_token.clone(),
                registration_request(),
            )
            .await
            .expect("register");
        registry
            .physical_connected(
                "machine-a".to_string(),
                registration.registration_id.clone(),
            )
            .await
            .expect("physical connection");
        let harness_key = test_key("eA==");
        let connection = registry
            .connect(
                "machine-a".to_string(),
                tokens.harness_token.clone(),
                EnvironmentRegistryConnectRequest {
                    harness_public_key: harness_key.clone(),
                },
            )
            .await
            .expect("connect");
        OnlineFixture {
            _directory: directory,
            registry,
            tokens,
            registration,
            connection,
            harness_key,
        }
    }

    #[tokio::test]
    async fn harness_authorization_enforces_public_key_and_expiry() {
        let fixture = online_fixture().await;
        assert!(
            fixture
                .registry
                .harness_authorized(
                    "machine-a".to_string(),
                    fixture.registration.registration_id.clone(),
                    fixture.connection.harness_key_authorization.clone(),
                )
                .await
                .expect("authorization")
        );
        assert!(
            !fixture
                .registry
                .validate_harness(
                    "machine-a".to_string(),
                    fixture.tokens.node_token.clone(),
                    EnvironmentRegistryHarnessKeyValidationRequest {
                        executor_registration_id: fixture.registration.registration_id.clone(),
                        harness_public_key: test_key("eQ=="),
                        harness_key_authorization: fixture
                            .connection
                            .harness_key_authorization
                            .clone(),
                    },
                )
                .await
                .expect("wrong key validation"),
            "the same authorization must not authenticate a different harness key"
        );

        let registration_id = fixture.registration.registration_id.clone();
        fixture
            .registry
            .with(move |store| {
                store.db.execute(
                    "UPDATE harness_authorizations SET expires_at=?1 WHERE registration_id=?2",
                    params![now_seconds() - 1, registration_id],
                )?;
                Ok(())
            })
            .await
            .expect("expire authorization");
        assert!(
            !fixture
                .registry
                .validate_harness(
                    "machine-a".to_string(),
                    fixture.tokens.node_token.clone(),
                    EnvironmentRegistryHarnessKeyValidationRequest {
                        executor_registration_id: fixture.registration.registration_id.clone(),
                        harness_public_key: fixture.harness_key.clone(),
                        harness_key_authorization: fixture
                            .connection
                            .harness_key_authorization
                            .clone(),
                    },
                )
                .await
                .expect("expired validation")
        );
        assert!(
            !fixture
                .registry
                .harness_authorized(
                    "machine-a".to_string(),
                    fixture.registration.registration_id.clone(),
                    fixture.connection.harness_key_authorization.clone(),
                )
                .await
                .expect("expired authorization")
        );
    }

    #[tokio::test]
    async fn enrollment_tokens_cannot_cross_tenant_boundaries() {
        let directory = tempfile::tempdir().expect("tempdir");
        let registry = Registry::open(directory.path().join("registry.sqlite3")).expect("registry");
        let tenant_a = registry
            .enroll("machine-a".to_string(), "tenant-a".to_string())
            .await
            .expect("tenant a");
        registry
            .enroll("machine-b".to_string(), "tenant-b".to_string())
            .await
            .expect("tenant b");
        let error = registry
            .register(
                "machine-b".to_string(),
                tenant_a.node_token,
                registration_request(),
            )
            .await
            .expect_err("another tenant's node token must not register this environment");
        assert_eq!(error.status, StatusCode::FORBIDDEN);
        let error = registry
            .connect(
                "machine-b".to_string(),
                tenant_a.harness_token,
                EnvironmentRegistryConnectRequest {
                    harness_public_key: test_key("eA=="),
                },
            )
            .await
            .expect_err("another tenant's harness token must not connect this environment");
        assert_eq!(error.status, StatusCode::FORBIDDEN);
    }

    #[tokio::test]
    async fn reenrollment_rotates_tokens_and_revocation_closes_the_generation() {
        let fixture = online_fixture().await;
        let rotated = fixture
            .registry
            .enroll("machine-a".to_string(), "tenant-a".to_string())
            .await
            .expect("rotate enrollment");
        assert!(
            !fixture
                .registry
                .registration_active(
                    "machine-a".to_string(),
                    fixture.registration.registration_id.clone(),
                )
                .await
                .expect("old registration status")
        );
        assert!(
            fixture
                .registry
                .register(
                    "machine-a".to_string(),
                    fixture.tokens.node_token.clone(),
                    registration_request(),
                )
                .await
                .is_err(),
            "the old node token must stop working after rotation"
        );
        assert!(
            fixture
                .registry
                .connect(
                    "machine-a".to_string(),
                    fixture.tokens.harness_token.clone(),
                    EnvironmentRegistryConnectRequest {
                        harness_public_key: fixture.harness_key.clone(),
                    },
                )
                .await
                .is_err(),
            "the old harness token must stop working after rotation"
        );
        let current = fixture
            .registry
            .register(
                "machine-a".to_string(),
                rotated.node_token.clone(),
                registration_request(),
            )
            .await
            .expect("rotated registration");
        assert!(
            fixture
                .registry
                .registration_active("machine-a".to_string(), current.registration_id.clone())
                .await
                .expect("current registration")
        );
        assert!(
            fixture
                .registry
                .revoke("machine-a".to_string())
                .await
                .expect("revoke")
        );
        assert!(
            !fixture
                .registry
                .registration_active("machine-a".to_string(), current.registration_id)
                .await
                .expect("revoked registration")
        );
        let error = fixture
            .registry
            .register(
                "machine-a".to_string(),
                rotated.node_token,
                registration_request(),
            )
            .await
            .expect_err("revoked enrollment must not register again");
        assert_eq!(error.status, StatusCode::FORBIDDEN);
    }

    #[tokio::test]
    async fn dev_ssh_tokens_are_scoped_and_client_grants_expire() {
        let directory = tempfile::tempdir().expect("tempdir");
        let registry = Registry::open(directory.path().join("registry.sqlite3")).expect("registry");
        let exec = registry
            .enroll("machine-a".to_string(), "tenant-a".to_string())
            .await
            .expect("exec enrollment");
        let dev = registry
            .enroll_dev_ssh("machine-a".to_string(), "tenant-a".to_string())
            .await
            .expect("dev enrollment");
        assert!(
            registry
                .dev_ssh_node_authorized("machine-a".to_string(), dev.node_token.clone())
                .await
                .expect("dev node")
        );
        assert!(
            !registry
                .dev_ssh_node_authorized("machine-a".to_string(), exec.node_token.clone())
                .await
                .expect("exec token is not a dev node token")
        );
        assert!(
            registry
                .register(
                    "machine-a".to_string(),
                    dev.node_token.clone(),
                    registration_request(),
                )
                .await
                .is_err(),
            "dev node token must not register an exec environment"
        );
        assert!(
            registry
                .connect_dev_ssh("machine-a".to_string(), exec.harness_token)
                .await
                .is_err(),
            "exec harness token must not authorize dev SSH"
        );
        let authorization = registry
            .connect_dev_ssh("machine-a".to_string(), dev.client_token.clone())
            .await
            .expect("client grant");
        assert!(
            registry
                .dev_ssh_client_authorized("machine-a".to_string(), authorization.clone())
                .await
                .expect("grant valid")
        );
        assert!(
            !registry
                .dev_ssh_client_authorized("machine-b".to_string(), authorization.clone())
                .await
                .expect("grant is environment bound")
        );
        registry
            .with(move |store| {
                store.db.execute(
                    "UPDATE dev_ssh_authorizations SET expires_at=?1 WHERE environment_id=?2",
                    params![now_seconds() - 1, "machine-a"],
                )?;
                Ok(())
            })
            .await
            .expect("expire grant");
        assert!(
            !registry
                .dev_ssh_client_authorized("machine-a".to_string(), authorization)
                .await
                .expect("expired grant")
        );
    }

    #[tokio::test]
    async fn dev_ssh_rotation_and_revocation_invalidate_grants() {
        let directory = tempfile::tempdir().expect("tempdir");
        let registry = Registry::open(directory.path().join("registry.sqlite3")).expect("registry");
        let original = registry
            .enroll_dev_ssh("machine-a".to_string(), "tenant-a".to_string())
            .await
            .expect("dev enrollment");
        let old_grant = registry
            .connect_dev_ssh("machine-a".to_string(), original.client_token.clone())
            .await
            .expect("old grant");
        let rotated = registry
            .enroll_dev_ssh("machine-a".to_string(), "tenant-a".to_string())
            .await
            .expect("rotate");
        assert!(
            !registry
                .dev_ssh_node_authorized("machine-a".to_string(), original.node_token)
                .await
                .expect("old node revoked")
        );
        assert!(
            !registry
                .dev_ssh_client_authorized("machine-a".to_string(), old_grant)
                .await
                .expect("old grant revoked")
        );
        assert!(
            registry
                .connect_dev_ssh("machine-a".to_string(), original.client_token)
                .await
                .is_err()
        );
        let current_grant = registry
            .connect_dev_ssh("machine-a".to_string(), rotated.client_token.clone())
            .await
            .expect("current grant");
        assert!(
            registry
                .revoke_dev_ssh("machine-a".to_string())
                .await
                .expect("revoke")
        );
        assert!(
            !registry
                .dev_ssh_node_authorized("machine-a".to_string(), rotated.node_token)
                .await
                .expect("node revoked")
        );
        assert!(
            !registry
                .dev_ssh_client_authorized("machine-a".to_string(), current_grant)
                .await
                .expect("grant revoked")
        );
        assert!(
            registry
                .connect_dev_ssh("machine-a".to_string(), rotated.client_token)
                .await
                .is_err()
        );
    }
}
