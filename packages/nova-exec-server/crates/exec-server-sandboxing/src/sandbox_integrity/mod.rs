//! 对位 codex 11da6b9edc `sandboxing/src/sandbox_integrity/`：基于策略的文件
//! 内容完整性检查件（PreparedPolicy/FileContentsChecker/IntegrityFinding）。

mod contents;
mod finding;
mod policy;

pub use contents::FileContentsChecker;
pub use finding::IntegrityFinding;
pub use finding::IntegrityFindingDetails;
