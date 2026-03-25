#ifndef AF_XDP_H
#define AF_XDP_H

#include <stdint.h>
#include <time.h>
#include <xdp/xsk.h>

#include "stats.h"

/* Defaults */
#define DEFAULT_FRAME_SIZE   2048
#define DEFAULT_FRAME_COUNT  4096
#define DEFAULT_BATCH_SIZE   64
#define DEFAULT_RING_SIZE    2048

/* Maximum free frame stack depth */
#define MAX_FREE_FRAMES      DEFAULT_FRAME_COUNT

/* Per-queue AF_XDP socket context */
struct xsk_info {
	struct xsk_socket      *xsk;
	struct xsk_ring_cons    rx;
	struct xsk_ring_prod    tx;
	struct xsk_ring_cons    comp;
	struct xsk_ring_prod    fill;
	struct xsk_umem        *umem;
	void                   *umem_area;
	uint32_t                frame_count;
	uint32_t                frame_size;
	/* Stack-based free frame allocator */
	uint64_t               *free_stack;
	uint32_t                free_count;
	int                     queue_id;
};

/* Dynamic timestamp buffer for per-packet arrival recording */
struct ts_buffer {
	uint64_t *data;       /* nanosecond timestamps (relative to start) */
	uint64_t  count;
	uint64_t  capacity;
};

/* Worker thread context */
struct worker_ctx {
	struct xsk_info         xsk;
	struct thread_stats     stats;
	int                     cpu_id;
	volatile int           *running;
	int                     batch_size;
	int                     record_timestamps; /* 0=off, 1=all, 2=min/max */
	struct timespec         start_time;
	struct ts_buffer        ts;
	uint64_t                ts_min_ns;  /* earliest RX timestamp (ns since start) */
	uint64_t                ts_max_ns;  /* latest RX timestamp (ns since start) */
} __attribute__((aligned(64)));

/*
 * Initialize UMEM for a single queue.
 * Allocates umem_area, creates UMEM object, populates free frame stack.
 */
int xsk_umem_init(struct xsk_info *xsk, uint32_t frame_count, uint32_t frame_size);

/*
 * Create and bind an AF_XDP socket to a specific interface queue.
 * The UMEM must be initialized first via xsk_umem_init().
 *
 * xdp_flags: XDP_FLAGS_DRV_MODE, XDP_FLAGS_SKB_MODE, etc.
 * bind_flags: XDP_ZEROCOPY, XDP_COPY, or 0
 */
int xsk_socket_init(struct xsk_info *xsk, const char *ifname,
		    int queue_id, uint32_t xdp_flags, uint16_t bind_flags);

/*
 * Populate the fill ring with initial frames.
 * Must be called after socket creation, before receiving packets.
 */
void xsk_populate_fill_ring(struct xsk_info *xsk);

/*
 * Worker thread entry point.
 * Runs the RX→process→TX loop until *running becomes 0.
 */
void *worker_thread(void *arg);

/*
 * Cleanup AF_XDP socket and UMEM resources.
 */
void xsk_cleanup(struct xsk_info *xsk);

#endif /* AF_XDP_H */
