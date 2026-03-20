#!/bin/sh
# Install dependencies and build the AF_XDP DNS responder
set -e

echo "Installing build dependencies..."
sudo apt update
sudo apt install -y \
    clang llvm gcc make pkg-config \
    libbpf-dev libelf-dev zlib1g-dev \
    linux-headers-$(uname -r) linux-tools-common linux-tools-$(uname -r)

# libxdp-dev may not be available in default Ubuntu 22.04 repos
if apt-cache show libxdp-dev >/dev/null 2>&1; then
    sudo apt install -y libxdp-dev
else
    echo "libxdp-dev not in repos, building xdp-tools from source..."
    sudo apt install -y libpcap-dev m4
    if [ ! -d /opt/xdp-tools ]; then
        sudo git clone https://github.com/xdp-project/xdp-tools.git /opt/xdp-tools
    fi
    cd /opt/xdp-tools
    sudo git submodule update --init
    sudo ./configure
    sudo make
    sudo make install
    sudo ldconfig
fi

echo "Building dns_responder..."
cd /local/repository/dns_responder
make clean && make

echo ""
echo "Build complete. Run with:"
echo "  sudo ./dns_responder -i <interface>"
