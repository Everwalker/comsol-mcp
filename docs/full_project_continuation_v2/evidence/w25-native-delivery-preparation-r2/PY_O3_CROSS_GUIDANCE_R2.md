# Intel cross-build guidance, candidate R2

Read-only source analysis; no Intel build was run.

The cryptography 50.0.1 workspace enables PyO3 features `abi3` and `abi3t`; its `cryptography-cffi` build script selects the stable `abi3` case for a GIL-enabled CPython target through 3.14 and defines `Py_LIMITED_API=0x030900f0`. PyO3 0.29's official guide explicitly documents stable-ABI extension builds without a Python interpreter (`PYO3_NO_PYTHON`) and says target/host interpreters are optional for `abi3` cross-builds. It also says `PYO3_CROSS_LIB_DIR` is needed only to link to libpython or when target `_sysconfigdata` is required. That means a full x86 CPython interpreter/config is not an established PyO3 prerequisite here.

The cryptography crate adds a separate CFFI bridge. Its checked-in `cryptography-cffi/build.rs` runs `_cffi_src/build_openssl.py` using `PYO3_PYTHON`, then compiles the generated `_openssl.c`. When `PYO3_CROSS_LIB_DIR` is unset it obtains include directories from the build Python's setuptools; when set, it derives an include path from the supplied cross-lib directory layout. The existing build script therefore requires a target-valid Python include tree for its C compilation. The currently installed CPython 3.12.13 is ARM64 and its `pyconfig.h` is unsuitable as x86-target evidence. No x86 header/config was synthesized and no fake `PYO3_CROSS_LIB_DIR` was created.

The official Python 3.12 configure documentation describes cross-building Python with a build interpreter matching the target Python version, `--build`, `--host`, and `--with-build-python`; it states `configure` generates `pyconfig.h`. The exact x86 configuration answers and generated headers remain open until a future source-only CPython 3.12.13 cross-build is performed and validated with the installed macOS SDK. The exact python.org 3.12.13 source archive is metadata-pinned in `PYTHON_AND_BUILD_INPUT_METADATA_R2.json`; no archive was fetched.

The Rust 1.83.0 host toolchain and x86_64 Apple stdlib have official manifest hashes and metadata-only byte sizes in `RUST_HOST_TARGET_COMPONENT_AUDIT_R2.json`. The host has Xcode Command Line Tools/SDK 27.0 and Apple Clang advertises x86-64 code generation, but no `rustc`, Cargo, rustup, Maturin, or Rosetta installation was found. Rosetta was not invoked. Homebrew OpenSSL dylibs are ARM64-only. All 32 registry crate lock hashes match crates.io API metadata; six crates do not declare an API `rust_version`, so the metadata check is not a build-compatibility proof.

Official Maturin docs say PyO3 bindings have cross-compilation support, while other binding types may work but are not regularly tested; its cross-compilation section particularly describes Linux/Zig and warns other platforms are not regularly tested. The exact Maturin 1.14.1 universal2 wheel metadata supports running a host ARM Maturin candidate, but does not establish that this cryptography/macOS x86 cross-build succeeds.

## Open build gates

- Acquire the exact official Rust installer/components and the 32 exact Cargo crate archives only at the approved build boundary; install the toolchain into task-local paths, not the user's Cargo/Rust directories.
- Build or otherwise obtain a verifiable x86 CPython 3.12.13 target header/config tree from the official source and installed SDK. Do not reuse the ARM `pyconfig.h`.
- Determine the target's actual SDK configuration and cross-compiler flags, build x86_64 OpenSSL from its pinned official source, and prove the minimum deployment version and linked-library closure.
- Build through the pinned Maturin candidate, then inspect the wheel tags and Mach-O slices with platform tools and install/test on an actual Intel runtime before claiming Intel support.

Current status: **OPEN / NOT BUILD-READY / NO INTEL WHEEL BUILT**.

## Primary references

- PyO3 0.29.0 guide: https://pyo3.rs/v0.29.0/building-and-distribution.html (stable ABI without interpreter; cross-compiling sections).
- Maturin distribution guide: https://www.maturin.rs/distribution.html (Cross Compiling; platform support limitations).
- CPython 3.12 configure guide: https://docs.python.org/3.12/using/configure.html (Cross Compiling Options; generated `pyconfig.h`).
- Cryptography 50.0.1 source tag: https://github.com/pyca/cryptography/tree/50.0.1; exact Cargo.toml, pyproject.toml, build.rs, and Cargo.lock source byte hashes are in the adjacent R2 JSON audit.
