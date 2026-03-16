#!/bin/sh
MULTIPLE_IFACE="${1:-false}"
sudo add-apt-repository ppa:isc/bind-esv -y
sudo apt update -y
sudo systemctl mask named
sudo apt install bind9 bind9-utils bind9-dnsutils -y
sudo cp /local/repository/bind/ns/named.conf.local /etc/bind/named.conf.local
if [ "$MULTIPLE_IFACE" = true ]; then
    sudo cp /local/repository/bind/ns/named.conf.options2 /etc/bind/named.conf.options
else
    sudo cp /local/repository/bind/ns/named.conf.options /etc/bind/named.conf.options
fi
sudo cp /local/repository/zone_file_defaults/db.workbench.lan /etc/bind/db.workbench.lan
sudo cp /local/repository/zone_file_defaults/db.dns64perf.test /etc/bind/db.dns64perf.test
sudo systemctl unmask named