#!/bin/sh
sudo add-apt-repository ppa:isc/bind-esv -y
sudo apt update -y
sudo systemctl mask named
sudo apt install bind9 bind9-utils bind9-dnsutils -y
sudo cp /local/repository/ns_software/bind/named.conf.local /etc/bind/named.conf.local
sudo cp /local/repository/ns_software/bind/named.conf.options /etc/bind/named.conf.options
sudo cp /local/repository/zone_file_defaults/db.workbench.lan /etc/bind/db.workbench.lan
sudo cp /local/repository/zone_file_defaults/db.dns64perf.test /etc/bind/db.dns64perf.test
sudo systemctl unmask named