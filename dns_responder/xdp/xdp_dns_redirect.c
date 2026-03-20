// SPDX-License-Identifier: GPL-2.0
/*
 * XDP program for DNS responder: filter UDP port 53 packets and redirect
 * them to AF_XDP sockets. All other traffic passes through normally.
 */

#include <linux/bpf.h>
#include <linux/if_ether.h>
#include <linux/ip.h>
#include <linux/ipv6.h>
#include <linux/udp.h>
#include <bpf/bpf_helpers.h>
#include <bpf/bpf_endian.h>

#define DNS_PORT 53

struct {
	__uint(type, BPF_MAP_TYPE_XSKMAP);
	__uint(max_entries, 64);
	__type(key, __u32);
	__type(value, __u32);
} xsks_map SEC(".maps");

static __always_inline int parse_udp_port(void *data, void *data_end,
					  __u32 l3_offset, __u16 *dst_port)
{
	struct udphdr *udp = data + l3_offset;

	if ((void *)(udp + 1) > data_end)
		return -1;

	*dst_port = udp->dest;
	return 0;
}

SEC("xdp")
int xdp_dns_redirect(struct xdp_md *ctx)
{
	void *data = (void *)(long)ctx->data;
	void *data_end = (void *)(long)ctx->data_end;
	struct ethhdr *eth = data;
	__u16 dst_port;
	__u32 l3_offset;

	if ((void *)(eth + 1) > data_end)
		return XDP_PASS;

	/* IPv4 UDP */
	if (eth->h_proto == bpf_htons(ETH_P_IP)) {
		struct iphdr *ip = (void *)(eth + 1);

		if ((void *)(ip + 1) > data_end)
			return XDP_PASS;

		if (ip->protocol != IPPROTO_UDP)
			return XDP_PASS;

		l3_offset = sizeof(*eth) + (ip->ihl * 4);

		if (parse_udp_port(data, data_end, l3_offset, &dst_port) < 0)
			return XDP_PASS;

		if (dst_port == bpf_htons(DNS_PORT))
			return bpf_redirect_map(&xsks_map,
						ctx->rx_queue_index,
						XDP_PASS);
	}
	/* IPv6 UDP */
	else if (eth->h_proto == bpf_htons(ETH_P_IPV6)) {
		struct ipv6hdr *ip6 = (void *)(eth + 1);

		if ((void *)(ip6 + 1) > data_end)
			return XDP_PASS;

		if (ip6->nexthdr != IPPROTO_UDP)
			return XDP_PASS;

		l3_offset = sizeof(*eth) + sizeof(*ip6);

		if (parse_udp_port(data, data_end, l3_offset, &dst_port) < 0)
			return XDP_PASS;

		if (dst_port == bpf_htons(DNS_PORT))
			return bpf_redirect_map(&xsks_map,
						ctx->rx_queue_index,
						XDP_PASS);
	}

	return XDP_PASS;
}

char _license[] SEC("license") = "GPL";
