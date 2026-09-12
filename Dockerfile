FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive
ENV FORCE_UNSAFE_CONFIGURE=1

# ca-certificates has to exist before anything can fetch https://apt.smech.xyz
# over HTTPS -- a bare ubuntu:24.04 has no CA trust store at all yet.
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates

# CMake comes from Smech Labs' own apt repo (apt.smech.xyz), not stock
# Ubuntu -- 24.04 only ships 3.28.3, and KDE Frameworks 6.27 declares
# cmake_minimum_required(VERSION 3.29). Adding the repo before the main
# install list means it participates in one normal `apt-get install`
# rather than a bolted-on second step.
COPY smech-labs-archive-keyring.gpg /usr/share/keyrings/smech-labs-archive-keyring.gpg
RUN echo "deb [signed-by=/usr/share/keyrings/smech-labs-archive-keyring.gpg] https://apt.smech.xyz smech-noble main" \
        > /etc/apt/sources.list.d/smech-labs.list

RUN apt-get update && apt-get install -y --no-install-recommends \
    # Core toolchain
    build-essential gcc g++ gcc-multilib g++-multilib \
    cmake ninja-build meson pkg-config \
    python3 python3-dev python3-pip \
    git curl wget ca-certificates \
    # Archive tools
    tar xz-utils gzip bzip2 zstd lzma \
    # Build system helpers
    autoconf automake libtool flex bison \
    gperf gettext texinfo \
    # Perl (needed by some build systems)
    perl \
    # Qt6 — X11/XCB
    libx11-dev libx11-xcb-dev libxext-dev libxfixes-dev \
    libxi-dev libxrender-dev libxrandr-dev libxcursor-dev \
    libice-dev libsm-dev libxft-dev libxkbfile-dev \
    libcups2-dev libplymouth-dev sassc python3-cairo-dev \
    gsettings-desktop-schemas libgtk-3-dev xserver-xorg-input-wacom \
    bc libyaml-dev libgirepository1.0-dev libstemmer-dev itstool xsltproc \
    libxmlb-dev valac bash-completion cpio grub-pc-bin \
    libsqlite3-dev \
    libxss-dev libxtst-dev libxcomposite-dev libxdamage-dev \
    libxcb1-dev libxcb-glx0-dev libxcb-keysyms1-dev \
    libxcb-image0-dev libxcb-shm0-dev libxcb-icccm4-dev \
    libxcb-sync-dev libxcb-xfixes0-dev libxcb-shape0-dev \
    libxcb-randr0-dev libxcb-render-util0-dev \
    libxcb-xinerama0-dev libxcb-xkb-dev \
    libxcb-composite0-dev libxcb-damage0-dev libxcb-dpms0-dev \
    libxcb-ewmh-dev libxcb-util-dev libxcb-cursor-dev \
    libxcb-res0-dev libxcb-xinput-dev libxcb-xtest0-dev \
    libxcb-dri2-0-dev libxcb-dri3-dev libxcb-present-dev libxshmfence-dev \
    libxxf86vm-dev libxcb-record0-dev \
    # Qt6 — Wayland
    libwayland-dev wayland-protocols libwayland-egl-backend-dev \
    libxkbcommon-dev libxkbcommon-x11-dev \
    # Qt6 — OpenGL / EGL / Vulkan
    libgl-dev libgles-dev libegl-dev libgbm-dev \
    libvulkan-dev libdrm-dev \
    # Qt6 — fonts & graphics
    libfontconfig-dev libfreetype-dev libharfbuzz-dev \
    libpng-dev libjpeg-dev libwebp-dev \
    # Qt6 — input & misc
    libinput-dev libevdev-dev libmtdev-dev libudev-dev \
    libdbus-1-dev libglib2.0-dev \
    libssl-dev libpcre2-dev \
    libzstd-dev liblz4-dev \
    # Qt6 — multimedia
    libasound2-dev libpulse-dev \
    libgstreamer1.0-dev libgstreamer-plugins-base1.0-dev \
    # Mesa
    llvm-dev libllvm18 libelf-dev libexpat1-dev \
    python3-mako libclang-dev \
    # KDE Frameworks
    libhunspell-dev libsecret-1-dev \
    libboost-dev libboost-all-dev \
    libxml2-dev libxslt1-dev libxml2-utils \
    libsasl2-dev libattr1-dev \
    # systemd build deps
    libcap-dev libmount-dev libkmod-dev kmod sudo podman \
    python3-jinja2 python3-lxml \
    # Calamares
    libparted-dev libpwquality-dev \
    libboost-python-dev \
    libyaml-cpp-dev \
    # Discover / PackageKit
    libpipewire-0.3-dev libspa-0.2-dev \
    libflatpak-dev libfwupd-dev \
    libappstream-dev \
    libpolkit-gobject-1-dev libpolkit-agent-1-dev \
    # GRUB
    libdevmapper-dev liblzma-dev \
    # Squashfs / ISO tools
    squashfs-tools xorriso mtools \
    # Misc utilities used by build scripts
    rsync patchelf \
    && rm -rf /var/lib/apt/lists/*

# podman (used by spk-compile.py's locale phase to run a throwaway
# container) defaults to the overlay storage driver, which can't stack on
# top of Docker's own overlay2 root filesystem -- the kernel doesn't
# support overlay-on-overlay. Force vfs instead: slower (plain copies, no
# copy-on-write layering) but works regardless of the underlying
# filesystem, and this nested container is tiny and short-lived so the
# performance cost is irrelevant.
RUN mkdir -p /etc/containers /var/lib/smech-podman-storage /run/smech-podman-storage && \
    printf '[storage]\ndriver = "vfs"\nrunroot = "/run/smech-podman-storage"\ngraphroot = "/var/lib/smech-podman-storage"\n' > /etc/containers/storage.conf

# Podman's own nested containers don't automatically inherit the outer
# Docker container's (already DNS64-fixed) resolv.conf -- they need their
# own explicit DNS config, same Cloudflare DNS64 resolvers as the Docker
# daemon itself, or they can't resolve any IPv4-only host from inside.
RUN printf '[containers]\ndns_servers = ["2606:4700:4700::64", "2606:4700:4700::6400"]\n' >> /etc/containers/containers.conf 2>/dev/null || \
    printf '[containers]\ndns_servers = ["2606:4700:4700::64", "2606:4700:4700::6400"]\n' > /etc/containers/containers.conf

# Ubuntu 24.04's default gcc/g++ is version 13. Plasma 6 packages (e.g.
# libkscreen) use C++23's std::ranges::to, which needs GCC >= 14. Noble's
# own repos carry gcc-14/g++-14 as an installable alternate version
# alongside the default 13 -- no PPA needed -- so install it and make it
# the active gcc/g++ via update-alternatives.
RUN apt-get update && apt-get install -y --no-install-recommends gcc-14 g++-14 && \
    rm -rf /var/lib/apt/lists/* && \
    update-alternatives --install /usr/bin/gcc gcc /usr/bin/gcc-14 100 \
        --slave /usr/bin/g++ g++ /usr/bin/g++-14 && \
    gcc --version | head -1

# Python deps for build scripts. --break-system-packages is safe here:
# this is a disposable, single-purpose build container, nothing else on
# it depends on the system Python staying untouched.
RUN pip3 install --no-cache-dir --break-system-packages requests

# Runtime .so's that phase_kde copies verbatim into the target rootfs
# (spk-compile.py's kde-<libname>-runtime loop) -- ical (calendaring),
# NetworkManager/ModemManager client libs, ffmpeg (video thumbnails/
# playback), VA-API, FreeRDP3+WinPR3 (KRDP), OpenCV core+imgproc and
# ZXing (barcode/QR scanning), none of which are pulled in transitively
# by anything installed above.
RUN apt-get update && apt-get install -y --no-install-recommends \
    libical-dev libnm-dev libmm-glib-dev \
    libavcodec-dev libavutil-dev libavformat-dev libavfilter-dev libswscale-dev \
    libva-dev freerdp3-dev libwinpr3-dev \
    libopencv-dev libzxing-dev && \
    rm -rf /var/lib/apt/lists/*

# Mesa's meson build forces -Dglvnd=enabled, which needs libglvnd's dev
# headers/pkgconfig files (not just its runtime lib, already pulled in
# transitively) or meson setup fails at dependency resolution. The
# requested Vulkan drivers (amd,intel,virtio) also need glslangValidator
# (glslang-tools) to compile SPIR-V shaders at build time. radeonsi's
# compute-based blit path needs libclc (matched to our LLVM 18 toolchain)
# for its SPIR-V kernel library, and libclc's own clspv-style pipeline
# pulls in LLVMSPIRVLib (bidirectional LLVM IR <-> SPIR-V translation).
RUN apt-get update && apt-get install -y --no-install-recommends \
    libglvnd-dev glslang-tools libclc-18 libclc-18-dev \
    libllvmspirvlib-18-dev llvm-spirv-18 && \
    rm -rf /var/lib/apt/lists/*

# GRUB's genmoddep.awk uses the gawk-specific asorti() function, which
# mawk (the only awk present on a bare Ubuntu image) does not implement.
# autoconf's AC_PROG_AWK picks gawk over mawk when both are present, so
# installing gawk is enough -- no explicit update-alternatives needed.
RUN apt-get update && apt-get install -y --no-install-recommends gawk && \
    rm -rf /var/lib/apt/lists/*

# kdoctools' cmake/uriencode.cmake needs the Perl URI::Escape module at
# configure time (kdoctools_encode_uri) -- not covered by the bare `perl`
# package installed above, which has no CPAN modules beyond core. It also
# needs the actual DocBook XML 4.5 DTD + XSL stylesheets to process KDE's
# help documentation.
RUN apt-get update && apt-get install -y --no-install-recommends \
    liburi-perl docbook-xml docbook-xsl && \
    rm -rf /var/lib/apt/lists/*

# knotifications' Canberra backend (notification sounds) needs libcanberra.
RUN apt-get update && apt-get install -y --no-install-recommends libcanberra-dev && \
    rm -rf /var/lib/apt/lists/*

# baloo (KDE file indexer) stores its index in LMDB.
RUN apt-get update && apt-get install -y --no-install-recommends liblmdb-dev && \
    rm -rf /var/lib/apt/lists/*

# prison (KF6Prison, barcode/QR generator) needs QRencode + Dmtx backends.
RUN apt-get update && apt-get install -y --no-install-recommends libqrencode-dev libdmtx-dev && \
    rm -rf /var/lib/apt/lists/*

# kscreenlocker authenticates unlock attempts via PAM.
RUN apt-get update && apt-get install -y --no-install-recommends libpam0g-dev && \
    rm -rf /var/lib/apt/lists/*

# libksysguard reads hardware sensors (required) and per-app network usage
# via libpcap (optional, but cheap to include).
RUN apt-get update && apt-get install -y --no-install-recommends libsensors-dev libpcap-dev && \
    rm -rf /var/lib/apt/lists/*

# kwin (the compositor) computes RandR mode timings via libxcvt.
RUN apt-get update && apt-get install -y --no-install-recommends libxcvt-dev && \
    rm -rf /var/lib/apt/lists/*

# libdisplay-info's meson build reads vendor names from hwdata's pnp.ids
# at build time (also used at runtime by kwin for monitor vendor lookup).
RUN apt-get update && apt-get install -y --no-install-recommends hwdata && \
    rm -rf /var/lib/apt/lists/*

# kwin: epoxy for OpenGL dispatch, lcms2 for its color management system.
RUN apt-get update && apt-get install -y --no-install-recommends libepoxy-dev liblcms2-dev && \
    rm -rf /var/lib/apt/lists/*

# phase_firmware() copies amdgpu/i915/radeon blobs from THIS container's
# /usr/lib/firmware into the target rootfs -- without linux-firmware
# installed here, the container has none of those dirs and the phase
# silently skips all three (empty firmware dir in every produced image).
RUN apt-get update && apt-get install -y --no-install-recommends linux-firmware && \
    rm -rf /var/lib/apt/lists/*

# Sanity marker so spk-compile.py's _in_matching_build_image() check
# knows this IS an Ubuntu-24.04-ABI-matched build container -- lets
# phase_locale (and phase_xwayland_deps) generate/build directly here
# instead of spinning up a throwaway nested podman container for a
# matching-ABI toolchain it already has.
RUN echo "smechos-build" > /etc/smechos-build-image

# Build volumes — sources cache and output root are mounted at runtime
VOLUME ["/mnt/smechos_build_root", "/mnt/spk-compile-sources"]

WORKDIR /build

COPY spk-compile.py /build/spk-compile.py

ENTRYPOINT ["python3", "/build/spk-compile.py"]
CMD ["smechos-plasma-live"]
