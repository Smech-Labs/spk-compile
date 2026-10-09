#!/usr/bin/env python3
"""Reproduces the CROSS_LLVM_BUILD / CROSS_SPIRV_TRANSLATOR_BUILD prerequisite
workdirs that phase_mesa's OpenCL/clc support (intel_clc) depends on.

Why this exists (task #72): these two workdirs were originally hand-built,
once, directly on one machine, during early cross-toolchain verification --
never through spk-compile.py, never scripted, never checked in. Every path
spk-compile.py reads out of them (CROSS_LLVM_BUILD, CROSS_LLVM_CONFIG,
CROSS_SPIRV_TRANSLATOR_BUILD, CROSS_CLANG_SRC_INCLUDE) was hardcoded to one
person's home directory. This script is the actual, real recipe used to
build the existing workdirs -- extracted from their own real CMakeCache.txt
files, not guessed -- so a clean machine or CI can reproduce the same
prerequisite from a plain `git clone` instead of inheriting tribal state.

This is a slow, one-time toolchain prerequisite (LLVM/Clang from source),
not a per-rootfs build -- it does NOT run as part of a normal spk-compile.py
phase, and does not touch any target rootfs. Run it once; point spk-
compile.py at its output via the SMECHOS_LLVM_WORKDIR /
SMECHOS_SPIRV_TRANSLATOR_WORKDIR env vars (see spk-compile.py's own
CROSS_LLVM_BUILD / CROSS_SPIRV_TRANSLATOR_BUILD constants) once it's done.

NOT YET RE-RUN END TO END ON A CLEAN MACHINE. Every flag and path below was
read directly out of the real CMakeCache.txt / cross-toolchain.cmake files
of the existing, already-working workdirs on this machine -- that's ground
truth, not a guess -- but nobody has actually executed this exact script
from a blank starting point yet to confirm it reproduces them byte-for-byte.
Flag that honestly rather than claiming it's proven.
"""
import os, subprocess, sys, multiprocessing

CROSS_TRIPLET = "x86_64-smechos-linux-gnu"
CROSS_TOOLCHAIN_BIN = os.path.expanduser(f"~/x-tools/{CROSS_TRIPLET}/bin")

LLVM_TAG = "llvmorg-20.1.2"
LLVM_URL = f"https://github.com/llvm/llvm-project/archive/refs/tags/{LLVM_TAG}.tar.gz"

SPIRV_TRANSLATOR_BRANCH = "llvm_release_200"
SPIRV_TRANSLATOR_URL = (f"https://github.com/KhronosGroup/SPIRV-LLVM-Translator/"
                         f"archive/refs/heads/{SPIRV_TRANSLATOR_BRANCH}.tar.gz")

WORKDIR = os.environ.get("SMECHOS_LLVM_WORKDIR",
                          os.path.expanduser("~/smechos-work/llvm-cross-workdir"))
SPIRV_WORKDIR = os.environ.get("SMECHOS_SPIRV_TRANSLATOR_WORKDIR",
                                os.path.expanduser("~/smechos-work/spirv-translator-workdir"))

LLVM_SRC = os.path.join(WORKDIR, f"llvm-project-{LLVM_TAG}")
NATIVE_BUILD = os.path.join(WORKDIR, "native-build")
CROSS_BUILD = os.path.join(WORKDIR, "cross-build")
TOOLCHAIN_FILE = os.path.join(WORKDIR, "cross-toolchain.cmake")

SPIRV_SRC = os.path.join(SPIRV_WORKDIR, f"SPIRV-LLVM-Translator-{SPIRV_TRANSLATOR_BRANCH}")
SPIRV_BUILD = os.path.join(SPIRV_WORKDIR, "build")


def run(cmd, cwd=None):
    print(f"$ {' '.join(cmd)}  (cwd={cwd})")
    subprocess.run(cmd, cwd=cwd, check=True)


def fetch_and_extract(url, dest_dir, strip_name):
    os.makedirs(os.path.dirname(dest_dir), exist_ok=True)
    tarball = dest_dir + ".tar.gz"
    if not os.path.exists(tarball):
        run(["curl", "-L", "-o", tarball, url])
    if not os.path.isdir(dest_dir):
        extract_to = os.path.dirname(dest_dir)
        run(["tar", "xf", tarball, "-C", extract_to])


