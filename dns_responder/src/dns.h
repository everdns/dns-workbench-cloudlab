#ifndef DNS_H
#define DNS_H

#include <stdint.h>
#include <stddef.h>

/* DNS header (12 bytes) */
struct dns_hdr {
	uint16_t id;
	uint16_t flags;
	uint16_t qdcount;
	uint16_t ancount;
	uint16_t nscount;
	uint16_t arcount;
} __attribute__((packed));

/* DNS query types */
#define DNS_TYPE_A      1
#define DNS_TYPE_AAAA   28
#define DNS_TYPE_CNAME  5
#define DNS_TYPE_MX     15
#define DNS_TYPE_HTTPS  65

/* DNS response flags: QR=1, AA=1, RCODE=0 */
#define DNS_FLAGS_RESPONSE  0x8400
/* DNS response flags: QR=1, AA=1, RCODE=3 (NXDOMAIN) */
#define DNS_FLAGS_NXDOMAIN  0x8403
/* DNS response flags: QR=1, AA=1, RCODE=4 (NOTIMP) */
#define DNS_FLAGS_NOTIMP    0x8404

#define DNS_CLASS_IN    1
#define DNS_TTL         3600

/* Maximum answer section size (pre-serialized) */
#define DNS_MAX_ANSWER_LEN 64

/* Maximum number of supported query types */
#define DNS_NUM_TEMPLATES  5

/* Pre-serialized answer section for a specific QTYPE */
struct dns_template {
	uint16_t qtype;
	uint8_t  answer[DNS_MAX_ANSWER_LEN];
	uint16_t answer_len;
};

/* Global template table */
extern struct dns_template dns_templates[DNS_NUM_TEMPLATES];

/*
 * Initialize precomputed DNS response templates.
 * Must be called once at startup before processing packets.
 */
void dns_templates_init(void);

/*
 * Process a DNS packet in-place: swap headers, build response.
 * Returns new total packet length, or 0 on parse error.
 *
 * pkt:  pointer to start of Ethernet frame
 * len:  total frame length
 * qtype_out: if non-NULL, set to the query type for stats
 */
uint32_t process_dns_packet(uint8_t *pkt, uint32_t len, uint16_t *qtype_out);

/*
 * Ultra-fast NXDOMAIN responder: swap headers, set RCODE=3, return same length.
 * Skips all QNAME scanning and answer building for maximum throughput.
 * Returns packet length on success, 0 on error.
 */
uint32_t process_nxdomain_packet(uint8_t *pkt, uint32_t len);

#endif /* DNS_H */
