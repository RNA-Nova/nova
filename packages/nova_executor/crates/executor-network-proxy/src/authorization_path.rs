/// 判断 `path` 在授权语义下是否有无歧义的解释。
///
/// （预留）MITM hook 在上游服务器解析之前就要对请求授权，因此必须拒绝那些
/// 常见上游可能解码或归一化成另一种资源的路径形态。当前 MITM 面尚未移植
/// （见 lib.rs 裁点说明），该函数作为纯函数先行落地，供后续批次使用。
#[allow(dead_code)]
pub(crate) fn is_safe_for_authorization(path: &str) -> bool {
    path.split('/').all(is_safe_segment_for_authorization)
}

fn is_safe_segment_for_authorization(segment: &str) -> bool {
    let bytes = segment.as_bytes();
    let mut index = 0;
    let mut decoded_dots = 0;
    let mut has_non_dot = false;
    while index < bytes.len() {
        match bytes[index] {
            b'.' => {
                decoded_dots += 1;
                index += 1;
            }
            b'\\' => return false,
            b'%' => {
                let Some(high) = bytes
                    .get(index + 1)
                    .and_then(|byte| decode_hex_digit(*byte))
                else {
                    return false;
                };
                let Some(low) = bytes
                    .get(index + 2)
                    .and_then(|byte| decode_hex_digit(*byte))
                else {
                    return false;
                };
                let decoded = high << 4 | low;
                match decoded {
                    b'%' | b'/' | b'\\' => return false,
                    b'.' => decoded_dots += 1,
                    _ => has_non_dot = true,
                }
                index += 3;
            }
            _ => {
                has_non_dot = true;
                index += 1;
            }
        }
    }

    has_non_dot || !matches!(decoded_dots, 1 | 2)
}

fn decode_hex_digit(byte: u8) -> Option<u8> {
    match byte {
        b'0'..=b'9' => Some(byte - b'0'),
        b'a'..=b'f' => Some(byte - b'a' + 10),
        b'A'..=b'F' => Some(byte - b'A' + 10),
        _ => None,
    }
}

#[cfg(test)]
#[path = "authorization_path_tests.rs"]
mod tests;
