# Shared helpers for iso-builder. Sourced by the boot-setup and
# image-assembly stages. Modeled on debian-cd's add_mkisofs_opt pattern.

# add_mkisofs_opt <opts-file> <option string...>
# Appends an option (or several space-separated options) to the named
# options file, used later as one big argument blob to xorriso.
add_mkisofs_opt() {
    local file="$1"; shift
    echo "$@" >> "$file"
}

# add_mkisofs_dir <dirs-file> <dir>
# Appends a source directory to the named dirs file, used later as the
# list of trees xorriso should master into the image (BIOS boot dir first,
# so it lands near the front of the disc).
add_mkisofs_dir() {
    local file="$1"; shift
    echo "$@" >> "$file"
}

xorriso_bin() {
    if command -v xorriso >/dev/null 2>&1; then
        echo xorriso
    else
        echo "ERROR: xorriso not found in PATH" >&2
        exit 1
    fi
}
