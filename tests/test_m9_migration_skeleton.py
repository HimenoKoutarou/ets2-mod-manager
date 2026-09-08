"""Static checks for the Rust/.NET migration skeleton."""
from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class MigrationSkeletonTests(unittest.TestCase):
    def test_rust_workspace_has_all_core_crates(self):
        workspace = (ROOT / "src" / "core" / "rust" / "Cargo.toml").read_text(encoding="utf-8")
        for name in ("archive_core", "bsii_core", "mod_scanner", "ets2_core_ffi"):
            self.assertIn(f'"{name}"', workspace)
        ffi = (ROOT / "src" / "core" / "rust" / "ets2_core_ffi" / "src" / "lib.rs").read_text(encoding="utf-8")
        self.assertIn("ets2_core_abi_version", ffi)
        self.assertIn("ets2_core_free_buffer", ffi)
        self.assertIn("ets2_core_inspect_bytes", ffi)
        self.assertIn("ets2_core_count_packages", ffi)

    def test_dotnet_solution_contains_planned_layers(self):
        solution = (ROOT / "src" / "dotnet" / "ETS2ModManager.sln").read_text(encoding="utf-8")
        for name in (
            "ETS2ModManager.Contracts",
            "ETS2ModManager.Domain",
            "ETS2ModManager.Application",
            "ETS2ModManager.Infrastructure",
            "ETS2ModManager.WpfClient",
            "ETS2ModManager.ModScanWorker",
            "ETS2ModManager.ContractTests",
        ):
            self.assertIn(name, solution)
        props = (ROOT / "src" / "dotnet" / "Directory.Build.props").read_text(encoding="utf-8")
        self.assertIn("net10.0", props)

    def test_rust_client_uses_explicit_ownership_release(self):
        client = (ROOT / "src" / "dotnet" / "ETS2ModManager.Infrastructure" / "Rust" / "Ets2CoreNative.cs").read_text(encoding="utf-8")
        self.assertIn("ets2_core_free_buffer", client)
        self.assertIn("finally", client)
        self.assertIn("EnsureCompatible", client)


if __name__ == "__main__":
    unittest.main()
