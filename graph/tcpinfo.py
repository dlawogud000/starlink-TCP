import re
import matplotlib.pyplot as plt

times = []
cwnds = []
rtts = []

path = "starlink-TCP/logs/20260401_144832_tcp_bbr_uplink_test6/"
logfile = path + "ss_tcpinfo.log"

current_time = None
best_entry = None

def parse_metrics(line):
    cwnd = None
    rtt = None
    bytes_sent = -1

    m = re.search(r"cwnd:(\d+)", line)
    if m:
        cwnd = int(m.group(1))

    m = re.search(r"rtt:([0-9.]+)", line)
    if m:
        rtt = float(m.group(1))

    m = re.search(r"bytes_sent:(\d+)", line)
    if m:
        bytes_sent = int(m.group(1))

    return cwnd, rtt, bytes_sent


def flush_best():
    if best_entry:
        times.append(best_entry["time"])
        cwnds.append(best_entry["cwnd"])
        rtts.append(best_entry["rtt"])


with open(logfile) as f:
    for line in f:
        line = line.strip()

        # timestamp line
        if re.match(r"^\d+\.\d+$", line):
            flush_best()
            current_time = float(line)
            best_entry = None
            continue

        # metric line (contains rtt, cwnd, bytes_sent)
        if "cwnd:" in line and "rtt:" in line:
            cwnd, rtt, bytes_sent = parse_metrics(line)

            if cwnd is None or rtt is None:
                continue

            # choose data connection (max bytes_sent)
            if best_entry is None or bytes_sent > best_entry["bytes_sent"]:
                best_entry = {
                    "time": current_time,
                    "cwnd": cwnd,
                    "rtt": rtt,
                    "bytes_sent": bytes_sent,
                }

# 마지막 남은 것도 flush
flush_best()

if not times:
    raise ValueError("No valid data found")

# normalize time
t0 = times[0]
times = [t - t0 for t in times]

# plot cwnd
plt.figure()
plt.plot(times, cwnds, label="cwnd")

plt.xlabel("Time (s)")
plt.ylabel("cwnd")
plt.title("cwnd over Time")
plt.grid()

plt.savefig(path + "cwnd.png", dpi=150, bbox_inches="tight")
plt.show()