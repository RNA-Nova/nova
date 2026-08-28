use base64::Engine as _;
use base64::engine::general_purpose::STANDARD;
use nova_executor_protocol::JSONRPCErrorError;
use nova_executor_utils_path_uri::PathUri;
use tokio::io;

use crate::CopyOptions;
use crate::CreateDirectoryOptions;
use crate::ExecServerRuntimePaths;
use crate::ExecutorFileSystem;
use crate::ExecutorFileSystemFuture;
use crate::FileMetadata;
use crate::FileSystemReadStream;
use crate::FileSystemResult;
use crate::FileSystemSandboxContext;
use crate::GetMetadataOptions;
use crate::ReadDirectoryEntry;
use crate::ReadFileOptions;
use crate::RemoveOptions;
use crate::WalkOptions;
use crate::WalkOutcome;
use crate::WriteFileOptions;
use crate::fs_helper::FsHelperPayload;
use crate::fs_helper::FsHelperRequest;
use crate::fs_sandbox::FileSystemSandboxRunner;
use crate::fs_sandbox::SandboxFsHelperWriteStream;
use crate::protocol::FsCanonicalizeParams;
use crate::protocol::FsCopyParams;
use crate::protocol::FsCreateDirectoryParams;
use crate::protocol::FsGetMetadataParams;
use crate::protocol::FsReadDirectoryParams;
use crate::protocol::FsReadFileParams;
use crate::protocol::FsRemoveParams;
use crate::protocol::FsWalkParams;
use crate::protocol::FsWriteFileParams;
use crate::protocol::FsWriteStreamParams;

#[derive(Clone)]
pub struct SandboxedFileSystem {
    sandbox_runner: FileSystemSandboxRunner,
}

impl SandboxedFileSystem {
    pub fn new(runtime_paths: ExecServerRuntimePaths) -> Self {
        Self {
            sandbox_runner: FileSystemSandboxRunner::new(runtime_paths),
        }
    }

    async fn run_sandboxed(
        &self,
        sandbox: &FileSystemSandboxContext,
        request: FsHelperRequest,
    ) -> FileSystemResult<FsHelperPayload> {
        self.sandbox_runner
            .run(sandbox, request)
            .await
            .map_err(map_sandbox_error)
    }

    /// 沙箱化开门（fs/readStream 的读端）：一次性 helper 在平台沙箱内 open
    /// 目标文件后把 fd/handle 传回 executor（Unix 经 SCM_RIGHTS、Windows 经
    /// 句柄复制，见 [`crate::sandboxed_file_open`]），executor 自持句柄读文件。
    pub(crate) async fn open_file_for_read(
        &self,
        path: &PathUri,
        sandbox: Option<&FileSystemSandboxContext>,
    ) -> FileSystemResult<tokio::fs::File> {
        let sandbox = require_platform_sandbox(sandbox)?;
        validate_native_path(path)?;
        // helper 进程自身已在沙箱内运行，开门请求不再携带沙箱上下文
        let command = self
            .sandbox_runner
            .prepare_command(sandbox)
            .map_err(map_sandbox_error)?;
        crate::sandboxed_file_open::open(command, path.clone())
            .await
            .map_err(map_sandbox_error)
    }

    /// 为 fs/writeStream 启动长命沙箱 helper：helper 进程在平台沙箱内持续写
    /// 文件，executor 逐行转发 chunk/finish 事件帧并收最终确认（线上
    /// writeStream 三件套形状不变）。
    pub(crate) async fn spawn_write_stream(
        &self,
        params: &FsWriteStreamParams,
    ) -> FileSystemResult<SandboxFsHelperWriteStream> {
        let sandbox = require_platform_sandbox(params.sandbox.as_ref())?;
        validate_native_path(&params.path)?;
        // helper 进程自身已在沙箱内运行，内部请求不再携带沙箱上下文
        let mut helper_params = params.clone();
        helper_params.sandbox = None;
        self.sandbox_runner
            .spawn_streaming_write(sandbox, FsHelperRequest::WriteStream(helper_params))
            .await
            .map_err(map_sandbox_error)
    }
}

