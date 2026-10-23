#!/bin/sh
# Build and install one BIND release from an already-downloaded tarball.
# Usage: build_bind.sh <version>      (runs on the name server; needs sudo)
# Reads /opt/bind-src/bind-<version>.tar.xz, installs into /opt/bind-<version>
# with its config dir at /etc/bind-<version>. Native build (nodes are aarch64).
set -eu

V="$1"
PREFIX="/opt/bind-$V"
SRC_ROOT=/opt/bind-src
TARBALL="$SRC_ROOT/bind-$V.tar.xz"

if [ -x "$PREFIX/sbin/named" ]; then
    echo "bind-$V already built"
    exit 0
fi

if [ ! -f /opt/bind-src/.deps-installed ]; then
    sudo apt-get update -y
    sudo DEBIAN_FRONTEND=noninteractive apt-get install -y \
        build-essential pkg-config autoconf automake libtool xz-utils curl \
        meson ninja-build libuv1-dev libssl-dev libcap-dev libnghttp2-dev \
        libxml2-dev libjson-c-dev liburcu-dev libidn2-dev libjemalloc-dev \
        dnsutils perl python3-ply
    sudo touch /opt/bind-src/.deps-installed
fi

BUILD_DIR="$SRC_ROOT/build-$V"
sudo rm -rf "$BUILD_DIR"
sudo mkdir -p "$BUILD_DIR"
sudo chown "$(id -u):$(id -g)" "$BUILD_DIR"
tar -xf "$TARBALL" -C "$BUILD_DIR" --strip-components=1
cd "$BUILD_DIR"

if [ -f meson.build ]; then
    meson setup build --prefix="$PREFIX" --sysconfdir="/etc/bind-$V" \
        --localstatedir="$PREFIX/var"
    ninja -C build
    sudo ninja -C build install
else
    ./configure --prefix="$PREFIX" --sysconfdir="/etc/bind-$V" \
        --localstatedir="$PREFIX/var"
    make -j"$(nproc)"
    sudo make install
fi

sudo mkdir -p "$PREFIX/var/run/named" "$PREFIX/var/run"
cd /
sudo rm -rf "$BUILD_DIR"
"$PREFIX/sbin/named" -V | head -1
