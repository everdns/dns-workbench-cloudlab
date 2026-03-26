import argparse
import ipaddress
import json
import os
import random
import string
import math


def make_header(sld):
    return f"""$TTL 3600
@   IN  SOA ns1.{sld}. admin.{sld}. (
            2026010701 ; serial (YYYYMMDDnn)
            3600       ; refresh
            1800       ; retry
            604800     ; expire
            3600 )     ; minimum

    IN  NS  ns1.{sld}.
ns1     IN  A 10.10.1.2
"""

DEFAULT_RECORD_WEIGHTS = {
    'A': 63,
    'AAAA': 20,
    'HTTPS': 8,
    'CNAME': 2,
    'MX': 2,
}

DEFAULTS = {
    'sld': 'workbench.lan',
    'base_subnet': '10.0.0.0',
    'num_records': 16777216,
    'max_records_per_file': 65536,
    'num_ips': 16777216,
    'out_dir': 'output',
}


def generate_interleaved_record_types_pattern(record_counts, types_order):
    record_types = []
    # Cycle through all types repeatedly until lighter types are exhausted
    while any(count > 0 for count in record_counts.values()):
        for rtype in types_order:
            if record_counts[rtype] > 0:
                record_types.append(rtype)
                record_counts[rtype] -= 1
    return record_types

def generate_fqdns_and_ips(num_ips: int, num_records: int, sld: str, base_subnet: str, out_dir: str, max_records_per_file: int, record_weights: dict = DEFAULT_RECORD_WEIGHTS):
    types_order = list(record_weights.keys())
    header = make_header(sld)
    base_subnet_file_str = base_subnet.replace('.', '-')

    # Parse the base subnet to get the starting IP
    network = ipaddress.ip_network(base_subnet, strict=False)
    start_ip = network.network_address

    # Calculate total weight and record counts per type
    total_weight = sum(record_weights.values())
    iters_through_main_record_pattern = num_records // total_weight
    remaining_records_count = num_records % total_weight
    remaining_record_counts = {rtype: int((record_weights[rtype] / total_weight) * remaining_records_count) for rtype in record_weights}

    if sum(remaining_record_counts.values()) < remaining_records_count:
        # If due to rounding we have fewer records than needed, add the remaining ones to the most common type
        most_common = max(record_weights, key=record_weights.get)
        remaining_record_counts[most_common] += remaining_records_count - sum(remaining_record_counts.values())

    primary_pattern = generate_interleaved_record_types_pattern(record_weights.copy(), types_order)
    remaining_pattern = generate_interleaved_record_types_pattern(remaining_record_counts, types_order)
    main_pattern_record_count = iters_through_main_record_pattern * len(primary_pattern)

    # Calculate file boundaries
    num_files = math.ceil(num_records / max_records_per_file)

    main_zone_filename = f"db.{sld}"
    single_file = num_files == 1

    dnsperf_file = open(os.path.join(out_dir, f"dnsperf_input_{base_subnet_file_str}_{num_records}"), 'w')
    dnspyre_file = open(os.path.join(out_dir, f"dnspyre_input_{base_subnet_file_str}_{num_records}"), 'w')

    try:
        file_idx = 0
        file_record_count = 0
        zone_file = None
        cur_pattern = primary_pattern
        pattern_idx = 0

        # Generate records and write to files
        for i in range(num_records):
            # Open new zone file if needed
            if file_record_count == 0:
                if zone_file:
                    zone_file.close()
                if single_file:
                    zone_file = open(os.path.join(out_dir, main_zone_filename), 'w')
                    zone_file.write(header)
                else:
                    part_filename = f"{main_zone_filename}.part{file_idx + 1}"
                    zone_file = open(os.path.join(out_dir, part_filename), 'w')

            ip_index = i % num_ips + 1  # +1 to avoid using the network address (first IP)
            ip_addr = start_ip + ip_index

            # Create FQDN: ip-addr.sld (zero-padded octets with dashes)
            octets = str(ip_addr).split('.')
            padded_octets = '-'.join(f"{int(octet):03d}" for octet in octets)
            fqdn = f"{padded_octets}.{sld}"

            # Determine which pattern to use
            if i == main_pattern_record_count:
                cur_pattern = remaining_pattern
                pattern_idx = 0
            elif pattern_idx >= len(cur_pattern):
                pattern_idx = 0
            record_type = cur_pattern[pattern_idx]
            pattern_idx += 1

            # Generate record data based on type
            if record_type == 'A':
                data = str(ip_addr)
                entry = get_zone_file_entry(fqdn, data, 'A')
            elif record_type == 'AAAA':
                # Generate IPv6 address from IPv4 address (RFC 6052 format)
                octets = str(ip_addr).split('.')
                hex_bytes = ''.join(f'{int(octet):02x}' for octet in octets)
                ipv6_suffix = f"{hex_bytes[0:4]}:{hex_bytes[4:8]}"
                ipv6_addr = ipaddress.ip_address(f"2001:db8::{ipv6_suffix}")
                entry = get_zone_file_entry(fqdn, str(ipv6_addr), 'AAAA')
            elif record_type == 'MX':
                priority = 10 + (i % 10)
                mail_server = f"mail{i % 10}.{sld}"
                entry = get_zone_file_entry(fqdn, (priority, mail_server), 'MX')
            elif record_type == 'HTTPS':
                priority = 1
                alpn = "h2,h3"
                entry = get_zone_file_entry(fqdn, (priority, alpn), 'HTTPS')
            elif record_type == 'CNAME':
                target_fqdn = f"canonical-{padded_octets}.{sld}"
                entry = get_zone_file_entry(fqdn, target_fqdn, 'CNAME')
            elif record_type == 'ANY':
                # For ANY, we can just create an A record as a placeholder since ANY is not actually stored in zone files
                data = str(ip_addr)
                entry = get_zone_file_entry(fqdn, data, 'A')
            elif record_type == 'NS':
                ns_target = f"ns{i % 10 + 1}.{sld}"
                entry = get_zone_file_entry(fqdn, ns_target, 'NS')
            elif record_type == 'TXT':
                txt_data = ''.join(random.choices(string.ascii_letters + string.digits, k=20))
                entry = get_zone_file_entry(fqdn, txt_data, 'TXT')
            else:
                # Default to A record if type is unrecognized
                data = str(ip_addr)
                entry = get_zone_file_entry(fqdn, data, 'A')

            # Write to zone file
            zone_file.write(entry)
            file_record_count += 1

            # Write to dnsperf file
            dnsperf_file.write(get_dnsperf_entry(fqdn, record_type))

            # Write to dnspyre file
            dnspyre_file.write(get_dnspyre_entry(fqdn, record_type))

            # Move to next zone file if current one is full
            if file_record_count >= max_records_per_file and file_idx < num_files - 1:
                file_idx += 1
                file_record_count = 0

    finally:
        if zone_file:
            zone_file.close()
        dnsperf_file.close()
        dnspyre_file.close()

    # For multi-file: write main zone file with header + $INCLUDE directives
    if not single_file:
        with open(os.path.join(out_dir, main_zone_filename), 'w') as main_file:
            main_file.write(header)
            for part_num in range(1, num_files + 1):
                main_file.write(f'$INCLUDE "{main_zone_filename}.part{part_num}"\n')

    return num_files

