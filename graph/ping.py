import re
import matplotlib.pyplot as plt

times = []
rtts = []

path="logs/20260330_150437_tcp_bbr_downlink_eth1/"

with open(path+"ping.log") as f:
    for line in f:
        match = re.search(r"\[(.*?)\].*time=(.*?) ms", line)
        if match:
            times.append(float(match.group(1)))
            rtts.append(float(match.group(2)))

# normalize time
t0 = times[0]
times = [t - t0 for t in times]

plt.plot(times, rtts)
plt.xlabel("Time (s)")
plt.ylabel("RTT (ms)")
plt.title("RTT over Time")
plt.grid()
plt.show()