#!/bin/sh
# Stop and uninstall Unbound (authoritative name server, compiled from source)
/local/repository/ns_software/unbound/stop.sh

# Remove unbound files installed by unbound/ns/install.sh
sudo make -C /opt/unbound-1.24.2 uninstall 2>/dev/null
sudo rm -rf /usr/local/etc/unbound
sudo rm -rf /opt/unbound-1.24.2.tar.gz /opt/unbound-1.24.2
