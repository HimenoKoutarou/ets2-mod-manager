//! Pure path-level scanner helpers used by the future worker process.

pub fn supported_package(path: &str) -> bool {
    let lower = path.to_ascii_lowercase();
    lower.ends_with(".scs") || lower.ends_with(".zip")
}

pub fn count_supported<I>(paths: I) -> usize
where
    I: IntoIterator,
    I::Item: AsRef<str>,
{
    paths
        .into_iter()
        .filter(|path| supported_package(path.as_ref()))
        .count()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn counts_scs_and_zip_only() {
        assert_eq!(count_supported(["a.scs", "b.zip", "folder", "c.SCS"]), 3);
    }
}
