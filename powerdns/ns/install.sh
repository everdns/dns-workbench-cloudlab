#!/bin/sh
MULTIPLE_IFACE="${1:-false}"
echo "deb [signed-by=/etc/apt/keyrings/auth-50-pub.asc] http://repo.powerdns.com/ubuntu jammy-auth-50 main" | sudo tee /etc/apt/sources.list.d/pdns.list
printf "Package: pdns-*\nPin: origin repo.powerdns.com\nPin-Priority: 600\n" | sudo tee /etc/apt/preferences.d/rec-54 > /dev/null
sudo install -d /etc/apt/keyrings; curl https://repo.powerdns.com/FD380FBB-pub.asc | sudo tee /etc/apt/keyrings/auth-50-pub.asc
sudo apt-get update
sudo systemctl mask pdns
sudo apt-get install pdns-server -y
sudo cp /local/repository/powerdns/ns/named.conf /etc/powerdns/named.conf
if [ "$MULTIPLE_IFACE" = true ]; then
    sudo cp /local/repository/powerdns/ns/pdns2.conf /etc/powerdns/pdns.conf
else
    sudo cp /local/repository/powerdns/ns/pdns.conf /etc/powerdns/pdns.conf
fi
sudo cp /local/repository/zone_file_defaults/db.workbench.lan /etc/powerdns/db.workbench.lan
sudo cp /local/repository/zone_file_defaults/db.dns64perf.test /etc/powerdns/db.dns64perf.test
sudo systemctl unmask pdns
sudo systemctl enable pdns && sudo systemctl start pdns