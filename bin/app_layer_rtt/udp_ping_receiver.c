// udp_ping_receiver.c
#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <string.h>
#include <unistd.h>
#include <arpa/inet.h>
#include <sys/socket.h>

#define PING_MAGIC 0x50494E47u  // "PING"

typedef struct {
    uint32_t magic;
    uint64_t seq;
    uint64_t send_ns;
} __attribute__((packed)) ping_pkt_t;

int main(int argc, char *argv[]) {
    if (argc != 4) {
        fprintf(stderr, "Usage: %s <server_ip> <port> <local_bind_ip>\n", argv[0]);
        return 1;
    }

    const char *server_ip = argv[1];
    int port = atoi(argv[2]);
    const char *local_ip = argv[3];

    int sock = socket(AF_INET, SOCK_DGRAM, 0);
    if (sock < 0) {
        perror("socket");
        return 1;
    }

    struct sockaddr_in local = {0};
    local.sin_family = AF_INET;
    local.sin_port = htons(0);

    // if (inet_pton(AF_INET, local_ip, &local.sin_addr) != 1) {
    //     fprintf(stderr, "Invalid local bind IP\n");
    //     close(sock);
    //     return 1;
    // }
        local.sin_addr.s_addr = INADDR_ANY;

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

    const char *reg = "REGISTER";
    if (sendto(sock, reg, strlen(reg), 0,
               (struct sockaddr *)&server, sizeof(server)) < 0) {
        perror("send REGISTER");
        close(sock);
        return 1;
    }

    printf("Registered from local IP %s. Echoing packets...\n", local_ip);

    int flag = 0;
    while (1) {
        ping_pkt_t pkt;
        struct sockaddr_in from;
        socklen_t fromlen = sizeof(from);

        ssize_t n = recvfrom(sock, &pkt, sizeof(pkt), 0,
                             (struct sockaddr *)&from, &fromlen);

        if (n < 0) {
            perror("recvfrom");
            continue;
        }

        if (n != sizeof(pkt)) {
            continue;
        }

        if (pkt.magic != PING_MAGIC) {
            continue;
        }

        sendto(sock, &pkt, sizeof(pkt), 0,
               (struct sockaddr *)&from, fromlen);

        if (flag == 0) {
        printf("got ping seq=%lu from %s:%d\n",
            pkt.seq,
            inet_ntoa(from.sin_addr),
            ntohs(from.sin_port));
        fflush(stdout);
        flag = 1;
        }
    }

    close(sock);
    return 0;
}