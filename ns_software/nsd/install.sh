#!/bin/sh
sudo wget https://nlnetlabs.nl/downloads/nsd/nsd-4.14.1.tar.gz -O /opt/nsd-4.14.1.tar.gz
sudo tar -xzf /opt/nsd-4.14.1.tar.gz -C /opt
sudo apt install -y build-essential libssl-dev libevent-dev bison flex
sudo apt install -y protobuf-c-compiler libprotobuf-c-dev libfstrm-dev
cd /opt/nsd-4.14.1 && sudo ./configure && sudo make && sudo make install
sudo cp /local/repository/ns_software/nsd/nsd.conf /etc/nsd/nsd.conf
sudo cp /local/repository/zone_file_defaults/db.workbench.lan /etc/nsd/db.workbench.lan
sudo cp /local/repository/zone_file_defaults/db.dns64perf.test /etc/nsd/db.dns64perf.test