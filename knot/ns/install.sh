#!/bin/sh
MULTIPLE_IFACE="${1:-false}"
sudo apt-get update
sudo apt-get -y install apt-transport-https ca-certificates wget
sudo wget -O /usr/share/keyrings/cznic-labs-pkg.gpg https://pkg.labs.nic.cz/gpg
echo "deb [signed-by=/usr/share/keyrings/cznic-labs-pkg.gpg] https://pkg.labs.nic.cz/knot-dns jammy main" | sudo tee /etc/apt/sources.list.d/cznic-labs-knot-dns.list 
sudo apt-get update
sudo systemctl mask knot
sudo apt-get install knot -y
sudo systemctl unmask knot

if [ "$MULTIPLE_IFACE" = true ]; then
    sudo cp /local/repository/knot/ns/etc/knot/knot2.conf /etc/knot/knot.conf
else
    sudo cp /local/repository/knot/ns/etc/knot/knot.conf /etc/knot/knot.conf
fi
sudo cp /local/repository/zone_file_defaults/db.workbench.lan /etc/knot/db.workbench.lan
sudo cp /local/repository/zone_file_defaults/db.dns64perf.test /etc/knot/db.dns64perf.test
sudo systemctl enable --now knot