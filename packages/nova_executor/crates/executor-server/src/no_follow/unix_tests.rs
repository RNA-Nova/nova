//! no-follow 语义的 unix 行为测试（对齐 codex exec-server tests/file_system_unix.rs
//! 的 local 变体）：经 `LocalFileSystem::unsandboxed()` 走完整 options 穿透链，
//! 符号链接在任意路径组件出现即报错，直读/直写真实文件不受影响。

use std::ffi::CString;
use std::os::unix::ffi::OsStrExt;
use std::os::unix::fs::FileTypeExt;
use std::os::unix::fs::PermissionsExt;
use std::os::unix::fs::symlink;
use std::os::unix::net::UnixListener;
use std::path::Path;
use std::sync::Arc;
use std::time::Duration;

use nova_executor_utils_path_uri::PathUri;
use pretty_assertions::assert_eq;
use tempfile::TempDir;
use tokio::time::timeout;

use crate::CreateDirectoryOptions;
use crate::ExecutorFileSystem;
use crate::GetMetadataOptions;
use crate::ReadFileOptions;
use crate::RemoveOptions;
use crate::WriteFileOptions;
use crate::local_file_system::LocalFileSystem;

fn unsandboxed() -> LocalFileSystem {
    LocalFileSystem::unsandboxed()
}