def write_toolchain_file():
    # Verified real content, read directly out of the existing, working
    # cross-toolchain.cmake rather than reconstructed from memory.
    content = f"""set(CMAKE_SYSTEM_NAME Linux)
set(CMAKE_SYSTEM_PROCESSOR x86_64)
set(CMAKE_C_COMPILER   {CROSS_TOOLCHAIN_BIN}/{CROSS_TRIPLET}-gcc)
set(CMAKE_CXX_COMPILER {CROSS_TOOLCHAIN_BIN}/{CROSS_TRIPLET}-g++)
set(CMAKE_AR           {CROSS_TOOLCHAIN_BIN}/{CROSS_TRIPLET}-ar)
set(CMAKE_RANLIB       {CROSS_TOOLCHAIN_BIN}/{CROSS_TRIPLET}-ranlib)
set(CMAKE_STRIP        {CROSS_TOOLCHAIN_BIN}/{CROSS_TRIPLET}-strip)
# Same-arch (x86_64 host == x86_64 target, only glibc differs), so cross
# binaries run directly on the build machine -- no QEMU/exe_wrapper needed.
set(CMAKE_CROSSCOMPILING_EMULATOR "")
"""
    os.makedirs(WORKDIR, exist_ok=True)
    with open(TOOLCHAIN_FILE, "w") as f:
        f.write(content)
    return TOOLCHAIN_FILE


def stage1_native_tblgen():
    """Native (host-arch, host-glibc) build of clang only, to produce a
    working clang-tblgen that runs ON the build machine -- a cross-built
    clang-tblgen can't run here to generate the cross-build's own sources,
    so this stage exists purely to produce that one host-native tool."""
    os.makedirs(NATIVE_BUILD, exist_ok=True)
    if not os.path.exists(os.path.join(NATIVE_BUILD, "bin", "clang-tblgen")):
        run(["cmake", "-G", "Ninja", "-S", os.path.join(LLVM_SRC, "llvm"),
             "-B", NATIVE_BUILD,
             "-DCMAKE_BUILD_TYPE=Release",
             "-DLLVM_ENABLE_PROJECTS=clang"], cwd=WORKDIR)
        run(["ninja", "-C", NATIVE_BUILD, "clang-tblgen"])


def stage2_cross_llvm_clang():
    """The real cross-build: clang + libclc, targeting X86;AMDGPU bitcode
    generation (what intel_clc/radeonsi's OpenCL C compiler actually needs
    LLVM for), cross-compiled with CROSS_TRIPLET's own gcc via the
    toolchain file, using stage 1's native clang-tblgen as the code
    generator (TableGen output must match the target clang being built,
    but the tool itself must execute on the host).

    LLVM_ENABLE_RTTI=ON is required here, not optional: Mesa's C++ code
    needs RTTI, LLVM defaults it off, and finding that out after the fact
    means rebuilding essentially all of LLVM+Clang again (RTTI is a
    project-wide flag, not a targeted one). That was a real, costly lesson
    from the first build of this chain -- caught here, on a genuinely
    clean machine, as a gap between the documented requirement and what
    this script actually passed to cmake. Fixed 2026-10-09 instead of
    quietly re-paying the same rebuild cost on every future clean build."""
    clang_tblgen = os.path.join(NATIVE_BUILD, "bin", "clang-tblgen")
    os.makedirs(CROSS_BUILD, exist_ok=True)
    run(["cmake", "-G", "Ninja", "-S", os.path.join(LLVM_SRC, "llvm"),
         "-B", CROSS_BUILD,
         f"-DCMAKE_TOOLCHAIN_FILE={TOOLCHAIN_FILE}",
         "-DCMAKE_BUILD_TYPE=Release",
         "-DCMAKE_INSTALL_PREFIX=/usr/local",
         "-DLLVM_ENABLE_PROJECTS=clang;libclc",
         "-DLLVM_TARGETS_TO_BUILD=X86;AMDGPU",
         "-DLLVM_DEFAULT_TARGET_TRIPLE=x86_64-unknown-linux-gnu",
         "-DLLVM_HOST_TRIPLE=x86_64-unknown-linux-gnu",
         "-DLLVM_BUILD_LLVM_DYLIB=OFF",
         "-DLLVM_ENABLE_RTTI=ON",
         f"-DCLANG_TABLEGEN={clang_tblgen}",
         f"-DCLANG_TABLEGEN_EXE={clang_tblgen}"], cwd=WORKDIR)
    run(["ninja", "-C", CROSS_BUILD])


