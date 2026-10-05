//! Configuration path validation for security-constraint rendering.
//!
//! 对位 codex `utils/path-uri/src/config_path.rs` 的校验子集
//! （`resolve_config_path*` 属于 codex config 层，未随本批移植）。

use crate::LegacyAppPathString;
use crate::LegacyAppPathStringError;
use crate::PathConvention;
use crate::PathUri;
use crate::PathUriParseError;

impl PathUri {
    /// Checks that a literal directory can be inserted into a glob unchanged.
    /// Rejects metacharacters instead of turning directory names into patterns.
    /// POSIX backslashes would escape the following pattern character.
    pub fn validate_glob_directory(
        &self,
        convention: PathConvention,
    ) -> Result<(), LegacyAppPathStringError> {
        self.validate_config_path(convention)?;
        let path = LegacyAppPathString::from_path_uri(self, convention)?.into_string();
        if contains_glob_metacharacter(&path)
            || convention == PathConvention::Posix && path.contains('\\')
        {
            return Err(LegacyAppPathStringError::UnsupportedConfigPath { path, convention });
        }
        Ok(())
    }

    /// Checks that a resolved configuration URI has a lossless native spelling
    /// in the owning executor's convention. Opaque and ambiguous paths fail.
    pub fn validate_config_path(
        &self,
        convention: PathConvention,
    ) -> Result<(), LegacyAppPathStringError> {
        if self.infer_path_convention() != Some(convention) {
            return Err(LegacyAppPathStringError::IncompatibleConvention {
                path: self.to_string(),
                convention,
            });
        }
        let bytes = self.decoded_path_bytes();
        if self.lexical_depth().is_none()
            || bytes.contains(&0)
            || std::str::from_utf8(&bytes).is_err()
        {
            return Err(PathUriParseError::InvalidFileUriPath {
                path: self.to_string(),
            }
            .into());
        }
        Self::validate_config_path_text(
            LegacyAppPathString::from_path_uri(self, convention)?.as_str(),
            convention,
        )
    }

    /// Validates native configuration text without resolving paths or glob syntax.
    /// Rejects spellings that could change targets during later native conversion.
    pub fn validate_config_path_text(
        input: &str,
        convention: PathConvention,
    ) -> Result<(), LegacyAppPathStringError> {
        let namespace_alias =
            nova_exec_server_utils_absolute_path::normalize_windows_device_path(input);
        let native_input = namespace_alias.as_deref().unwrap_or(input);
        let has_windows_component_colon = convention == PathConvention::Windows
            && convention
                .path_segments(native_input)
                .enumerate()
                .any(|(index, segment)| {
                    let segment = if index == 0
                        && segment
                            .as_bytes()
                            .first()
                            .is_some_and(u8::is_ascii_alphabetic)
                        && segment.as_bytes().get(/*index*/ 1) == Some(&b':')
                    {
                        &segment[2..]
                    } else {
                        segment
                    };
                    segment.contains(':')
                });
        let mixed_home_separators = convention == PathConvention::Windows
            && convention
                .home_relative_suffix(input)
                .is_some_and(|suffix| {
                    suffix.starts_with('/') && suffix.trim_start_matches('/').starts_with('\\')
                        || suffix.starts_with('\\')
                            && suffix.trim_start_matches('\\').starts_with('/')
                });
        let bare_windows_drive = convention == PathConvention::Windows
            && matches!(native_input.as_bytes(), [drive, b':'] if drive.is_ascii_alphabetic());
        if input.contains('\0')
            || has_windows_component_colon
            || mixed_home_separators
            || bare_windows_drive
        {
            return Err(LegacyAppPathStringError::UnsupportedConfigPath {
                path: input.to_string(),
                convention,
            });
        }
        Ok(())
    }
}

fn contains_glob_metacharacter(path: &str) -> bool {
    path.chars()
        .any(|character| matches!(character, '*' | '?' | '[' | ']' | '{' | '}'))
}
