#!/bin/bash

# Uninstall dns software that is installed

# Stop services before uninstalling
for svc in named pdns pdns-recursor knot-resolver knot; do
    if systemctl is-active --quiet "$svc" 2>/dev/null; then
        echo "Stopping $svc..."
        sudo systemctl stop "$svc"
    fi
done
sudo pkill nsd
sudo pkill unbound

# Remove nsd files installed by nsd/ns/install.sh
sudo make -C /opt/nsd-4.14.1 uninstall 2>/dev/null
sudo rm -f /usr/local/sbin/nsd /usr/local/sbin/nsd-checkconf /usr/local/sbin/nsd-checkzone /usr/local/sbin/nsd-control
sudo rm -rf /opt/nsd-4.14.1.tar.gz /opt/nsd-4.14.1 /etc/nsd /var/run /var/db/nsd/xfrd.state /var/db/nsd/zone.list /var/db/nsd/cookiesecrets.txt

#Remove unbound files installed by unbound/resolver/install.sh
sudo make -C /opt/unbound-1.24.2 uninstall 2>/dev/null
sudo rm -rf /usr/local/etc/unbound
sudo rm -rf /opt/unbound-1.24.2.tar.gz /opt/unbound-1.24.2

for pkg in bind9 bind9-utils bind9-dnsutils pdns-server pdns-recursor knot-resolver6 knot; do
    if dpkg -l "$pkg" 2>/dev/null | grep -q "^ii"; then
        echo "Removing $pkg..."
        sudo apt-get remove --purge "$pkg" -y
    else
        echo "$pkg is not installed, skipping."
    fi
done

echo "Done."