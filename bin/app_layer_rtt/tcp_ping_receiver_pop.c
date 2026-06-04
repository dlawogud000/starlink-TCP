// tcp_ping_receiver_with_pop_auto.c
// TCP app-layer ping receiver + concurrent POP ICMP ping logger.
//
// POP IP is resolved at runtime by executing:
//   GET_POP_IP_SCRIPT <server_ip>
//
// Usage:
//   ./tcp_ping_receiver <server_ip> <port> <local_bind_ip> [pop_log]
//
// Example:
//   ./tcp_ping_receiver 165.194.35.203 40075 192.168.1.150 ./pop_ping.log
//
// Hardcoded POP ping settings:
//   POP_PING_IFACE
//   POP_PING_INTERVAL_SEC
//   GET_POP_IP_SCRIPT
//
// If the get_pop_ip script is in another path, change GET_POP_IP_SCRIPT below
// or compile with:
//   gcc -DGET_POP_IP_SCRIPT=\"/path/to/get_pop_ip.sh\" ...

#define _GNU_SOURCE

#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <string.h>
#include <unistd.h>
#include <errno.h>
#include <signal.h>
#include <ctype.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <arpa/inet.h>
#include <sys/socket.h>
#include <netinet/tcp.h>
#include <time.h>
#include <sys/stat.h>

#define POP_OUTPUT_BASE "./logs"
#define POP_PLOT_SCRIPT "/home/dlawogud/git/starlink-TCP/graph/pop_ping.py"


#define PING_MAGIC 0x50494E47u

#ifndef POP_PING_IFACE
#define POP_PING_IFACE "enx588694fda289"
#endif

#ifndef POP_PING_INTERVAL_SEC
#define POP_PING_INTERVAL_SEC "0.01"
#endif

#ifndef GET_POP_IP_SCRIPT
#define GET_POP_IP_SCRIPT "./get_pop_ip.sh"
#endif

#ifndef DEFAULT_POP_LOG
#define DEFAULT_POP_LOG "pop_ping.log"
#endif

#define POP_PLOT_SCRIPT "/home/dlawogud/git/starlink-TCP/graph/pop_ping.py"
#define POP_PLOT_OUT    "pop_ping.png"

typedef struct {
    uint32_t magic;
    uint64_t seq;
    uint64_t send_ns;
} __attribute__((packed)) ping_pkt_t;

static volatile sig_atomic_t stop_requested = 0;
static pid_t pop_ping_pid = -1;

static void handle_signal(int signo) {
    (void)signo;
    stop_requested = 1;
}

static void trim_whitespace(char *s) {
    if (s == NULL) return;

    char *start = s;
    while (*start && isspace((unsigned char)*start)) start++;

    if (start != s) {
        memmove(s, start, strlen(start) + 1);
    }

    size_t len = strlen(s);
    while (len > 0 && isspace((unsigned char)s[len - 1])) {
        s[len - 1] = '\0';
        len--;
    }
}

static int get_pop_ip_from_script(const char *server_ip, char *buf, size_t buflen) {
    if (server_ip == NULL || buf == NULL || buflen == 0) {
        return -1;
    }

    char cmd[1024];
    snprintf(cmd, sizeof(cmd), "'%s' '%s'", GET_POP_IP_SCRIPT, server_ip);

    FILE *fp = popen(cmd, "r");
    if (fp == NULL) {
        perror("popen get_pop_ip.sh");
        return -1;
    }

    if (fgets(buf, (int)buflen, fp) == NULL) {
        int status = pclose(fp);
        fprintf(stderr, "[WARN] get_pop_ip.sh produced no output, status=%d\n", status);
        return -1;
    }

    int status = pclose(fp);
    trim_whitespace(buf);

    if (status != 0) {
        fprintf(stderr, "[WARN] get_pop_ip.sh exited with status=%d, output='%s'\n", status, buf);
        return -1;
    }

    struct in_addr tmp;
    if (inet_pton(AF_INET, buf, &tmp) != 1) {
        fprintf(stderr, "[WARN] get_pop_ip.sh output is not a valid IPv4 address: '%s'\n", buf);
        return -1;
    }

    return 0;
}

static void stop_pop_ping_child(void) {
    if (pop_ping_pid > 0) {
        // Kill the whole process group created by the child.
        kill(-pop_ping_pid, SIGTERM);
        usleep(200 * 1000);
        kill(-pop_ping_pid, SIGKILL);
        waitpid(pop_ping_pid, NULL, 0);
        pop_ping_pid = -1;
    }
}

