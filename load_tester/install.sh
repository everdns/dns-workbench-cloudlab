#!/bin/sh
echo 'export PATH=$PATH:/opt/go/bin' | sudo tee /etc/profile.d/go_path.sh && sudo chmod +x /etc/profile.d/go_path.sh
sudo add-apt-repository ppa:longsleep/golang-backports -y
sudo apt update && sudo apt upgrade -y
sudo apt install golang -y
sudo mkdir -p /opt/go && sudo chown -R $USER /opt/go
GOPATH=/opt/go PATH=$PATH:/opt/go/bin go install github.com/tantalor93/dnspyre/v3@latest
GOPATH=/opt/go PATH=$PATH:/opt/go/bin go install github.com/everdns/dnspyre-dnsworkbench@latest
sudo cp /opt/go/bin/dnspyre /usr/local/bin/
sudo cp /opt/go/bin/dnspyre-dnsworkbench /usr/local/bin/
sudo apt install -y autoconf automake libtool  libssl-dev libldns-dev libck-dev libnghttp2-dev
sudo git clone https://codeberg.org/DNS-OARC/dnsperf.git /opt/dnsperf
cd /opt/dnsperf && sudo ./autogen.sh && sudo ./configure
cd /opt/dnsperf && sudo make && sudo make install
sudo git clone https://github.com/everdns/dnsperf-dnsworkbench.git /opt/dnsperf-dnsworkbench
cd /opt/dnsperf-dnsworkbench && sudo ./autogen.sh && sudo ./configure
cd /opt/dnsperf-dnsworkbench && sudo make && sudo make install
sudo apt install -y gcc g++
sudo apt install -y clang 
sudo git clone https://github.com/everdns/dns64perfpp-dnsworkbench.git /opt/dns64perfpp-dnsworkbench
cd /opt/dns64perfpp-dnsworkbench && sudo make CXXFLAGS+=" -DDNS64PERFPP_IPV4" && sudo make install
sudo git clone https://github.com/everdns/dns64perfpp-dnsworkbench.git /opt/dns64perfpp
cd /opt/dns64perfpp && sudo git checkout original_feature/multiport
cd /opt/dns64perfpp && sudo make CXXFLAGS+=" -DDNS64PERFPP_IPV4" && sudo make install
sudo apt install -y knot-dnsutils
sudo apt install -y build-essential pkg-config libgnutls28-dev liblmdb-dev libbpf-dev libmnl-dev libelf-dev
sudo git clone https://github.com/everdns/kxdpgun-dnsworkbench.git /opt/kxdpgun-dnsworkbench
cd /opt/kxdpgun-dnsworkbench && sudo autoreconf -if && sudo /opt/kxdpgun-dnsworkbench/configure && sudo make  && sudo make install