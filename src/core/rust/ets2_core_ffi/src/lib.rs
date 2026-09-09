//! Stable C ABI for the first Rust migration slice.
//!
//! Inputs are UTF-8/byte buffers and outputs are owned byte buffers. The
//! caller must release both output buffers with `ets2_core_free_buffer`.

use std::slice;

use archive_core::detect_kind;
use bsii_core::parse_summary;
use mod_scanner::{count_supported, scan_roots};

pub const ABI_VERSION: u32 = 1;
pub const ERR_OK: i32 = 0;
pub const ERR_INVALID_ARGUMENT: i32 = 1;
pub const ERR_INVALID_UTF8: i32 = 2;

#[repr(C)]
#[derive(Clone, Copy)]
pub struct Ets2Buffer {
    pub ptr: *mut u8,
    pub len: usize,
}

#[repr(C)]
#[derive(Clone, Copy)]
pub struct Ets2Result {
    pub code: i32,
    pub data: Ets2Buffer,
    pub error: Ets2Buffer,
}

fn empty_buffer() -> Ets2Buffer {
    Ets2Buffer {
        ptr: std::ptr::null_mut(),
        len: 0,
    }
}

fn owned_buffer(bytes: Vec<u8>) -> Ets2Buffer {
    if bytes.is_empty() {
        return empty_buffer();
    }
    let boxed = bytes.into_boxed_slice();
    let len = boxed.len();
    let ptr = Box::into_raw(boxed) as *mut u8;
    Ets2Buffer { ptr, len }
}

fn ok(data: Vec<u8>) -> Ets2Result {
    Ets2Result {
        code: ERR_OK,
        data: owned_buffer(data),
        error: empty_buffer(),
    }
}

fn err(code: i32, message: &[u8]) -> Ets2Result {
    Ets2Result {
        code,
        data: empty_buffer(),
        error: owned_buffer(message.to_vec()),
    }
}

unsafe fn input<'a>(ptr: *const u8, len: usize) -> Result<&'a [u8], Ets2Result> {
    if len > 0 && ptr.is_null() {
        return Err(err(ERR_INVALID_ARGUMENT, b"null_input"));
    }
    Ok(slice::from_raw_parts(
        if ptr.is_null() { [].as_ptr() } else { ptr },
        len,
    ))
}

#[no_mangle]
pub extern "C" fn ets2_core_abi_version() -> u32 {
    ABI_VERSION
}

#[no_mangle]
pub unsafe extern "C" fn ets2_core_free_buffer(buffer: Ets2Buffer) {
    if buffer.ptr.is_null() || buffer.len == 0 {
        return;
    }
    let raw = std::ptr::slice_from_raw_parts_mut(buffer.ptr, buffer.len);
    drop(Box::from_raw(raw));
}

#[no_mangle]
pub unsafe extern "C" fn ets2_core_inspect_bytes(ptr: *const u8, len: usize) -> Ets2Result {
    let bytes = match input(ptr, len) {
        Ok(bytes) => bytes,
        Err(result) => return result,
    };
    if bytes.starts_with(b"BSII") {
        let summary = match parse_summary(bytes) {
            Ok(summary) => summary,
            Err(message) => return err(ERR_INVALID_ARGUMENT, message.as_bytes()),
        };
        return ok(format!(
            "{{\"kind\":\"bsii\",\"version\":{},\"definitions\":{},\"objects\":{}}}",
            summary.version, summary.definitions, summary.objects
        )
        .into_bytes());
    }
    let kind = detect_kind(bytes).as_str();
    ok(format!("{{\"kind\":\"{kind}\"}}").into_bytes())
}

#[no_mangle]
pub unsafe extern "C" fn ets2_core_scan_roots(ptr: *const u8, len: usize) -> Ets2Result {
    let bytes = match input(ptr, len) {
        Ok(bytes) => bytes,
        Err(result) => return result,
    };
    let text = match std::str::from_utf8(bytes) {
        Ok(text) => text,
        Err(_) => return err(ERR_INVALID_UTF8, b"input_not_utf8"),
    };
    let mut lines = text.lines();
    let local = lines
        .next()
        .filter(|v| !v.trim().is_empty())
        .map(std::path::Path::new);
    let workshop = lines
        .next()
        .filter(|v| !v.trim().is_empty())
        .map(std::path::Path::new);
    let packages = scan_roots(local, workshop);
    let mut json = String::from("{\"packages\":[");
    for (index, package) in packages.iter().enumerate() {
        if index > 0 {
            json.push(',');
        }
        let package_name = if package.manifest.package_name.is_empty() {
            &package.mod_id
        } else {
            &package.manifest.package_name
        };
        let display = if package.manifest.display_name.is_empty() {
            &package.mod_id
        } else {
            &package.manifest.display_name
        };
        json.push_str(&format!("{{\"mod_id\":\"{}\",\"package_name\":\"{}\",\"path\":\"{}\",\"type\":\"{}\",\"display_name\":\"{}\",\"size\":{},\"modified_ms\":{}}}",
            escape_json(&package.mod_id), escape_json(package_name), escape_json(&package.path.to_string_lossy()), escape_json(&package.package_type), escape_json(display), package.size, package.modified_unix_ms));
    }
    json.push_str("]}");
    ok(json.into_bytes())
}

fn escape_json(value: &str) -> String {
    value
        .replace('\\', "\\\\")
        .replace('"', "\\\"")
        .replace('\n', "\\n")
        .replace('\r', "\\r")
}

#[no_mangle]
pub unsafe extern "C" fn ets2_core_count_packages(ptr: *const u8, len: usize) -> Ets2Result {
    let bytes = match input(ptr, len) {
        Ok(bytes) => bytes,
        Err(result) => return result,
    };
    let text = match std::str::from_utf8(bytes) {
        Ok(text) => text,
        Err(_) => return err(ERR_INVALID_UTF8, b"input_not_utf8"),
    };
    let paths = text.lines().filter(|line| !line.trim().is_empty());
    let count = count_supported(paths);
    ok(format!("{{\"supported_packages\":{count}}}").into_bytes())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn abi_version_is_stable() {
        assert_eq!(ets2_core_abi_version(), 1);
    }

    #[test]
    fn inspect_returns_owned_json() {
        let result = unsafe { ets2_core_inspect_bytes(b"BSII\x03\0\0\0".as_ptr(), 8) };
        assert_eq!(result.code, ERR_OK);
        let data = unsafe { std::slice::from_raw_parts(result.data.ptr, result.data.len) };
        assert_eq!(
            data,
            br#"{"kind":"bsii","version":3,"definitions":0,"objects":0}"#
        );
        unsafe { ets2_core_free_buffer(result.data) };
    }
}
