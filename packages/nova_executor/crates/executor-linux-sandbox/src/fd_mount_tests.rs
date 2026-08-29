use super::verify_fd_mounts;
use pretty_assertions::assert_eq;
use std::fs::File;
use std::os::fd::IntoRawFd;
use std::os::unix::fs::MetadataExt;
use std::path::Path;

/// 记录 descriptor 原始指向的文件身份（设备号 + inode）。
fn descriptor_identity(file: &File) -> (u64, u64) {
    let metadata = file
        .metadata()
        .expect("descriptor target should be statable");
    (metadata.dev() as u64, metadata.ino() as u64)
}

/// 断言 descriptor 已被关闭。
///
/// fd 是进程级资源：并行的其他测试可能在断言前重新占用同一 fd 号，因此
/// fcntl 显示存活时还要确认它不再指向受检文件——仍指向才是真正的泄漏。
fn assert_descriptor_closed(descriptor: libc::c_int, original: (u64, u64)) {
    if unsafe { libc::fcntl(descriptor, libc::F_GETFD) } < 0 {
        assert_eq!(
            std::io::Error::last_os_error().raw_os_error(),
            Some(libc::EBADF)
        );
        return;
    }
    let mut stat: libc::stat = unsafe { std::mem::zeroed() };
    let rc = unsafe { libc::fstat(descriptor, &mut stat) };
    assert_eq!(rc, 0, "reused descriptor should remain statable");
    assert_ne!(
        (stat.st_dev as u64, stat.st_ino as u64),
        original,
        "descriptor should be closed, not still pointing at the original file"
    );
}

/// A matching mount authenticates its original inode and closes the inherited descriptor.
#[test]
fn matching_mount_closes_inherited_descriptor() {
    let root = tempfile::tempdir().expect("temporary directory should be created");
    let file = File::open(root.path()).expect("directory descriptor should open");
    let original = descriptor_identity(&file);
    let descriptor = file.into_raw_fd();
    let marker = format!("{descriptor}:{}", root.path().display());

    verify_fd_mounts(&[marker]).expect("matching mount should verify");

    assert_descriptor_closed(descriptor, original);
}

/// A swapped destination fails closed without leaking the original descriptor.
#[test]
fn mismatched_mount_closes_inherited_descriptor() {
    let source = tempfile::tempdir().expect("source directory should be created");
    let destination = tempfile::tempdir().expect("destination directory should be created");
    let file = File::open(source.path()).expect("source directory descriptor should open");
    let original = descriptor_identity(&file);
    let descriptor = file.into_raw_fd();
    let marker = format!("{descriptor}:{}", destination.path().display());

    let error = verify_fd_mounts(&[marker]).expect_err("different inodes must be rejected");

    assert_eq!(error.kind(), std::io::ErrorKind::PermissionDenied);
    assert_descriptor_closed(descriptor, original);
}

/// A symlink to the original inode is not itself the authenticated mount.
#[test]
fn symlinked_mount_destination_closes_inherited_descriptor() {
    let root = tempfile::tempdir().expect("temporary directory should be created");
    let source = root.path().join("source");
    let destination = root.path().join("destination");
    std::fs::create_dir(&source).expect("source directory should be created");
    std::os::unix::fs::symlink(&source, &destination)
        .expect("mount destination symlink should be created");
    let file = File::open(&source).expect("source directory descriptor should open");
    let original = descriptor_identity(&file);
    let descriptor = file.into_raw_fd();
    let marker = format!("{descriptor}:{}", destination.display());

    let error = verify_fd_mounts(&[marker]).expect_err("symlinked mounts must be rejected");

    assert_eq!(error.kind(), std::io::ErrorKind::PermissionDenied);
    assert_descriptor_closed(descriptor, original);
}

/// Malformed mount markers cannot claim standard streams or relative destinations.
#[test]
fn malformed_mount_markers_are_rejected() {
    for marker in [
        "missing-separator",
        "invalid:/tmp",
        "0:/tmp",
        "1:/tmp",
        "2:/tmp",
    ] {
        let error = verify_fd_mounts(&[marker.to_string()])
            .expect_err("malformed mount marker must be rejected");
        assert_eq!(error.kind(), std::io::ErrorKind::InvalidInput);
    }

    let root = tempfile::tempdir().expect("temporary directory should be created");
    let file = File::open(root.path()).expect("directory descriptor should open");
    let original = descriptor_identity(&file);
    let descriptor = file.into_raw_fd();
    let error = verify_fd_mounts(&[format!("{descriptor}:relative")])
        .expect_err("relative mount destinations must be rejected");

    assert_eq!(error.kind(), std::io::ErrorKind::InvalidInput);
    assert_descriptor_closed(descriptor, original);
}

/// A transferred descriptor can be consumed only once even if a marker is repeated.
#[test]
fn duplicate_mount_descriptors_are_rejected() {
    let root = tempfile::tempdir().expect("temporary directory should be created");
    let file = File::open(root.path()).expect("directory descriptor should open");
    let original = descriptor_identity(&file);
    let descriptor = file.into_raw_fd();
    let marker = format!("{descriptor}:{}", root.path().display());

    let error = verify_fd_mounts(&[marker.clone(), marker])
        .expect_err("a mount descriptor must not be consumed twice");

    assert_eq!(error.kind(), std::io::ErrorKind::InvalidInput);
    assert_descriptor_closed(descriptor, original);
}
