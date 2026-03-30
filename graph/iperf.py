import json
import matplotlib.pyplot as plt

path="logs/20260330_150437_tcp_bbr_downlink_eth1/"

with open(path+"iperf.json") as f:
    data = json.load(f)

times = []
throughputs = []

for interval in data["intervals"]:
    t = interval["sum"]["end"]
    bw = interval["sum"]["bits_per_second"] / 1e6  # Mbps
    times.append(t)
    throughputs.append(bw)

plt.plot(times, throughputs)
plt.xlabel("Time (s)")
plt.ylabel("Throughput (Mbps)")
plt.title("Throughput over Time")
plt.grid()
plt.show()