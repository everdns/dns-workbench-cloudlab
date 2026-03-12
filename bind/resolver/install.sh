#!/bin/sh
MULTIPLE_IFACE="${1:-false}"
sudo add-apt-repository ppa:isc/bind-esv -y
sudo apt update -y
sudo systemctl mask named
sudo apt install bind9 bind9-utils bind9-dnsutils -y
sudo cp /local/repository/bind/resolver/named.conf.options /etc/bind/named.conf.options
if [ "$MULTIPLE_IFACE" = true ]; then
    sudo cp /local/repository/bind/resolver/named.conf.local2 /etc/bind/named.conf.local
else
    sudo cp /local/repository/bind/resolver/named.conf.local /etc/bind/named.conf.local
fi
sudo systemctl unmask named
sudo systemctl enable named && sudo systemctl start named