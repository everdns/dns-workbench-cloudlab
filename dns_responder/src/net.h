#ifndef NET_H
#define NET_H

#include <stdint.h>
#include <string.h>

/* Packed network header structures */

struct eth_hdr {
	uint8_t  dst[6];
	uint8_t  src[6];
	uint16_t proto;
} __attribute__((packed));

struct ipv4_hdr {
	uint8_t  ihl_ver;
	uint8_t  tos;
	uint16_t tot_len;
	uint16_t id;
	uint16_t frag_off;
	uint8_t  ttl;
	uint8_t  protocol;
	uint16_t check;
	uint32_t saddr;
	uint32_t daddr;
} __attribute__((packed));

struct udp_hdr {
	uint16_t source;
	uint16_t dest;
	uint16_t len;
	uint16_t check;
} __attribute__((packed));

/* Swap Ethernet src/dst MACs in-place */
static inline void eth_swap(struct eth_hdr *eth)
{
	uint8_t tmp[6];
	memcpy(tmp, eth->dst, 6);
	memcpy(eth->dst, eth->src, 6);
	memcpy(eth->src, tmp, 6);
}

/* Swap IPv4 src/dst addresses in-place */
static inline void ipv4_swap(struct ipv4_hdr *ip)
{
	uint32_t tmp = ip->saddr;
	ip->saddr = ip->daddr;
	ip->daddr = tmp;
}

/* Swap UDP src/dst ports in-place */
static inline void udp_swap(struct udp_hdr *udp)
{
	uint16_t tmp = udp->source;
	udp->source = udp->dest;
	udp->dest = tmp;
}

/*
 * Incremental IP checksum update per RFC 1624.
 * old_val and new_val are in network byte order.
 */
static inline void ip_checksum_update(uint16_t *check,
				      uint16_t old_val, uint16_t new_val)
{
	uint32_t sum;
	sum = (~(*check) & 0xFFFF) + (~old_val & 0xFFFF) + new_val;
	sum = (sum >> 16) + (sum & 0xFFFF);
	sum += (sum >> 16);
	*check = ~sum & 0xFFFF;
}

/*
 * Recompute IP header checksum from scratch.
 * Used when multiple fields change (src+dst IP swap + length change).
 */
static inline void ip_checksum_recompute(struct ipv4_hdr *ip)
{
	uint32_t sum = 0;
	const uint8_t *raw = (const uint8_t *)ip;
	int len = (ip->ihl_ver & 0x0F) * 4; /* IHL in bytes */

	ip->check = 0;
	for (int i = 0; i < len - 1; i += 2) {
		uint16_t word;
		memcpy(&word, raw + i, 2);
		sum += word;
	}
	sum = (sum >> 16) + (sum & 0xFFFF);
	sum += (sum >> 16);
	ip->check = ~sum & 0xFFFF;
}

#endif /* NET_H */
