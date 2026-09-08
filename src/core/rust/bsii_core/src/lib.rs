//! BSII boundary primitives. Full object parsing remains a later migration.

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct BsiiHeader {
    pub version: u32,
}

pub fn inspect_header(bytes: &[u8]) -> Result<BsiiHeader, &'static str> {
    if bytes.len() < 8 || &bytes[..4] != b"BSII" {
        return Err("invalid_bsii_header");
    }
    let version = u32::from_le_bytes([bytes[4], bytes[5], bytes[6], bytes[7]]);
    Ok(BsiiHeader { version })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn reads_little_endian_version() {
        assert_eq!(inspect_header(b"BSII\x03\0\0\0").unwrap().version, 3);
        assert_eq!(inspect_header(b"bad"), Err("invalid_bsii_header"));
    }
}
