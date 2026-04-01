import re
import matplotlib.pyplot as plt

times = []
rtts = []

path = "starlink-TCP/logs/20260401_144832_tcp_bbr_uplink_test6/"
logfile = path + "ss_rtt.log"

with open(logfile) as f:
    for line in f:
        line = line.strip()

        # no_connection 라인은 건너뜀
        if not line or "no_connection" in line:
            continue

        # 예: 1711951234.123456789 rtt_ms=42.7 rttvar_ms=3.1 ...
        match = re.search(r"^([0-9]+\.[0-9]+)\s+rtt_ms=([0-9.]+)", line)
        if match:
            times.append(float(match.group(1)))
            rtts.append(float(match.group(2)))

if not times:
    raise ValueError("ss_rtt.log에서 RTT 데이터를 찾지 못했습니다.")

# normalize time
t0 = times[0]
times = [t - t0 for t in times]

plt.figure()
plt.plot(times, rtts)
plt.xlabel("Time (s)")
plt.ylabel("RTT (ms)")
plt.title("RTT over Time")
plt.grid()

plt.savefig(path + "ss_rtt.png", dpi=150, bbox_inches="tight")
plt.show()