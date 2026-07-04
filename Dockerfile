FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive
ENV FORCE_UNSAFE_CONFIGURE=1

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
    libxss-dev libxtst-dev libxcomposite-dev libxdamage-dev \
    libxcb1-dev libxcb-glx0-dev libxcb-keysyms1-dev \
    libxcb-image0-dev libxcb-shm0-dev libxcb-icccm4-dev \
    libxcb-sync-dev libxcb-xfixes0-dev libxcb-shape0-dev \
    libxcb-randr0-dev libxcb-render-util0-dev \
    libxcb-xinerama0-dev libxcb-xkb-dev \
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
    libboost-dev libboost-all-dev \
    libxml2-dev libxslt1-dev \
    libsasl2-dev libattr1-dev \
    # systemd build deps
    libcap-dev libmount-dev \
    python3-jinja2 python3-lxml \
    # Calamares
    libparted-dev libpwquality-dev \
    libboost-python-dev \
    yaml-cpp-dev \
    # Discover / PackageKit
    libflatpak-dev libfwupd-dev \
    libappstream-dev \
    libpolkit-gobject-1-dev \
    # GRUB
    libdevmapper-dev liblzma-dev \
    # Squashfs / ISO tools
    squashfs-tools xorriso mtools \
    # Misc utilities used by build scripts
    rsync patchelf \
    && rm -rf /var/lib/apt/lists/*

# Python deps for build scripts
RUN pip3 install --no-cache-dir requests

# Build volumes — sources cache and output root are mounted at runtime
VOLUME ["/mnt/smechos_build_root", "/mnt/spk-compile-sources"]

WORKDIR /build

COPY spk-compile.py /build/spk-compile.py

ENTRYPOINT ["python3", "/build/spk-compile.py"]
CMD ["smechos-plasma-live"]
