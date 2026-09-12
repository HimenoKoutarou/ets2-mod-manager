#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

#[cfg(feature = "desktop")]
fn main() {
    ets2_mod_manager_lib::run();
}

#[cfg(not(feature = "desktop"))]
fn main() {}