def get_dnsperf_entry(fqdn, record_type='A'):
    return f"{fqdn}.  {record_type}\n"

def get_dnspyre_entry(fqdn):
    return f"{fqdn}."

def get_zone_file_entry(fqdn, data, record_type='A'):
    if record_type == 'A':
        return f"{fqdn}.  IN  A  {data}\n"
    elif record_type == 'AAAA':
        return f"{fqdn}.  IN  AAAA  {data}\n"
    elif record_type == 'MX':
        priority, mail_server = data
        return f"{fqdn}.  IN  MX  {priority} {mail_server}.\n"
    elif record_type == 'HTTPS':
        priority, alpn = data
        return f"{fqdn}.  IN  HTTPS  {priority} . alpn=\"{alpn}\"\n"
    elif record_type == 'CNAME':
        return f"{fqdn}.  IN  CNAME  {data}.\n"
    elif record_type == 'NS':  
        return f"{fqdn}.  IN  NS  {data}.\n"
    elif record_type == 'TXT':
        return f"{fqdn}.  IN  TXT  \"{data}\"\n"
    else:
        return f"{fqdn}.  IN  {record_type}  {data}\n"

def load_config(config_path):
    with open(config_path) as f:
        return json.load(f)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate DNS zone files and dnsperf input")
    parser.add_argument("--config", default="config.json", help="Path to JSON config file")
    parser.add_argument("--sld", default=None, help="Second-level domain (default: workbench.lan)")
    parser.add_argument("--base-subnet", default=None, help="Base subnet address (default: 10.0.0.0)")
    parser.add_argument("--num-records", type=int, default=None, help="Number of DNS records to generate (default: 16777216)")
    parser.add_argument("--max-records-per-file", type=int, default=None, help="Max records per zone file (default: 65536)")
    parser.add_argument("--num-ips", type=int, default=None, help="Number of unique IPs (default: 16777216)")
    parser.add_argument("--out-dir", default=None, help="Output directory (default: output)")
    args = parser.parse_args()

    # Start with defaults, layer config file values, then CLI args
    config = dict(DEFAULTS)
    record_weights = DEFAULT_RECORD_WEIGHTS

    if args.config:
        file_config = load_config(args.config)
        record_weights = file_config.pop('record_weights', DEFAULT_RECORD_WEIGHTS)
        config.update({k: v for k, v in file_config.items() if k != 'config'})

    # CLI args override config file values (only if explicitly provided)
    cli_overrides = {k: v for k, v in vars(args).items() if v is not None and k != 'config'}
    config.update(cli_overrides)

    if not os.path.exists(config['out_dir']):
        os.makedirs(config['out_dir'])

    
    print(f"Generating {config['num_records']} records with the following type distribution:")
    for rtype, weight in record_weights.items():
        print(f"{rtype}: {weight}")
    print("Using Config:")
    print(config)

    num_files = generate_fqdns_and_ips(
        config['num_ips'], config['num_records'], config['sld'],
        config['base_subnet'], config['out_dir'], config['max_records_per_file'],
        record_weights,
    )
    print(f"Created {config['num_records']} domain/ip pairs across {num_files} zone file(s)")