#include "af_xdp.h"
#include "dns.h"
#include "net.h"

#include <errno.h>
#include <poll.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/socket.h>
#include <time.h>
#include <unistd.h>

#define TS_INITIAL_CAPACITY (1 << 20) /* 1M entries */

/* Pop a free frame address from the stack. Returns -1 if empty. */
static inline int64_t alloc_frame(struct xsk_info *xsk)
{
	if (__builtin_expect(xsk->free_count == 0, 0))
		return -1;
	return (int64_t)xsk->free_stack[--xsk->free_count];
}

/* Push a frame address back to the free stack. */
static inline void free_frame(struct xsk_info *xsk, uint64_t addr)
{
	xsk->free_stack[xsk->free_count++] = addr;
}

int xsk_umem_init(struct xsk_info *xsk, uint32_t frame_count, uint32_t frame_size)
{
	size_t umem_size = (size_t)frame_count * frame_size;

	xsk->frame_count = frame_count;
	xsk->frame_size = frame_size;

	/* Allocate UMEM memory region */
	xsk->umem_area = mmap(NULL, umem_size, PROT_READ | PROT_WRITE,
			      MAP_PRIVATE | MAP_ANONYMOUS | MAP_HUGETLB, -1, 0);
	if (xsk->umem_area == MAP_FAILED) {
		/* Fallback to regular pages if hugepages unavailable */
		xsk->umem_area = mmap(NULL, umem_size, PROT_READ | PROT_WRITE,
				      MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
		if (xsk->umem_area == MAP_FAILED) {
			fprintf(stderr, "ERROR: mmap UMEM failed: %s\n",
				strerror(errno));
			return -1;
		}
	}

	/* Create UMEM object */
	struct xsk_umem_config cfg = {
		.fill_size = DEFAULT_RING_SIZE,
		.comp_size = DEFAULT_RING_SIZE,
		.frame_size = frame_size,
		.frame_headroom = 0,
		.flags = 0,
	};

	int ret = xsk_umem__create(&xsk->umem, xsk->umem_area, umem_size,
				   &xsk->fill, &xsk->comp, &cfg);
	if (ret) {
		fprintf(stderr, "ERROR: xsk_umem__create failed: %s\n",
			strerror(-ret));
		munmap(xsk->umem_area, umem_size);
		return -1;
	}

	/* Initialize free frame stack */
	xsk->free_stack = malloc(frame_count * sizeof(uint64_t));
	if (!xsk->free_stack) {
		fprintf(stderr, "ERROR: malloc free_stack failed\n");
		xsk_umem__delete(xsk->umem);
		munmap(xsk->umem_area, umem_size);
		return -1;
	}

	xsk->free_count = 0;
	for (uint32_t i = 0; i < frame_count; i++)
		xsk->free_stack[xsk->free_count++] = (uint64_t)i * frame_size;

	return 0;
}

int xsk_socket_init(struct xsk_info *xsk, const char *ifname,
		    int queue_id, uint32_t xdp_flags, uint16_t bind_flags)
{
	struct xsk_socket_config cfg = {
		.rx_size = DEFAULT_RING_SIZE,
		.tx_size = DEFAULT_RING_SIZE,
		.xdp_flags = xdp_flags,
		.bind_flags = bind_flags,
		.libbpf_flags = XSK_LIBBPF_FLAGS__INHIBIT_PROG_LOAD,
	};

	xsk->queue_id = queue_id;

	int ret = xsk_socket__create(&xsk->xsk, ifname, queue_id,
				     xsk->umem, &xsk->rx, &xsk->tx, &cfg);
	if (ret) {
		fprintf(stderr, "ERROR: xsk_socket__create queue %d failed: %s\n",
			queue_id, strerror(-ret));
		return -1;
	}

	return 0;
}

void xsk_populate_fill_ring(struct xsk_info *xsk)
{
	uint32_t idx;
	uint32_t count = DEFAULT_RING_SIZE;

	if (xsk_ring_prod__reserve(&xsk->fill, count, &idx) != count) {
		fprintf(stderr, "WARNING: could not fully populate fill ring\n");
		return;
	}

	for (uint32_t i = 0; i < count; i++) {
		int64_t addr = alloc_frame(xsk);
		if (addr < 0)
			break;
		*xsk_ring_prod__fill_addr(&xsk->fill, idx + i) = (uint64_t)addr;
	}

	xsk_ring_prod__submit(&xsk->fill, count);
}

/*
 * Reclaim completed TX frames and push them back to the free stack.
 */
static inline void reclaim_completion(struct xsk_info *xsk)
{
	uint32_t idx;
	uint32_t completed = xsk_ring_cons__peek(&xsk->comp, DEFAULT_BATCH_SIZE, &idx);

	if (completed == 0)
		return;

	for (uint32_t i = 0; i < completed; i++) {
		uint64_t addr = *xsk_ring_cons__comp_addr(&xsk->comp, idx + i);
		free_frame(xsk, addr);
	}

	xsk_ring_cons__release(&xsk->comp, completed);
}

/*
 * Refill the fill ring from the free stack.
 */
static inline void refill_fill_ring(struct xsk_info *xsk)
{
	uint32_t idx;
	uint32_t to_fill = xsk->free_count;

	if (to_fill == 0)
		return;

	/* Don't overfill */
	if (to_fill > DEFAULT_RING_SIZE / 2)
		to_fill = DEFAULT_RING_SIZE / 2;

	uint32_t reserved = xsk_ring_prod__reserve(&xsk->fill, to_fill, &idx);
	if (reserved == 0)
		return;

	for (uint32_t i = 0; i < reserved; i++) {
		int64_t addr = alloc_frame(xsk);
		if (addr < 0)
			break;
		*xsk_ring_prod__fill_addr(&xsk->fill, idx + i) = (uint64_t)addr;
	}

	xsk_ring_prod__submit(&xsk->fill, reserved);
}

static void kick_tx(struct xsk_info *xsk)
{
	sendto(xsk_socket__fd(xsk->xsk), NULL, 0, MSG_DONTWAIT, NULL, 0);
}

static inline void ts_record(struct ts_buffer *ts, const struct timespec *start)
{
	if (__builtin_expect(ts->count == ts->capacity, 0)) {
		uint64_t new_cap = ts->capacity * 2;
		uint64_t *new_data = realloc(ts->data,
					     new_cap * sizeof(uint64_t));
		if (!new_data)
			return; /* drop timestamp on OOM */
		ts->data = new_data;
		ts->capacity = new_cap;
	}

	struct timespec now;
	clock_gettime(CLOCK_MONOTONIC, &now);
	uint64_t now_ns = (uint64_t)now.tv_sec * 1000000000ULL + now.tv_nsec;
	uint64_t start_ns = (uint64_t)start->tv_sec * 1000000000ULL
			    + start->tv_nsec;
	ts->data[ts->count++] = now_ns - start_ns;
}

void *worker_thread(void *arg)
{
	struct worker_ctx *ctx = (struct worker_ctx *)arg;
	struct xsk_info *xsk = &ctx->xsk;
	int batch_size = ctx->batch_size;

	/* Initialize timestamp buffer if recording */
	if (ctx->record_timestamps) {
		ctx->ts.capacity = TS_INITIAL_CAPACITY;
		ctx->ts.count = 0;
		ctx->ts.data = malloc(ctx->ts.capacity * sizeof(uint64_t));
		if (!ctx->ts.data) {
			fprintf(stderr, "WARNING: queue %d: timestamp alloc "
				"failed, disabling\n", ctx->cpu_id);
			ctx->record_timestamps = 0;
		}
	}

	while (__builtin_expect(*ctx->running, 1)) {
		uint32_t rx_idx = 0;

		/* Step 1: Reclaim completed TX frames */
		reclaim_completion(xsk);

		/* Step 2: Refill the fill ring */
		refill_fill_ring(xsk);

		/* Step 3: Peek RX batch */
		uint32_t rx_count = xsk_ring_cons__peek(&xsk->rx, batch_size,
							&rx_idx);
		if (rx_count == 0) {
			/* Brief yield when no packets -- avoids burning CPU
			 * when load generator hasn't started yet */
			struct pollfd fds = {
				.fd = xsk_socket__fd(xsk->xsk),
				.events = POLLIN,
			};
			poll(&fds, 1, 10);
			continue;
		}

		/* Step 4: Process each packet, collect TX-ready frames */
		uint64_t tx_addrs[DEFAULT_BATCH_SIZE];
		uint32_t tx_lens[DEFAULT_BATCH_SIZE];
		uint32_t tx_count = 0;

		for (uint32_t i = 0; i < rx_count; i++) {
			const struct xdp_desc *rx_desc;
			rx_desc = xsk_ring_cons__rx_desc(&xsk->rx, rx_idx + i);

			uint64_t addr = rx_desc->addr;
			uint32_t pkt_len = rx_desc->len;
			uint8_t *pkt = xsk_umem__get_data(xsk->umem_area, addr);

			/* Prefetch next packet */
			if (i + 1 < rx_count) {
				const struct xdp_desc *next;
				next = xsk_ring_cons__rx_desc(&xsk->rx,
							     rx_idx + i + 1);
				__builtin_prefetch(
					xsk_umem__get_data(xsk->umem_area,
							   next->addr),
					1, 3);
			}

			ctx->stats.rx_packets++;
			ctx->stats.rx_bytes += pkt_len;

			if (ctx->record_timestamps)
				ts_record(&ctx->ts, &ctx->start_time);

			uint16_t qtype = 0;
			uint32_t new_len = process_dns_packet(pkt, pkt_len,
							      &qtype);

			if (__builtin_expect(new_len == 0, 0)) {
				ctx->stats.parse_errors++;
				free_frame(xsk, addr);
				continue;
			}

			/* Update per-type stats */
			switch (qtype) {
			case DNS_TYPE_A:     ctx->stats.type_a++;     break;
			case DNS_TYPE_AAAA:  ctx->stats.type_aaaa++;  break;
			case DNS_TYPE_CNAME: ctx->stats.type_cname++; break;
			case DNS_TYPE_MX:    ctx->stats.type_mx++;    break;
			case DNS_TYPE_HTTPS: ctx->stats.type_https++; break;
			default:             ctx->stats.type_other++; break;
			}

			tx_addrs[tx_count] = addr;
			tx_lens[tx_count] = new_len;
			tx_count++;
		}

		/* Release RX ring */
		xsk_ring_cons__release(&xsk->rx, rx_count);

		/* Step 5: Reserve exactly tx_count TX slots and submit */
		if (tx_count > 0) {
			uint32_t tx_idx_reserved = 0;
			while (xsk_ring_prod__reserve(&xsk->tx, tx_count,
						      &tx_idx_reserved) < tx_count) {
				kick_tx(xsk);
				reclaim_completion(xsk);
			}

			for (uint32_t i = 0; i < tx_count; i++) {
				struct xdp_desc *tx_desc;
				tx_desc = xsk_ring_prod__tx_desc(&xsk->tx,
								 tx_idx_reserved + i);
				tx_desc->addr = tx_addrs[i];
				tx_desc->len = tx_lens[i];
			}

			xsk_ring_prod__submit(&xsk->tx, tx_count);
			kick_tx(xsk);
			ctx->stats.tx_packets += tx_count;
			for (uint32_t i = 0; i < tx_count; i++)
				ctx->stats.tx_bytes += tx_lens[i];
		}
	}

	return NULL;
}

void xsk_cleanup(struct xsk_info *xsk)
{
	if (xsk->xsk)
		xsk_socket__delete(xsk->xsk);
	if (xsk->umem)
		xsk_umem__delete(xsk->umem);
	if (xsk->umem_area) {
		size_t size = (size_t)xsk->frame_count * xsk->frame_size;
		munmap(xsk->umem_area, size);
	}
	free(xsk->free_stack);
}
