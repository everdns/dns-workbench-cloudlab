#!/bin/sh
sudo systemctl mask bind9
sudo apt install bind9 bind9-utils bind9-dnsutils -y
sudo systemctl unmask bind9