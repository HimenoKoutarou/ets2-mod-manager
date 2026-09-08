//! Small, dependency-free archive header classifier.

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ArchiveKind {
    Zip,
    HashFs,
    Aem,
    Unknown,
}

impl ArchiveKind {
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Zip => "zip",
            Self::HashFs => "scs_hashfs",
            Self::Aem => "aem",
            Self::Unknown => "unknown",
        }
    }
}

pub fn detect_kind(bytes: &[u8]) -> ArchiveKind {
    if bytes.starts_with(b"SCS#") {
        ArchiveKind::HashFs
    } else if bytes.starts_with(b"AEM!") {
        ArchiveKind::Aem
    } else if bytes.starts_with(b"PK") {
        ArchiveKind::Zip
    } else {
        ArchiveKind::Unknown
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn classifies_known_headers() {
        assert_eq!(detect_kind(b"PK\x03\x04"), ArchiveKind::Zip);
        assert_eq!(detect_kind(b"SCS#\0\0"), ArchiveKind::HashFs);
        assert_eq!(detect_kind(b"AEM!\0\0"), ArchiveKind::Aem);
        assert_eq!(detect_kind(b"nope"), ArchiveKind::Unknown);
    }
}
