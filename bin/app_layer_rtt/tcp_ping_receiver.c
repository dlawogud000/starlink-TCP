// tcp_ping_receiver.c
#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <string.h>
#include <unistd.h>
#include <errno.h>
#include <arpa/inet.h>
#include <sys/socket.h>
#include <netinet/tcp.h>

#define PING_MAGIC 0x50494E47u

typedef struct {
    uint32_t magic;
    uint64_t seq;
    uint64_t send_ns;
} __attribute__((packed)) ping_pkt_t;

static ssize_t read_all(int fd, void *buf, size_t len) {
    char *p = (char *)buf;
    size_t left = len;

    while (left > 0) {
        ssize_t n = recv(fd, p, left, 0);
        if (n < 0) {
            if (errno == EINTR) continue;
            return -1;
        }
        if (n == 0) return 0;

        p += n;
        left -= n;
    }

    return len;
}

static ssize_t write_all(int fd, const void *buf, size_t len) {
    const char *p = (const char *)buf;
    size_t left = len;

    while (left > 0) {
        ssize_t n = send(fd, p, left, 0);
        if (n < 0) {
            if (errno == EINTR) continue;
            return -1;
        }
        if (n == 0) return -1;

        p += n;
        left -= n;
    }

    return len;
}

int main(int argc, char *argv[]) {
    if (argc != 4) {
        fprintf(stderr, "Usage: %s <server_ip> <port> <local_bind_ip>\n", argv[0]);
        return 1;
    }

    const char *server_ip = argv[1];
    int port = atoi(argv[2]);
    const char *local_ip = argv[3];

    int sock = socket(AF_INET, SOCK_STREAM, 0);
    if (sock < 0) {
        perror("socket");
        return 1;
    }

    int one = 1;
    setsockopt(sock, IPPROTO_TCP, TCP_NODELAY, &one, sizeof(one));

    struct sockaddr_in local = {0};
    local.sin_family = AF_INET;
    local.sin_port = htons(0);

    if (strcmp(local_ip, "any") == 0 || strcmp(local_ip, "0.0.0.0") == 0) {
        local.sin_addr.s_addr = INADDR_ANY;
    } else {
        if (inet_pton(AF_INET, local_ip, &local.sin_addr) != 1) {
            fprintf(stderr, "Invalid local bind IP\n");
            close(sock);
            return 1;
        }
    }

    if (bind(sock, (struct sockaddr *)&local, sizeof(local)) < 0) {
        perror("bind local IP");
        close(sock);
        return 1;
    }

    struct sockaddr_in server = {0};
    server.sin_family = AF_INET;
    server.sin_port = htons(port);

    if (inet_pton(AF_INET, server_ip, &server.sin_addr) != 1) {
        fprintf(stderr, "Invalid server IP\n");
        close(sock);
        return 1;
    }

    if (connect(sock, (struct sockaddr *)&server, sizeof(server)) < 0) {
        perror("connect");
        close(sock);
        return 1;
    }

    printf("Connected from local IP %s. Echoing packets...\n", local_ip);

    int printed = 0;

    while (1) {
        ping_pkt_t pkt;

        ssize_t n = read_all(sock, &pkt, sizeof(pkt));
        if (n < 0) {
            perror("recv");
            break;
        }

        if (n == 0) {
            printf("Server closed connection\n");
            break;
        }

        if (pkt.magic != PING_MAGIC) {
            continue;
        }

        if (write_all(sock, &pkt, sizeof(pkt)) < 0) {
            perror("send");
            break;
        }

        if (!printed) {
            printf("got ping seq=%lu\n", pkt.seq);
            fflush(stdout);
            printed = 1;
        }
    }

    close(sock);
    return 0;
}