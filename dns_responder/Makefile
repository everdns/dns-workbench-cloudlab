# DNS Responder - High-performance AF_XDP DNS response generator
# Build targets: userspace binary + XDP eBPF object

CLANG     ?= clang
CC        ?= gcc
CFLAGS     = -O2 -Wall -Wextra -Wno-unused-parameter -pthread -g
LDFLAGS    = -lxdp -lbpf -lpthread -lelf -lz

# eBPF target arch and the multiarch include dir holding <asm/types.h>.
# clang -target bpf does not search the multiarch dir on its own.
BPF_ARCH   := $(shell uname -m | sed 's/x86_64/x86/; s/aarch64/arm64/; \
                                      s/ppc64le/powerpc/; s/s390x/s390/; \
                                      s/arm.*/arm/')
MULTIARCH  := $(shell $(CC) -print-multiarch 2>/dev/null)

BPF_CFLAGS = -O2 -g -target bpf -D__TARGET_ARCH_$(BPF_ARCH) \
             $(if $(MULTIARCH),-I/usr/include/$(MULTIARCH))

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
