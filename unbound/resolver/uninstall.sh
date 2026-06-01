#!/bin/sh
# Stop and uninstall Unbound (recursive resolver, compiled from source)
/local/repository/unbound/resolver/stop.sh

# Remove unbound files installed by unbound/resolver/install.sh
sudo make -C /opt/unbound-1.24.2 uninstall 2>/dev/null
sudo rm -rf /usr/local/etc/unbound
sudo rm -rf /opt/unbound-1.24.2.tar.gz /opt/unbound-1.24.2
