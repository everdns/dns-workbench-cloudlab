#!/bin/sh
MULTIPLE_IFACE="${1:-false}"
sudo apt-get update -y
sudo apt-get -y install apt-transport-https ca-certificates wget
sudo wget -O /usr/share/keyrings/cznic-labs-pkg.gpg https://pkg.labs.nic.cz/gpg
echo "deb [signed-by=/usr/share/keyrings/cznic-labs-pkg.gpg] https://pkg.labs.nic.cz/knot-resolver jammy main" | sudo tee /etc/apt/sources.list.d/cznic-labs-knot-resolver.list
sudo apt-get update -y
sudo systemctl mask knot-resolver
sudo apt-get install knot-resolver6 -y
if [ "$MULTIPLE_IFACE" = true ]; then
    sudo cp /local/repository/knot/resolver/config2.yaml /etc/knot-resolver/config.yaml
else
    sudo cp /local/repository/knot/resolver/config.yaml /etc/knot-resolver/config.yaml
fi
sudo systemctl unmask knot-resolver
sudo systemctl enable knot-resolver && sudo systemctl start knot-resolver