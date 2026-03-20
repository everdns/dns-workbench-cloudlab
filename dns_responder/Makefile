# DNS Responder - High-performance AF_XDP DNS response generator
# Build targets: userspace binary + XDP eBPF object

CLANG     ?= clang
CC        ?= gcc
CFLAGS     = -O2 -Wall -Wextra -Wno-unused-parameter -pthread -g
LDFLAGS    = -lxdp -lbpf -lpthread -lelf -lz

BPF_CFLAGS = -O2 -g -target bpf -D__TARGET_ARCH_x86

# Source files
SRCS       = src/main.c src/af_xdp.c src/dns.c src/stats.c src/net.c
OBJS       = $(SRCS:.c=.o)
TARGET     = dns_responder

XDP_SRC    = xdp/xdp_dns_redirect.c
XDP_OBJ    = xdp/xdp_dns_redirect.o

# vmlinux.h generation (for BTF-based eBPF includes)
VMLINUX_H  = xdp/vmlinux.h

.PHONY: all clean

all: $(TARGET) $(XDP_OBJ)

# Userspace binary
$(TARGET): $(OBJS)
	$(CC) $(CFLAGS) -o $@ $^ $(LDFLAGS)

src/%.o: src/%.c
	$(CC) $(CFLAGS) -c -o $@ $<

# XDP eBPF object
$(XDP_OBJ): $(XDP_SRC)
	$(CLANG) $(BPF_CFLAGS) -c -o $@ $<

clean:
	rm -f $(TARGET) $(OBJS) $(XDP_OBJ)