impl SandboxedFileSystem {
    async fn canonicalize(
        &self,
        path: &PathUri,
        sandbox: Option<&FileSystemSandboxContext>,
    ) -> FileSystemResult<PathUri> {
        let sandbox = require_platform_sandbox(sandbox)?;
        validate_native_path(path)?;
        let response = self
            .run_sandboxed(
                sandbox,
                FsHelperRequest::Canonicalize(FsCanonicalizeParams {
                    path: path.clone(),
                    sandbox: None,
                }),
            )
            .await?
            .expect_canonicalize()
            .map_err(map_sandbox_error)?;
        Ok(response.path)
    }

    async fn read_file(
        &self,
        path: &PathUri,
        options: ReadFileOptions,
        sandbox: Option<&FileSystemSandboxContext>,
    ) -> FileSystemResult<Vec<u8>> {
        let sandbox = require_platform_sandbox(sandbox)?;
        validate_native_path(path)?;
        let response = self
            .run_sandboxed(
                sandbox,
                FsHelperRequest::ReadFile(FsReadFileParams {
                    path: path.clone(),
                    follow_symlinks: (!options.follow_symlinks).then_some(false),
                    sandbox: None,
                }),
            )
            .await?
            .expect_read_file()
            .map_err(map_sandbox_error)?;
        STANDARD.decode(response.data_base64).map_err(|err| {
            io::Error::new(
                io::ErrorKind::InvalidData,
                format!("fs/readFile returned invalid base64 dataBase64: {err}"),
            )
        })
    }

    async fn write_file(
        &self,
        path: &PathUri,
        contents: Vec<u8>,
        options: WriteFileOptions,
        sandbox: Option<&FileSystemSandboxContext>,
    ) -> FileSystemResult<()> {
        let sandbox = require_platform_sandbox(sandbox)?;
        validate_native_path(path)?;
        self.run_sandboxed(
            sandbox,
            FsHelperRequest::WriteFile(FsWriteFileParams {
                path: path.clone(),
                data_base64: STANDARD.encode(contents),
                follow_symlinks: (!options.follow_symlinks).then_some(false),
                sandbox: None,
            }),
        )
        .await?
        .expect_write_file()
        .map_err(map_sandbox_error)?;
        Ok(())
    }

    async fn create_directory(
        &self,
        path: &PathUri,
        options: CreateDirectoryOptions,
        sandbox: Option<&FileSystemSandboxContext>,
    ) -> FileSystemResult<()> {
        let sandbox = require_platform_sandbox(sandbox)?;
        validate_native_path(path)?;
        self.run_sandboxed(
            sandbox,
            FsHelperRequest::CreateDirectory(FsCreateDirectoryParams {
                path: path.clone(),
                recursive: Some(options.recursive),
                follow_symlinks: (!options.follow_symlinks).then_some(false),
                sandbox: None,
            }),
        )
        .await?
        .expect_create_directory()
        .map_err(map_sandbox_error)?;
        Ok(())
    }

    async fn get_metadata(
        &self,
        path: &PathUri,
        options: GetMetadataOptions,
        sandbox: Option<&FileSystemSandboxContext>,
    ) -> FileSystemResult<FileMetadata> {
        let sandbox = require_platform_sandbox(sandbox)?;
        validate_native_path(path)?;
        let response = self
            .run_sandboxed(
                sandbox,
                FsHelperRequest::GetMetadata(FsGetMetadataParams {
                    path: path.clone(),
                    follow_symlinks: (!options.follow_symlinks).then_some(false),
                    sandbox: None,
                }),
            )
            .await?
            .expect_get_metadata()
            .map_err(map_sandbox_error)?;
        Ok(FileMetadata {
            is_directory: response.is_directory,
            is_file: response.is_file,
            is_symlink: response.is_symlink,
            size: response.size,
            created_at_ms: response.created_at_ms,
            modified_at_ms: response.modified_at_ms,
        })
    }