fn uri(path: &Path) -> PathUri {
    PathUri::from_host_native_path(path).expect("path URI")
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn no_follow_operations_reject_symlinks_in_any_path_component() {
    let file_system = unsandboxed();
    let tmp = TempDir::new().expect("tempdir");
    // macOS 的临时目录本身经 /var -> /private/var 符号链接，先规范化
    let tmp_path = tmp.path().canonicalize().expect("canonical tempdir");
    let real = tmp_path.join("real");
    std::fs::create_dir(&real).expect("real dir");
    let existing = real.join("existing.txt");
    std::fs::write(&existing, "unchanged").expect("existing file");
    let removable = real.join("removable.txt");
    std::fs::write(&removable, "keep").expect("removable file");
    let directory_link = tmp_path.join("directory-link");
    symlink(&real, &directory_link).expect("directory symlink");
    let file_link = tmp_path.join("file-link");
    symlink(&existing, &file_link).expect("file symlink");

    let no_follow_read = ReadFileOptions {
        follow_symlinks: false,
    };
    let no_follow_write = WriteFileOptions {
        follow_symlinks: false,
    };
    let no_follow_metadata = GetMetadataOptions {
        follow_symlinks: false,
    };
    let no_follow_create = CreateDirectoryOptions {
        recursive: true,
        follow_symlinks: false,
    };
    let no_follow_remove = RemoveOptions {
        recursive: false,
        force: false,
        follow_symlinks: false,
    };

    // 叶子是符号链接：读/写/元数据/删除全部拒绝
    assert!(
        file_system
            .read_file(&uri(&file_link), no_follow_read, /*sandbox*/ None)
            .await
            .is_err()
    );
    // 中间组件是符号链接：同样拒绝
    assert!(
        file_system
            .read_file(
                &uri(&directory_link.join("existing.txt")),
                no_follow_read,
                /*sandbox*/ None,
            )
            .await
            .is_err()
    );
    assert!(
        file_system
            .write_file(
                &uri(&file_link),
                b"changed".to_vec(),
                no_follow_write,
                /*sandbox*/ None,
            )
            .await
            .is_err()
    );
    assert_eq!(
        std::fs::read_to_string(&existing).expect("read existing"),
        "unchanged"
    );
    assert!(
        file_system
            .write_file(
                &uri(&directory_link.join("existing.txt")),
                b"changed".to_vec(),
                no_follow_write,
                /*sandbox*/ None,
            )
            .await
            .is_err()
    );
    assert_eq!(
        std::fs::read_to_string(&existing).expect("read existing"),
        "unchanged"
    );
    assert!(
        file_system
            .get_metadata(&uri(&file_link), no_follow_metadata, /*sandbox*/ None)
            .await
            .is_err()
    );
    // 真实目录的 no-follow 元数据正常返回
    let directory_metadata = file_system
        .get_metadata(&uri(&real), no_follow_metadata, /*sandbox*/ None)
        .await
        .expect("directory metadata");
    assert!(directory_metadata.is_directory);
    assert!(
        file_system
            .create_directory(
                &uri(&directory_link.join("created")),
                no_follow_create,
                /*sandbox*/ None,
            )
            .await
            .is_err()
    );
    assert!(!real.join("created").exists());
    assert!(
        file_system
            .remove(
                &uri(&directory_link.join("removable.txt")),
                no_follow_remove,
                /*sandbox*/ None,
            )
            .await
            .is_err()
    );
    assert!(removable.exists());
    assert!(
        file_system
            .remove(&uri(&file_link), no_follow_remove, /*sandbox*/ None)
            .await
            .is_err()
    );
    assert!(
        file_link
            .symlink_metadata()
            .expect("symlink metadata")
            .file_type()
            .is_symlink()
    );
}

#[tokio::test]
async fn no_follow_non_recursive_root_creation_fails() {
    let file_system = unsandboxed();
    let result = file_system
        .create_directory(
            &uri(Path::new("/")),
            CreateDirectoryOptions {
                recursive: false,
                follow_symlinks: false,
            },
            /*sandbox*/ None,
        )
        .await;

    assert!(result.is_err());
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn no_follow_operations_support_search_only_ancestors() {
    let file_system = unsandboxed();
    let tmp = TempDir::new().expect("tempdir");
    let root = tmp.path().canonicalize().expect("canonical tempdir");
    let search_only = root.join("search-only");
    std::fs::create_dir(&search_only).expect("search-only dir");
    let existing = search_only.join("existing.txt");
    std::fs::write(&existing, "before").expect("existing file");
    let unreadable = search_only.join("unreadable.txt");
    std::fs::write(&unreadable, "metadata only").expect("unreadable file");
    std::fs::set_permissions(&unreadable, std::fs::Permissions::from_mode(0o000))
        .expect("chmod unreadable");
    let socket_path = search_only.join("socket");
    let _socket = UnixListener::bind(&socket_path).expect("bind socket");
    let removable = search_only.join("removable.txt");
    std::fs::write(&removable, "remove").expect("removable file");
    // 目录仅可搜索（不可读不可写）：openat 逐组件下潜只需 execute 位
    std::fs::set_permissions(&search_only, std::fs::Permissions::from_mode(0o300))
        .expect("chmod search-only");

    let result = async {
        let root_metadata = file_system
            .get_metadata(
                &uri(Path::new("/")),
                GetMetadataOptions {
                    follow_symlinks: false,
                },
                /*sandbox*/ None,
            )
            .await?;
        assert!(root_metadata.is_directory);

        assert_eq!(
            file_system
                .read_file(
                    &uri(&existing),
                    ReadFileOptions {
                        follow_symlinks: false,
                    },
                    /*sandbox*/ None,
                )
                .await?,
            b"before"
        );
        file_system
            .write_file(
                &uri(&existing),
                b"after".to_vec(),
                WriteFileOptions {
                    follow_symlinks: false,
                },
                /*sandbox*/ None,
            )
            .await?;
        assert_eq!(
            std::fs::read_to_string(&existing).expect("read existing"),
            "after"
        );

        // 无读权限的文件与 socket 的元数据仍可取（只需祖先搜索位）
        for metadata_path in [&unreadable, &socket_path] {
            file_system
                .get_metadata(
                    &uri(metadata_path),
                    GetMetadataOptions {
                        follow_symlinks: false,
                    },
                    /*sandbox*/ None,
                )
                .await?;
        }

        let nested = search_only.join("created").join("nested");
        file_system
            .create_directory(
                &uri(&nested),
                CreateDirectoryOptions {
                    recursive: true,
                    follow_symlinks: false,
                },
                /*sandbox*/ None,
            )
            .await?;
        assert!(nested.is_dir());

        file_system
            .remove(
                &uri(&removable),
                RemoveOptions {
                    recursive: false,
                    force: false,
                    follow_symlinks: false,
                },
                /*sandbox*/ None,
            )
            .await?;
        assert!(!removable.exists());
        std::io::Result::Ok(())
    }
    .await;

    std::fs::set_permissions(&search_only, std::fs::Permissions::from_mode(0o700))
        .expect("restore search-only permissions");
    std::fs::set_permissions(&unreadable, std::fs::Permissions::from_mode(0o600))
        .expect("restore unreadable permissions");
    result.expect("no-follow operations over search-only ancestors");
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn no_follow_write_rejects_fifo_without_blocking() {
    let file_system = unsandboxed();
    let tmp = TempDir::new().expect("tempdir");
    let fifo = tmp
        .path()
        .canonicalize()
        .expect("canonical tempdir")
        .join("fifo");
    let fifo_c = CString::new(fifo.as_os_str().as_bytes()).expect("fifo cstring");
    // SAFETY: mkfifo 仅创建文件系统节点，参数均有效
    if unsafe { libc::mkfifo(fifo_c.as_ptr(), 0o600) } != 0 {
        panic!("mkfifo failed: {}", std::io::Error::last_os_error());
    }

    let result = timeout(
        Duration::from_secs(1),
        file_system.write_file(
            &uri(&fifo),
            b"must not be written".to_vec(),
            WriteFileOptions {
                follow_symlinks: false,
            },
            /*sandbox*/ None,
        ),
    )
    .await
    .expect("strict FIFO write must not block");
    assert!(result.is_err());
    assert!(
        fifo.symlink_metadata()
            .expect("fifo metadata")
            .file_type()
            .is_fifo()
    );
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn no_follow_recursive_mkdir_handles_concurrent_creators() {
    let file_system = Arc::new(unsandboxed());
    let tmp = TempDir::new().expect("tempdir");
    let path = tmp
        .path()
        .canonicalize()
        .expect("canonical tempdir")
        .join("shared")
        .join("nested");
    let path_uri = uri(&path);
    let barrier = Arc::new(tokio::sync::Barrier::new(16));
    let mut tasks = Vec::new();
    for _ in 0..16 {
        let file_system = Arc::clone(&file_system);
        let path_uri = path_uri.clone();
        let barrier = Arc::clone(&barrier);
        tasks.push(tokio::spawn(async move {
            barrier.wait().await;
            file_system
                .create_directory(
                    &path_uri,
                    CreateDirectoryOptions {
                        recursive: true,
                        follow_symlinks: false,
                    },
                    /*sandbox*/ None,
                )
                .await
        }));
    }
    for task in tasks {
        task.await
            .expect("mkdir task should not panic")
            .expect("concurrent no-follow mkdir should succeed");
    }
    assert!(path.is_dir());
}

#[cfg(target_os = "linux")]
#[tokio::test]
async fn no_follow_metadata_preserves_linux_birthtime() {
    let file_system = unsandboxed();
    let tmp = TempDir::new().expect("tempdir");
    let file = tmp.path().join("created.txt");
    std::fs::write(&file, "created").expect("created file");
    let expected = match std::fs::metadata(&file).expect("metadata").created() {
        Ok(created) => Some(
            i64::try_from(
                created
                    .duration_since(std::time::UNIX_EPOCH)
                    .expect("creation time after epoch")
                    .as_millis(),
            )
            .expect("creation time fits in i64"),
        ),
        Err(error) if error.kind() == std::io::ErrorKind::Unsupported => None,
        Err(error) => panic!("unexpected creation time error: {error}"),
    };

    let metadata = file_system
        .get_metadata(
            &uri(&file),
            GetMetadataOptions {
                follow_symlinks: false,
            },
            /*sandbox*/ None,
        )
        .await
        .expect("no-follow metadata");

    if let Some(expected) = expected {
        assert!(expected > 0);
        assert_eq!(metadata.created_at_ms, expected);
    } else {
        assert_eq!(metadata.created_at_ms, 0);
    }
}
