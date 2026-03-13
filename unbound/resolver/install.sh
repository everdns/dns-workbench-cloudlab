#!/bin/sh
MULTIPLE_IFACE="${1:-false}"
sudo apt update -y
sudo apt install -y build-essential libssl-dev libexpat1-dev bison flex
sudo wget https://nlnetlabs.nl/downloads/unbound/unbound-1.24.2.tar.gz -O /opt/unbound-1.24.2.tar.gz
sudo tar -xzf /opt/unbound-1.24.2.tar.gz
cd /opt/unbound-1.24.2 && sudo ./configure && sudo make && sudo make install
sudo /usr/local/sbin/unbound-anchor -a /usr/local/etc/unbound/root.key || true
sudo cp /local/repository/unbound/resolver/unbound.conf /usr/local/etc/unbound/unbound.conf
if [ "$MULTIPLE_IFACE" = true ]; then
    sudo cp /local/repository/unbound/resolver/forward2.conf /usr/local/etc/unbound/forward.conf
else
    sudo cp /local/repository/unbound/resolver/forward.conf /usr/local/etc/unbound/forward.conf
fi
sudo /usr/local/sbin/unbound-checkconf
sudo /usr/local/sbin/unbound