    async fn read_directory(
        &self,
        path: &PathUri,
        sandbox: Option<&FileSystemSandboxContext>,
    ) -> FileSystemResult<Vec<ReadDirectoryEntry>> {
        let sandbox = require_platform_sandbox(sandbox)?;
        validate_native_path(path)?;
        let response = self
            .run_sandboxed(
                sandbox,
                FsHelperRequest::ReadDirectory(FsReadDirectoryParams {
                    path: path.clone(),
                    sandbox: None,
                }),
            )
            .await?
            .expect_read_directory()
            .map_err(map_sandbox_error)?;
        Ok(response
            .entries
            .into_iter()
            .map(|entry| ReadDirectoryEntry {
                file_name: entry.file_name,
                is_directory: entry.is_directory,
                is_file: entry.is_file,
            })
            .collect())
    }

    async fn walk(
        &self,
        path: &PathUri,
        options: WalkOptions,
        sandbox: Option<&FileSystemSandboxContext>,
    ) -> FileSystemResult<WalkOutcome> {
        let sandbox = require_platform_sandbox(sandbox)?;
        validate_native_path(path)?;
        let response = self
            .run_sandboxed(
                sandbox,
                FsHelperRequest::Walk(FsWalkParams {
                    path: path.clone(),
                    options,
                    sandbox: None,
                }),
            )
            .await?
            .expect_walk()
            .map_err(map_sandbox_error)?;
        Ok(response)
    }

    async fn remove(
        &self,
        path: &PathUri,
        remove_options: RemoveOptions,
        sandbox: Option<&FileSystemSandboxContext>,
    ) -> FileSystemResult<()> {
        let sandbox = require_platform_sandbox(sandbox)?;
        validate_native_path(path)?;
        self.run_sandboxed(
            sandbox,
            FsHelperRequest::Remove(FsRemoveParams {
                path: path.clone(),
                recursive: Some(remove_options.recursive),
                force: Some(remove_options.force),
                follow_symlinks: (!remove_options.follow_symlinks).then_some(false),
                sandbox: None,
            }),
        )
        .await?
        .expect_remove()
        .map_err(map_sandbox_error)?;
        Ok(())
    }

    async fn copy(
        &self,
        source_path: &PathUri,
        destination_path: &PathUri,
        options: CopyOptions,
        sandbox: Option<&FileSystemSandboxContext>,
    ) -> FileSystemResult<()> {
        let sandbox = require_platform_sandbox(sandbox)?;
        validate_native_path(source_path)?;
        validate_native_path(destination_path)?;
        self.run_sandboxed(
            sandbox,
            FsHelperRequest::Copy(FsCopyParams {
                source_path: source_path.clone(),
                destination_path: destination_path.clone(),
                recursive: options.recursive,
                sandbox: None,
            }),
        )
        .await?
        .expect_copy()
        .map_err(map_sandbox_error)?;
        Ok(())
    }
}

