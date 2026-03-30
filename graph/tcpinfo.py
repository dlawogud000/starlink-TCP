import re
import matplotlib.pyplot as plt

times = []
cwnds = []
rtts = []

path="logs/20260330_150437_tcp_bbr_downlink_eth1/"

with open(path+"ss_tcpinfo.log") as f:
    for line in f:
        if line.startswith("1"):  # timestamp line
            current_time = float(line.strip())
        if "cwnd:" in line:
            cwnd = int(re.search(r"cwnd:(\d+)", line).group(1))
            rtt = float(re.search(r"rtt:(\d+\.\d+)", line).group(1))
            
            times.append(current_time)
            cwnds.append(cwnd)
            rtts.append(rtt)

t0 = times[0]
times = [t - t0 for t in times]

plt.plot(times, cwnds)
plt.xlabel("Time (s)")
plt.ylabel("cwnd")
plt.title("cwnd over Time")
plt.show()