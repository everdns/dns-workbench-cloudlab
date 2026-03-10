#!/usr/bin/env python3
"""
Generate a AAAA zone file for dns64perf.test domain with individual entries for each IPv4 address in a subnet.

For each IPv4 address in the specified subnet, creates a DNS entry mapping:
    {ipv4_address}.dns64perf.test IN AAAA 64:ff9b::{ipv6_suffix}

The AAAA record uses the well-known prefix 64:ff9b:: for IPv6/IPv4 translation (RFC 6052).

Usage:
    python3 generate_aaaa_zone.py <ipv4_subnet> [output_file]

Example:
    python3 generate_aaaa_zone.py 10.10.0.0/16
    python3 generate_aaaa_zone.py 10.10.0.0/24 zone_file_aaaa
    python3 generate_aaaa_zone.py 192.168.1.0/28 > zone_file_aaaa
"""

import sys
import ipaddress


def ipv4_to_ipv6_suffix(ipv4_addr):
    """Convert an IPv4 address to the IPv6 suffix for RFC 6052 well-known prefix."""
    octets = str(ipv4_addr).split('.')
    # Convert 4 octets to 4 hex bytes
    hex_bytes = ''.join(f'{int(octet):02x}' for octet in octets)
    # Return as IPv6 format: first two bytes : last two bytes
    return f"{hex_bytes[0:4]}:{hex_bytes[4:8]}"


def ipv4_to_domain_name(ipv4_addr):
    """Convert IPv4 address to hyphenated zero-padded domain name format (e.g., 010-000-000-001)."""
    octets = str(ipv4_addr).split('.')
    return '-'.join(f'{int(octet):03d}' for octet in octets)


def generate_zone_file(subnet_str, output_file=None):
    """Generate the AAAA zone file content for the given subnet."""

    try:
        subnet = ipaddress.ip_network(subnet_str, strict=False)
    except ipaddress.AddressValueError as e:
        print(f"Error: Invalid subnet: {e}", file=sys.stderr)
        sys.exit(1)

    if subnet.version != 4:
        print("Error: Only IPv4 subnets are supported", file=sys.stderr)
        sys.exit(1)

    # Zone file header with defaults
    zone_content = f"""$TTL 3600
@   IN  SOA ns1.dns64perf.test. admin.dns64perf.test. (
            2026010701 ; serial (YYYYMMDDnn)
            3600       ; refresh
            1800       ; retry
            604800     ; expire
            3600 )     ; minimum

    IN  NS  ns1.dns64perf.test.
ns1     IN  A   10.10.1.2

"""

    # Add AAAA records for each address in the subnet
    for ip in subnet.hosts():
        ipv6_suffix = ipv4_to_ipv6_suffix(ip)
        ipv6_addr = f"64:ff9b::{ipv6_suffix}"
        domain_name = ipv4_to_domain_name(ip)
        zone_content += f"{domain_name}.dns64perf.test. IN AAAA {ipv6_addr}\n"

    if output_file:
        try:
            with open(output_file, 'w') as f:
                f.write(zone_content)
            num_records = subnet.num_addresses - 2  # Exclude network and broadcast
            print(f"Zone file written to: {output_file} ({num_records} records)")
        except IOError as e:
            print(f"Error writing to file {output_file}: {e}", file=sys.stderr)
            sys.exit(1)
    else:
        print(zone_content, end='')


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 generate_aaaa_zone.py <ipv4_subnet> [output_file]", file=sys.stderr)
        print("\nExample:", file=sys.stderr)
        print("  python3 generate_aaaa_zone.py 10.10.0.0/16", file=sys.stderr)
        print("  python3 generate_aaaa_zone.py 10.10.0.0/24 zone_file_aaaa", file=sys.stderr)
        sys.exit(1)

    subnet = sys.argv[1]
    output_file = sys.argv[2] if len(sys.argv) > 2 else None

    generate_zone_file(subnet, output_file)


if __name__ == "__main__":
    main()