impl ExecutorFileSystem for SandboxedFileSystem {
    fn canonicalize<'a>(
        &'a self,
        path: &'a PathUri,
        sandbox: Option<&'a FileSystemSandboxContext>,
    ) -> ExecutorFileSystemFuture<'a, PathUri> {
        Box::pin(SandboxedFileSystem::canonicalize(self, path, sandbox))
    }

    fn read_file<'a>(
        &'a self,
        path: &'a PathUri,
        options: ReadFileOptions,
        sandbox: Option<&'a FileSystemSandboxContext>,
    ) -> ExecutorFileSystemFuture<'a, Vec<u8>> {
        Box::pin(SandboxedFileSystem::read_file(self, path, options, sandbox))
    }

    fn read_file_stream<'a>(
        &'a self,
        _path: &'a PathUri,
        _sandbox: Option<&'a FileSystemSandboxContext>,
    ) -> ExecutorFileSystemFuture<'a, FileSystemReadStream> {
        // 注：RPC 层的 fs/readStream 已支持平台沙箱（开门 fd 传递，
        // 见 open_file_for_read）；这里不支持的是 FileSystemReadStream 这一
        // 进程内流抽象。
        Box::pin(async {
            Err(io::Error::new(
                io::ErrorKind::Unsupported,
                "streaming file reads do not support platform sandboxing",
            ))
        })
    }

    fn write_file<'a>(
        &'a self,
        path: &'a PathUri,
        contents: Vec<u8>,
        options: WriteFileOptions,
        sandbox: Option<&'a FileSystemSandboxContext>,
    ) -> ExecutorFileSystemFuture<'a, ()> {
        Box::pin(SandboxedFileSystem::write_file(
            self, path, contents, options, sandbox,
        ))
    }

    fn create_directory<'a>(
        &'a self,
        path: &'a PathUri,
        options: CreateDirectoryOptions,
        sandbox: Option<&'a FileSystemSandboxContext>,
    ) -> ExecutorFileSystemFuture<'a, ()> {
        Box::pin(SandboxedFileSystem::create_directory(
            self, path, options, sandbox,
        ))
    }

    fn get_metadata<'a>(
        &'a self,
        path: &'a PathUri,
        options: GetMetadataOptions,
        sandbox: Option<&'a FileSystemSandboxContext>,
    ) -> ExecutorFileSystemFuture<'a, FileMetadata> {
        Box::pin(SandboxedFileSystem::get_metadata(
            self, path, options, sandbox,
        ))
    }

    fn read_directory<'a>(
        &'a self,
        path: &'a PathUri,
        sandbox: Option<&'a FileSystemSandboxContext>,
    ) -> ExecutorFileSystemFuture<'a, Vec<ReadDirectoryEntry>> {
        Box::pin(SandboxedFileSystem::read_directory(self, path, sandbox))
    }

    fn walk<'a>(
        &'a self,
        path: &'a PathUri,
        options: WalkOptions,
        sandbox: Option<&'a FileSystemSandboxContext>,
    ) -> ExecutorFileSystemFuture<'a, WalkOutcome> {
        Box::pin(SandboxedFileSystem::walk(self, path, options, sandbox))
    }

    fn remove<'a>(
        &'a self,
        path: &'a PathUri,
        remove_options: RemoveOptions,
        sandbox: Option<&'a FileSystemSandboxContext>,
    ) -> ExecutorFileSystemFuture<'a, ()> {
        Box::pin(SandboxedFileSystem::remove(
            self,
            path,
            remove_options,
            sandbox,
        ))
    }

    fn copy<'a>(
        &'a self,
        source_path: &'a PathUri,
        destination_path: &'a PathUri,
        options: CopyOptions,
        sandbox: Option<&'a FileSystemSandboxContext>,
    ) -> ExecutorFileSystemFuture<'a, ()> {
        Box::pin(SandboxedFileSystem::copy(
            self,
            source_path,
            destination_path,
            options,
            sandbox,
        ))
    }
}

fn validate_native_path(path: &PathUri) -> FileSystemResult<()> {
    path.to_abs_path().map(drop)
}

fn require_platform_sandbox(
    sandbox: Option<&FileSystemSandboxContext>,
) -> FileSystemResult<&FileSystemSandboxContext> {
    sandbox
        .filter(|sandbox| sandbox.should_run_in_sandbox())
        .ok_or_else(|| {
            io::Error::new(
                io::ErrorKind::InvalidInput,
                "sandboxed filesystem operations require ReadOnly or WorkspaceWrite sandbox policy",
            )
        })
}

pub(crate) fn map_sandbox_error(error: JSONRPCErrorError) -> io::Error {
    match error.code {
        -32004 => io::Error::new(io::ErrorKind::NotFound, error.message),
        -32600 => io::Error::new(io::ErrorKind::InvalidInput, error.message),
        _ => io::Error::other(error.message),
    }
}

#[cfg(all(test, any(unix, windows)))]
#[path = "sandboxed_file_system_path_uri_tests.rs"]
mod path_uri_tests;
