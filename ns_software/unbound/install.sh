#!/bin/sh
sudo apt install -y build-essential libssl-dev libexpat1-dev bison flex libevent-dev
sudo wget https://nlnetlabs.nl/downloads/unbound/unbound-1.24.2.tar.gz -O /opt/unbound-1.24.2.tar.gz
sudo tar -xzf /opt/unbound-1.24.2.tar.gz -C /opt
cd /opt/unbound-1.24.2 && sudo ./configure --with-libevent && sudo make && sudo make install && sudo ldconfig
sudo cp /local/repository/ns_software/unbound/unbound.conf /usr/local/etc/unbound/unbound.conf
sudo cp /local/repository/zone_file_defaults/db.workbench.lan /usr/local/etc/unbound/db.workbench.lan
sudo cp /local/repository/zone_file_defaults/db.dns64perf.test /usr/local/etc/unbound/db.dns64perf.test