static int start_pop_ping_child(const char *pop_ip, const char *pop_log) {
    if (pop_ip == NULL || pop_log == NULL) {
        return 0;
    }

    pid_t pid = fork();
    if (pid < 0) {
        perror("fork pop ping");
        return -1;
    }

    if (pid == 0) {
        // Child: make a new process group so parent can kill shell + ping together.
        setpgid(0, 0);

        char cmd[4096];
        snprintf(cmd, sizeof(cmd),
                 "echo '[INFO] pop_ip=%s iface=%s interval=%s' >> '%s'; "
                 "while true; do "
                 "ts=$(date +%%s.%%N); "
                 "ping -I '%s' -c 1 -W 1 '%s' 2>&1 | "
                 "awk -v t=\"$ts\" '/time=/ {print t, $0; fflush()} /100%% packet loss/ {print t, \"timeout\"; fflush()}'; "
                 "sleep '%s'; "
                 "done >> '%s' 2>&1",
                 pop_ip, POP_PING_IFACE, POP_PING_INTERVAL_SEC, pop_log,
                 POP_PING_IFACE, pop_ip,
                 POP_PING_INTERVAL_SEC,
                 pop_log);

        execlp("bash", "bash", "-c", cmd, (char *)NULL);
        perror("execlp bash pop ping");
        _exit(127);
    }

    pop_ping_pid = pid;
    printf("[INFO] POP ping started: pid=%d pop_ip=%s log=%s iface=%s interval=%s\n",
           pid, pop_ip, pop_log, POP_PING_IFACE, POP_PING_INTERVAL_SEC);
    fflush(stdout);
    return 0;
}

static ssize_t read_all(int fd, void *buf, size_t len) {
    char *p = (char *)buf;
    size_t left = len;

    while (left > 0) {
        ssize_t n = recv(fd, p, left, 0);
        if (n < 0) {
            if (errno == EINTR) {
                if (stop_requested) return -1;
                continue;
            }
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
            if (errno == EINTR) {
                if (stop_requested) return -1;
                continue;
            }
            return -1;
        }
        if (n == 0) return -1;

        p += n;
        left -= n;
    }

    return len;
}

int main(int argc, char *argv[]) {
    if (argc != 4 && argc != 5) {
        fprintf(stderr,
                "Usage:\n"
                "  %s <server_ip> <port> <local_bind_ip> [pop_log]\n"
                "\n"
                "Example:\n"
                "  %s 165.194.35.203 40075 192.168.1.150 ./pop_ping.log\n",
                argv[0], argv[0]);
        return 1;
    }

    char run_dir[1024];
    char pop_log_path[1024];

    time_t now = time(NULL);
    struct tm *tm_info = localtime(&now);

    char ts[64];
    strftime(ts, sizeof(ts), "%Y%m%d_%H%M%S", tm_info);

    const char *suffix = (argc >= 5) ? argv[4] : "default";

    snprintf(run_dir, sizeof(run_dir),
            "%s/pop_ping_%s_%s",
            POP_OUTPUT_BASE,
            ts,
            suffix);

    mkdir(POP_OUTPUT_BASE, 0755);
    mkdir(run_dir, 0755);

    snprintf(pop_log_path, sizeof(pop_log_path),
            "%s/pop_ping.log",
            run_dir);

    const char *server_ip = argv[1];
    int port = atoi(argv[2]);
    const char *local_ip = argv[3];
    const char *pop_log = pop_log_path;

    signal(SIGINT, handle_signal);
    signal(SIGTERM, handle_signal);

    char pop_ip[128] = {0};
    int have_pop_ip = 0;

    if (get_pop_ip_from_script(server_ip, pop_ip, sizeof(pop_ip)) == 0) {
        have_pop_ip = 1;
        printf("[INFO] POP IP: %s\n", pop_ip);
    } else {
        fprintf(stderr, "[WARN] Failed to get POP IP. Continuing without POP ping.\n");
    }

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

    if (strcmp(local_ip, "any") == 0 || strcmp(local_ip, "0.0.0.0") == 0 || strcmp(local_ip, "-") == 0) {
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
    fflush(stdout);

    if (have_pop_ip) {
        if (start_pop_ping_child(pop_ip, pop_log) < 0) {
            fprintf(stderr, "[WARN] Failed to start POP ping child. Continuing TCP app-layer ping only.\n");
        }
    }

    int printed = 0;

    while (!stop_requested) {
        ping_pkt_t pkt;

        ssize_t n = read_all(sock, &pkt, sizeof(pkt));
        if (n < 0) {
            if (!stop_requested) {
                perror("recv");
            }
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
            if (!stop_requested) {
                perror("send");
            }
            break;
        }

        if (!printed) {
            printf("got ping seq=%lu\n", (unsigned long)pkt.seq);
            fflush(stdout);
            printed = 1;
        }
    }

    close(sock);
    stop_pop_ping_child();
    char plot_cmd[2048];

    snprintf(plot_cmd, sizeof(plot_cmd),
            "python3 '%s' '%s'",
            POP_PLOT_SCRIPT,
            run_dir);

    printf("[INFO] Running plot: %s\n", plot_cmd);

    int ret = system(plot_cmd);

    if (ret != 0) {
        fprintf(stderr, "[WARN] plot failed: ret=%d\n", ret);
    }

    printf("[INFO] receiver stopped\n");
    return 0;
}