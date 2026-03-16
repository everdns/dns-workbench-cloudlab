#!/bin/sh
MULTIPLE_IFACE="${1:-false}"
sudo apt update -y
sudo apt install -y build-essential libssl-dev libexpat1-dev bison flex
sudo wget https://nlnetlabs.nl/downloads/unbound/unbound-1.24.2.tar.gz -O /opt/unbound-1.24.2.tar.gz
sudo tar -xzf /opt/unbound-1.24.2.tar.gz -C /opt
cd /opt/unbound-1.24.2 && sudo ./configure && sudo make && sudo make install
if [ "$MULTIPLE_IFACE" = true ]; then
    sudo cp /local/repository/unbound/ns/unbound2.conf /usr/local/etc/unbound/unbound.conf
else
    sudo cp /local/repository/unbound/ns/unbound.conf /usr/local/etc/unbound/unbound.conf
fi
sudo cp /local/repository/zone_file_defaults/db.workbench.lan /usr/local/etc/unbound/db.workbench.lan
sudo cp /local/repository/zone_file_defaults/db.dns64perf.test /usr/local/etc/unbound/db.dns64perf.test