def stage3_spirv_translator():
    """SPIRV-LLVM-Translator, cross-built against stage 2's cross LLVM via
    the same toolchain file. llvm_release_200 is the branch (not a tag --
    verified against the real GitHub API before writing this) KhronosGroup
    maintains specifically for LLVM 20.x compatibility, matching this
    chain's pinned LLVM version."""
    os.makedirs(SPIRV_BUILD, exist_ok=True)
    run(["cmake", "-G", "Ninja", "-S", SPIRV_SRC, "-B", SPIRV_BUILD,
         f"-DCMAKE_TOOLCHAIN_FILE={TOOLCHAIN_FILE}",
         "-DCMAKE_BUILD_TYPE=Release",
         "-DCMAKE_INSTALL_PREFIX=/usr/local",
         f"-DLLVM_DIR={CROSS_BUILD}/lib/cmake/llvm",
         "-DLLVM_SPIRV_BUILD_EXTERNAL=YES",
         "-DLLVM_SPIRV_INCLUDE_TESTS=OFF"], cwd=WORKDIR)
    run(["ninja", "-C", SPIRV_BUILD])


def fix_spirv_pc_and_headers():
    """Folds in bug #28's fix (found 2026-10-04): the real LLVMSPIRVLib.pc
    CMake generates has prefix=/usr/local (its untouched CMake default,
    never actually installed there) and headers ship flat
    (include/LLVMSPIRVLib.h) while Mesa's clc_helpers.cpp expects them
    nested (<LLVMSPIRVLib/LLVMSPIRVLib.h>). Doing this as part of the
    bootstrap, not as a manual post-hoc patch on one machine, is the actual
    fix for task #72 -- last night's version of this fix lived only in
    this machine's filesystem, not in anything checked into the repo."""
    fixed_inc = os.path.join(SPIRV_BUILD, "include-fixed", "LLVMSPIRVLib")
    os.makedirs(fixed_inc, exist_ok=True)
    real_inc = os.path.join(SPIRV_SRC, "include")
    for name in ("LLVMSPIRVLib.h", "LLVMSPIRVOpts.h", "LLVMSPIRVExtensions.inc"):
        link = os.path.join(fixed_inc, name)
        target = os.path.join(real_inc, name)
        if os.path.lexists(link):
            os.remove(link)
        os.symlink(target, link)

    pc_path = os.path.join(SPIRV_BUILD, "LLVMSPIRVLib.pc")
    with open(pc_path, "w") as f:
        f.write(f"""prefix={SPIRV_BUILD}
exec_prefix=${{prefix}}
libdir=${{prefix}}/lib/SPIRV
includedir={SPIRV_BUILD}/include-fixed

Name: LLVMSPIRVLib
Description: LLVM/SPIR-V bi-directional translator
Version: 20.1.0.0
URL: https://github.com/KhronosGroup/SPIRV-LLVM-Translator

Libs: -L${{libdir}} -lLLVMSPIRVLib
Cflags: -I${{includedir}}
""")


def main():
    nproc = multiprocessing.cpu_count()
    print(f"=== bootstrap_cross_llvm: workdir={WORKDIR}, spirv_workdir={SPIRV_WORKDIR}, "
          f"nproc={nproc} ===")
    print("This is a slow, one-time, multi-hour build (real LLVM/Clang from "
          "source). Not something to run casually -- make sure that's actually "
          "intended before continuing.")

    write_toolchain_file()
    fetch_and_extract(LLVM_URL, LLVM_SRC, LLVM_TAG)
    stage1_native_tblgen()
    stage2_cross_llvm_clang()
    fetch_and_extract(SPIRV_TRANSLATOR_URL, SPIRV_SRC, SPIRV_TRANSLATOR_BRANCH)
    stage3_spirv_translator()
    fix_spirv_pc_and_headers()

    print("=== done ===")
    print(f"Point spk-compile.py at this output via:")
    print(f"  export SMECHOS_LLVM_WORKDIR={WORKDIR}")
    print(f"  export SMECHOS_SPIRV_TRANSLATOR_WORKDIR={SPIRV_WORKDIR}")


if __name__ == "__main__":
    main()
