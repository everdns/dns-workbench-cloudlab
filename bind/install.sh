#!/bin/sh
sudo add-apt-repository ppa:isc/bind-esv -y
sudo apt update -y
sudo systemctl mask named
sudo apt install bind9 bind9-utils bind9-dnsutils -y
sudo systemctl unmask named